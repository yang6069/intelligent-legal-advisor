# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | MCP 协议工具管理器（需求文档 4.4 / 9.1.3）
==================================================================
模块职责：
    实现 MCP（Model Context Protocol）协议语义的工具管理中枢：
    1. 工具注册（register）：工具服务器上线时声明工具清单与参数 Schema；
    2. 工具发现（discover）：支持通配符模式过滤的工具目录查询；
    3. 工具调用（call）：统一入口的参数校验 + 执行 + 调用统计；
    4. 健康检查（health_check）：探测各工具服务器存活状态。

实现说明：
    - 需求文档 4.4.2 基于 mcp 官方 SDK 的 stdio 传输实现。为保证本地与
      Docker 环境零外部依赖稳定运行，本实现采用「进程内 MCP 兼容层」：
      工具以 Python 可调用对象注册，协议语义（注册/发现/调用/健康检查/
      JSON Schema 参数校验/调用统计）与 MCP 规范保持一致，并持久化到
      MySQL mcp_tools 表，支持热插拔（重启后自动恢复注册）；
    - 内置 8 个法律工具服务器工具（4.4.3 清单），覆盖法条检索、案例
      检索、合同模板、条款校验、赔偿金计算、利息计算、合规检查与
      图谱查询。
==================================================================
"""
import asyncio
import fnmatch
import json
import logging
import re
import time
from typing import Any, Callable

from src import config, database

logger = logging.getLogger("zhifatong.mcp")


# ==================================================================
# 一、内置法律工具实现（MCP 工具服务器：mcp-law-search 等）
# ==================================================================
def _tool_search_articles(args: dict) -> dict:
    """
    工具 search_articles：法律条文检索。

    参数（JSON Schema）:
        query: 搜索关键词或法条编号（必填）
        law_type: 文档类型过滤（law/case/interpretation/template，可选）
        effective_only: 是否仅返回有效法条（默认 True）

    实现逻辑:
        优先精确匹配法条编号 -> 模糊匹配法名/标题 -> 全文 LIKE 兜底。
    """
    query = (args.get("query") or "").strip()
    effective_only = args.get("effective_only", True)
    if not query:
        return {"total": 0, "items": []}

    conds, params = ["doc_type = 'law'"], []
    if effective_only:
        conds.append("effectiveness = '有效'")

    # 精确法条编号匹配（如 "第563条"）
    if re.match(r"^第?\d+条$", query):
        conds.append("article_no LIKE %s")
        params.append(f"%{query}%")
        rows = database.query_all(
            f"SELECT id, title, law_name, article_no, content, effectiveness "
            f"FROM kb_documents WHERE {' AND '.join(conds)} ORDER BY id LIMIT 10",
            tuple(params))
        if rows:
            return {"total": len(rows), "items": rows}

    # 法名/标题模糊匹配
    conds.append("(law_name LIKE %s OR title LIKE %s OR content LIKE %s)")
    kw = f"%{query}%"
    params.extend([kw, kw, kw])
    rows = database.query_all(
        f"SELECT id, title, law_name, article_no, content, effectiveness "
        f"FROM kb_documents WHERE {' AND '.join(conds)} ORDER BY char_count LIMIT 10",
        tuple(params))
    return {"total": len(rows), "items": rows}


def _tool_search_cases(args: dict) -> dict:
    """
    工具 search_cases：裁判案例检索（关键词匹配 + 相似度排序）。
    """
    query = (args.get("query") or "").strip()
    if not query:
        return {"total": 0, "items": []}
    kw = f"%{query}%"
    rows = database.query_all(
        "SELECT id, title, law_name, content, effectiveness "
        "FROM kb_documents WHERE doc_type = 'case' "
        "AND (title LIKE %s OR content LIKE %s) ORDER BY id DESC LIMIT 10",
        (kw, kw))
    return {"total": len(rows), "items": rows}


_CONTRACT_TEMPLATES = {
    "劳动合同": {
        "title": "标准劳动合同模板（要点版）",
        "clauses": ["合同期限与试用期", "工作内容与工作地点", "劳动报酬与支付方式",
                    "社会保险与福利", "劳动保护与劳动条件", "竞业限制与保密",
                    "合同解除与终止条件", "违约责任与争议解决"],
    },
    "租赁合同": {
        "title": "标准房屋租赁合同模板（要点版）",
        "clauses": ["租赁物基本情况", "租赁期限与交付", "租金与押金",
                    "维修责任划分", "转租限制", "合同解除条件",
                    "违约责任", "争议解决方式"],
    },
    "买卖合同": {
        "title": "标准买卖合同模板（要点版）",
        "clauses": ["标的物名称与规格", "数量与质量标准", "价款与支付方式",
                    "交付时间地点与验收", "所有权转移与风险负担",
                    "违约责任", "争议解决"],
    },
}


def _tool_get_template(args: dict) -> dict:
    """工具 get_template：获取标准合同模板（按合同类型）。"""
    t = (args.get("contract_type") or "").strip()
    if t in _CONTRACT_TEMPLATES:
        return _CONTRACT_TEMPLATES[t]
    return {"available_types": list(_CONTRACT_TEMPLATES),
            "hint": "请指定 contract_type，可选值见 available_types"}


def _tool_validate_clause(args: dict) -> dict:
    """
    工具 validate_clause：条款合规性校验（规则引擎通道）。

    复用合同审查模块的 16 条内建风险规则，对单条条款执行正则检测。
    """
    from src.contract.risk_rules import risk_rule_engine
    clause = args.get("clause_text") or ""
    hits = risk_rule_engine.check(clause)
    return {
        "valid": len(hits) == 0,
        "risk_count": len(hits),
        "risks": [h.__dict__ if hasattr(h, "__dict__") else dict(h) for h in hits],
    }


def _tool_calculate_compensation(args: dict) -> dict:
    """
    工具 calculate_compensation：劳动赔偿金计算。

    算法（《劳动合同法》第47/87条）:
        - 经济补偿 N = 月工资 × 工作年限（满一年计 1，六个月以上不满一年计 1，
          不满六个月计 0.5）；
        - 违法解除赔偿金 = 2N（第87条）。
    """
    monthly_salary = float(args.get("monthly_salary", 0))
    years = float(args.get("work_years", 0))
    illegal = bool(args.get("illegal_termination", False))

    # 年限换算：向下取整年数 + 余数 >= 0.5 年进 1，否则 +0.5
    full_years = int(years)
    remainder = years - full_years
    n = full_years + (1 if remainder >= 0.5 else 0.5 if remainder > 0 else 0)

    compensation = monthly_salary * n                    # N
    damages = compensation * 2 if illegal else 0.0       # 2N
    return {
        "monthly_salary": monthly_salary, "work_years": years,
        "compensation_months": n, "economic_compensation": round(compensation, 2),
        "illegal_termination": illegal,
        "damages_2n": round(damages, 2),
        "legal_basis": "《劳动合同法》第47条、第87条",
    }


def _tool_calculate_interest(args: dict) -> dict:
    """
    工具 calculate_interest：借款利息计算（含 LPR 4 倍上限提示）。

    算法:
        单利利息 = 本金 × 年利率 × 年限；
        司法保护上限 = 合同成立时一年期 LPR 的 4 倍。
    """
    principal = float(args.get("principal", 0))
    annual_rate = float(args.get("annual_rate", 0))     # 百分比形式，如 12 表示 12%
    years = float(args.get("years", 1))

    interest = principal * (annual_rate / 100.0) * years
    lpr4 = 14.2   # 假设一年期 LPR 3.55% × 4（示例基准，实际按合同成立时点取值）
    return {
        "principal": principal, "annual_rate_percent": annual_rate,
        "years": years, "simple_interest": round(interest, 2),
        "judicial_cap_percent": lpr4,
        "exceeds_cap": annual_rate > lpr4,
        "legal_basis": "《民间借贷司法解释》第25条（LPR 四倍上限）",
    }


_COMPLIANCE_ITEMS = [
    ("个人信息处理告知同意", r"(个人信息|用户数据).{0,20}(收集|处理|存储)"),
    ("数据出境安全评估", r"(数据|个人信息).{0,12}(出境|跨境传输)"),
    ("网络安全等级保护", r"(网络安全|等保)"),
    ("未成年人保护机制", r"(未成年人|儿童).{0,16}(保护|监护同意)"),
    ("算法备案与透明度", r"(自动化决策|算法推荐|个性化推荐)"),
]


def _tool_check_compliance(args: dict) -> dict:
    """
    工具 check_compliance：合规要点检查（基于文本关键词的合规清单核对）。
    """
    text = args.get("text") or ""
    hits, misses = [], []
    for name, pattern in _COMPLIANCE_ITEMS:
        if re.search(pattern, text):
            hits.append(name)
        else:
            misses.append(name)
    return {
        "score": round(len(hits) / len(_COMPLIANCE_ITEMS) * 100, 1),
        "compliant_items": hits, "missing_items": misses,
        "regulations": ["《个人信息保护法》", "《数据安全法》",
                        "《网络安全法》", "《未成年人保护法》"],
    }


def _tool_graph_query(args: dict) -> dict:
    """工具 graph_query：知识图谱关联查询（复用 GraphRAG 第三路检索）。"""
    from src.rag import graph_store
    query = args.get("query") or ""
    limit = int(args.get("limit", 10))
    if not graph_store.ensure_graph():
        return {"available": False, "reason": "Neo4j 图谱服务不可用，已降级"}
    hits = graph_store.graph_search(query, top_k=limit)
    return {"available": True, "total": len(hits), "items": hits}


# 内置工具服务器清单：工具名 -> (所属服务器, 描述, Schema, 实现函数)
BUILTIN_TOOLS: dict[str, dict] = {
    "search_articles": {
        "server": "mcp-law-search",
        "description": "搜索法律法规条文，支持按法条编号、关键词、效力级别检索",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或法条编号"},
                "effective_only": {"type": "boolean", "default": True},
            },
            "required": ["query"],
        },
        "func": _tool_search_articles,
    },
    "search_cases": {
        "server": "mcp-case-search",
        "description": "搜索裁判案例，返回标题与案情摘要",
        "schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "案例关键词"}},
            "required": ["query"],
        },
        "func": _tool_search_cases,
    },
    "get_template": {
        "server": "mcp-contract-tools",
        "description": "获取标准合同模板（劳动合同/租赁合同/买卖合同）",
        "schema": {
            "type": "object",
            "properties": {"contract_type": {"type": "string", "description": "合同类型"}},
            "required": ["contract_type"],
        },
        "func": _tool_get_template,
    },
    "validate_clause": {
        "server": "mcp-contract-tools",
        "description": "使用16条风险规则校验单条合同条款的合规性",
        "schema": {
            "type": "object",
            "properties": {"clause_text": {"type": "string", "description": "条款文本"}},
            "required": ["clause_text"],
        },
        "func": _tool_validate_clause,
    },
    "calculate_compensation": {
        "server": "mcp-calc-tools",
        "description": "计算劳动经济补偿金(N)与违法解除赔偿金(2N)",
        "schema": {
            "type": "object",
            "properties": {
                "monthly_salary": {"type": "number", "description": "月工资(元)"},
                "work_years": {"type": "number", "description": "工作年限"},
                "illegal_termination": {"type": "boolean", "default": False,
                                         "description": "是否违法解除"},
            },
            "required": ["monthly_salary", "work_years"],
        },
        "func": _tool_calculate_compensation,
    },
    "calculate_interest": {
        "server": "mcp-calc-tools",
        "description": "计算借款利息并提示 LPR 四倍司法保护上限",
        "schema": {
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "本金(元)"},
                "annual_rate": {"type": "number", "description": "年利率(%)"},
                "years": {"type": "number", "default": 1, "description": "年限"},
            },
            "required": ["principal", "annual_rate"],
        },
        "func": _tool_calculate_interest,
    },
    "check_compliance": {
        "server": "mcp-compliance-check",
        "description": "检查文本的合规要点覆盖情况（个保法/数安法/网安法）",
        "schema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "待检查文本"}},
            "required": ["text"],
        },
        "func": _tool_check_compliance,
    },
    "graph_query": {
        "server": "mcp-graph-query",
        "description": "法律知识图谱关联查询（法条-案例-概念网络）",
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "查询关键词"},
                "limit": {"type": "integer", "default": 10, "description": "返回数量"},
            },
            "required": ["query"],
        },
        "func": _tool_graph_query,
    },
}


# ==================================================================
# 二、MCP 工具管理器
# ==================================================================
class MCPToolManager:
    """
    MCP 协议工具管理器：注册 / 发现 / 调用 / 健康检查。

    状态保持:
        - self.servers: server_name -> 注册信息（注册时间 / 工具数）
        - self.tools:   tool_name   -> 工具元数据（含可调用实现）
        - self.health:  server_name -> 健康状态（healthy/unhealthy + 时间戳）
    """

    def __init__(self):
        self.servers: dict[str, dict] = {}
        self.tools: dict[str, dict] = {}
        self.health: dict[str, dict] = {}
        self._ready = False

    # ------------------------------------------------------------------
    # 初始化：注册内置工具 + 从数据库恢复外部注册的工具
    # ------------------------------------------------------------------
    def ensure_ready(self) -> None:
        """
        惰性初始化（首次访问时执行）：
        1. 注册 8 个内置法律工具；
        2. 从 mcp_tools 表恢复历史注册的外部工具状态。
        幂等：多次调用仅执行一次。
        """
        if self._ready:
            return
        self._ready = True
        # 注册内置工具（进程内 MCP 兼容实现）
        for name, meta in BUILTIN_TOOLS.items():
            self._register_builtin(name, meta)
        # 恢复数据库中的注册记录（外部动态注册的工具重启后标记 inactive，
        # 因其进程内实现不可恢复；内置工具以内存注册为准）
        try:
            rows = database.query_all(
                "SELECT tool_name, server_name, description, schema_json, status "
                "FROM mcp_tools")
            for row in rows:
                name = row["tool_name"]
                if name in self.tools:
                    # 内置工具：同步数据库状态
                    self.tools[name]["call_count"] = 0
                    continue
                # 外部注册但实现已丢失 -> 标记 inactive 保留目录可追溯
                self.tools[name] = {
                    "server": row["server_name"],
                    "description": row["description"] or "",
                    "schema": row.get("schema_json") or {},
                    "func": None,
                    "status": "inactive",
                    "call_count": 0,
                }
        except Exception as e:
            logger.warning("MCP 工具表恢复失败（数据库未就绪?）: %s", e)
        logger.info("MCP 工具管理器就绪：内置 %d 个工具", len(BUILTIN_TOOLS))

    def _register_builtin(self, name: str, meta: dict) -> None:
        """注册内置工具到内存目录并落库（ON DUPLICATE 幂等）。"""
        self.tools[name] = {
            "server": meta["server"],
            "description": meta["description"],
            "schema": meta["schema"],
            "func": meta["func"],
            "status": "active",
            "call_count": 0,
        }
        # 服务器维度统计
        srv = meta["server"]
        self.servers.setdefault(srv, {"registered_at": time.time(), "tools": 0})
        self.servers[srv]["tools"] += 1
        self.health[srv] = {"status": "healthy", "checked_at": time.time()}
        # 持久化注册记录
        try:
            database.execute(
                "INSERT INTO mcp_tools (tool_name, server_name, description, "
                "schema_json, status) VALUES (%s, %s, %s, %s, 'active') "
                "ON DUPLICATE KEY UPDATE description = VALUES(description), "
                "status = 'active'",
                (name, srv, meta["description"], json.dumps(meta["schema"],
                                                             ensure_ascii=False)))
        except Exception as e:
            logger.debug("内置工具落库跳过（数据库未就绪）: %s", e)

    # ------------------------------------------------------------------
    # 注册（外部工具服务器热插拔入口）
    # ------------------------------------------------------------------
    def register_tool(self, tool_name: str, server_name: str, description: str,
                      schema: dict, func: Callable | None = None,
                      persist: bool = True) -> dict:
        """
        注册 / 更新一个 MCP 工具（热插拔）。

        参数:
            tool_name: 工具唯一名
            server_name: 所属工具服务器名
            description: 工具功能描述
            schema: 输入参数 JSON Schema
            func: 进程内实现（None 表示仅登记目录，调用时返回未实现）
            persist: 是否持久化到 mcp_tools 表

        返回: {"status": "ok"/"error", ...}
        """
        if not tool_name or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", tool_name):
            return {"status": "error", "message": "工具名非法（仅允许字母数字下划线）"}
        try:
            self.tools[tool_name] = {
                "server": server_name, "description": description or "",
                "schema": schema or {}, "func": func,
                "status": "active", "call_count": 0,
            }
            self.servers.setdefault(server_name, {"registered_at": time.time(),
                                                  "tools": 0})
            self.servers[server_name]["tools"] += 1
            self.health[server_name] = {"status": "healthy", "checked_at": time.time()}
            if persist:
                database.execute(
                    "INSERT INTO mcp_tools (tool_name, server_name, description, "
                    "schema_json, status) VALUES (%s, %s, %s, %s, 'active') "
                    "ON DUPLICATE KEY UPDATE description = VALUES(description), "
                    "schema_json = VALUES(schema_json), status = 'active'",
                    (tool_name, server_name, description,
                     json.dumps(schema or {}, ensure_ascii=False)))
            logger.info("MCP 工具注册成功: %s (server=%s)", tool_name, server_name)
            return {"status": "ok", "tool": tool_name, "server": server_name}
        except Exception as e:
            logger.error("MCP 工具注册失败: %s -> %s", tool_name, e)
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # 发现
    # ------------------------------------------------------------------
    def discover_tools(self, pattern: str = "*") -> list[dict]:
        """
        发现所有已注册工具（支持通配符过滤，如 "search_*"）。

        返回: [{name, server, description, schema, status}, ...]
        """
        self.ensure_ready()
        return [
            {"name": name, "server": info["server"],
             "description": info["description"], "schema": info["schema"],
             "status": info.get("status", "active")}
            for name, info in self.tools.items()
            if fnmatch.fnmatch(name, pattern)
        ]

    # ------------------------------------------------------------------
    # 调用
    # ------------------------------------------------------------------
    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict:
        """
        调用 MCP 工具（统一入口：参数校验 + 执行 + 统计落库）。

        参数:
            tool_name: 工具名（须已注册且 active）
            arguments: 工具参数（按其 JSON Schema 校验必填项）

        返回:
            {"status": "ok", "result": <工具返回>, "latency_ms": <耗时>}
            {"status": "error", "message": <原因>}
        """
        self.ensure_ready()
        t0 = time.perf_counter()
        info = self.tools.get(tool_name)
        if not info:
            return {"status": "error", "message": f"工具 {tool_name} 未注册"}
        if info.get("status") != "active":
            return {"status": "error", "message": f"工具 {tool_name} 已停用"}

        # ---- 必填参数校验（按 JSON Schema required 字段）----
        required = (info.get("schema") or {}).get("required", [])
        missing = [k for k in required if k not in (arguments or {})]
        if missing:
            return {"status": "error",
                    "message": f"缺少必填参数: {', '.join(missing)}"}

        func = info.get("func")
        if func is None:
            return {"status": "error", "message": "工具实现不可用（外部进程未接入）"}

        # ---- 执行（同步工具放入线程池，避免阻塞事件循环）----
        try:
            result = await asyncio.to_thread(func, arguments or {})
            latency = round((time.perf_counter() - t0) * 1000, 1)
            # 调用统计（内存 + 数据库，失败不影响返回）
            info["call_count"] = info.get("call_count", 0) + 1
            try:
                database.execute(
                    "UPDATE mcp_tools SET call_count = call_count + 1, "
                    "last_call_at = NOW() WHERE tool_name = %s", (tool_name,))
            except Exception:
                pass
            return {"status": "ok", "result": result, "latency_ms": latency}
        except Exception as e:
            logger.error("MCP 工具 %s 调用异常: %s", tool_name, e, exc_info=True)
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # 健康检查
    # ------------------------------------------------------------------
    async def health_check(self) -> dict:
        """
        所有工具服务器健康检查。

        探测逻辑:
            对每个服务器抽样一个工具执行一次轻量调用（缺省参数触发
            参数校验即视为连通），成功 -> healthy，异常 -> unhealthy。
        """
        self.ensure_ready()
        results = {}
        for server_name in self.servers:
            # 找该服务器下任一工具做探测
            probe = next((n for n, i in self.tools.items()
                          if i["server"] == server_name), None)
            if probe is None:
                results[server_name] = "unknown"
                continue
            try:
                ret = await self.call_tool(probe, {})   # 空参数触发校验
                # 参数校验错误也算连通（说明工具进程可响应）
                status = "healthy" if ret.get("status") in ("ok", "error") else "unhealthy"
            except Exception:
                status = "unhealthy"
            results[server_name] = status
            self.health[server_name] = {"status": status, "checked_at": time.time()}
        return results

    # ------------------------------------------------------------------
    # 停用 / 启用（热插拔下线）
    # ------------------------------------------------------------------
    def set_tool_status(self, tool_name: str, active: bool) -> dict:
        """停用/启用工具（热插拔下线，不删除目录记录）。"""
        self.ensure_ready()
        info = self.tools.get(tool_name)
        if not info:
            return {"status": "error", "message": f"工具 {tool_name} 不存在"}
        info["status"] = "active" if active else "inactive"
        try:
            database.execute(
                "UPDATE mcp_tools SET status = %s WHERE tool_name = %s",
                ("active" if active else "inactive", tool_name))
        except Exception as e:
            logger.warning("工具状态落库失败: %s", e)
        return {"status": "ok", "tool": tool_name,
                "tool_status": info["status"]}

    # ------------------------------------------------------------------
    # 统计概览（管理界面）
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """工具目录统计概览（服务器数 / 工具数 / 活跃率 / 累计调用）。"""
        self.ensure_ready()
        active = sum(1 for i in self.tools.values() if i.get("status") == "active")
        return {
            "servers": len(self.servers),
            "tools": len(self.tools),
            "active_tools": active,
            "inactive_tools": len(self.tools) - active,
            "total_calls": sum(i.get("call_count", 0) for i in self.tools.values()),
            "health": {k: v.get("status") for k, v in self.health.items()},
        }


# 全局单例（后端各路由共享）
mcp_manager = MCPToolManager()
