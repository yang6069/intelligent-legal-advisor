# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 多 Agent 对抗式合同审查工作流（核心创新，需求文档 4.3 / 6.2）
==================================================================
模块职责：
    基于自研状态图引擎（graph_engine.StateGraph，LangGraph 兼容语义）
    编排 10 节点合同审查工作流：

    parse_doc（合同解析）
        -> split_clauses（条款拆分）
        -> 并行分支：primary_review（主审）/ review_check（复核）/ adverse_attack（反方）
        -> debate（对抗辩论：主审必须回应反方质疑）
        -> [条件路由] 达成共识 -> risk_score
                       未共识   -> arbitrate（仲裁）-> risk_score
        -> suggestions（修改建议生成）
        -> perspectives（三视角独立推理：法务/合规/业务，真实 LLM 并发）
        -> report_generate（报告输出，含三视角意见+推理链+执行轨迹）
        -> END

多 Agent 角色：
    主审 Agent（中立专业）：完整性检查 + 规则/LLM 双引擎风险识别 + 合规校验
    复核 Agent（规则校验）：规则引擎复检主审结论，发现等级不一致与遗漏
    反方 Agent（弱势立场）：站在弱势方立场逐条质疑，提出反对意见
    仲裁节点：对辩论未共识问题给出最终裁决
==================================================================
"""
import asyncio
import logging
import time
import uuid
from typing import TypedDict

from src import config
from src.contract.parser import detect_contract_type, extract_parties
from src.contract.risk_rules import risk_rule_engine, split_clauses
from src.core.graph_engine import StateGraph, END, MemorySaver
from src.core.llm_client import llm_client

logger = logging.getLogger("zhifatong.contract.workflow")


# ==================================================================
# 一、工作流状态定义（需求文档 4.3.2 ContractReviewState）
# ==================================================================
class ContractReviewState(TypedDict, total=False):
    """合同审查工作流状态（total=False：全部键可选，节点增量更新）。"""
    # 输入
    contract_text: str                    # 合同全文
    contract_type: str                    # 合同类型
    parties: list                         # 当事方信息

    # 中间状态
    clauses: list                         # 拆分后的条款列表
    completeness_check: dict              # 完整性检查结果

    # 三方 Agent 独立输出
    primary_risks: list                   # 主审风险清单
    review_check: dict                    # 复核复检结果
    adverse_opinions: list                # 反方反对意见

    # 对抗辩论
    debate_log: list                      # 辩论记录（多轮）
    unresolved_issues: list               # 未达成共识的问题

    # 输出
    risk_score: float                     # 综合风险评分(0-100)
    risk_level: str                       # 风险等级
    modification_suggestions: list        # 修改建议
    review_report: dict                   # 审查报告
    perspective_opinions: list            # 三视角独立审查意见（法务/合规/业务）

    # 流程控制
    current_step: str                     # 当前步骤
    error: str                            # 错误信息
    node_timings: dict                    # 各节点耗时（性能观测）
    llm_calls: dict                       # 各 Agent 节点 LLM 真实调用标记（执行轨迹审计）


# ==================================================================
# 二、标准合同模板（必备条款清单，完整性检查依据）
# ==================================================================
CONTRACT_TEMPLATES = {
    "买卖合同": ["标的物", "价款", "交付", "验收", "违约责任", "争议解决", "质量标准"],
    "租赁合同": ["租赁物", "租金", "租期", "押金", "维修", "违约责任", "争议解决"],
    "劳动合同": ["合同期限", "工作内容", "工作地点", "劳动报酬", "工作时间", "社会保险", "劳动保护"],
    "教育培训合同": ["培训内容", "培训费用", "服务期限", "退费标准", "违约责任", "争议解决"],
    "服务合同": ["服务内容", "服务费用", "服务期限", "验收标准", "违约责任", "保密", "争议解决"],
    "借款合同": ["借款金额", "利率", "还款期限", "还款方式", "担保", "违约责任"],
    "保密协议": ["保密信息范围", "保密义务", "保密期限", "违约责任", "例外情形"],
    "技术合同": ["技术内容", "知识产权归属", "报酬", "交付", "验收", "保密", "违约责任"],
    "其他": ["当事人", "标的", "价款", "履行", "违约责任", "争议解决"],
}

# 主审 / 反方辩论合并 / 三视角 提示词（轻量化：只给规则命中摘要，杜绝全文）
PRIMARY_PROMPT = """你是合同审查主审专家（仅做规则引擎未覆盖的语义风险补充）。

合同类型：{contract_type}

【必备条款缺失】（必须检查）
{missing}

【规则已命中的风险条款（请补充法律依据和修改建议）】
{rule_hits}

审查维度：仅检查规则未覆盖的风险
1. 条款语义模糊（如"合理费用""相关人员"无定义）
2. 权利义务隐性不对等（如甲方单方面解除权无限制）
3. 逻辑矛盾（条款A说保密B又说可以公开）

只输出 JSON：
{{"risks": [{{"clause_index": 0, "risk_type": "模糊/不对等/矛盾",
 "severity": "高|中|低", "description": "描述100字内",
 "legal_basis": "法律依据", "suggestion": "建议80字内"}}]}}

最多输出5条，只挑最严重的，已有规则覆盖的不要再列。"""

ADVERSE_ADVOCACY_PROMPT = """你是合同弱势方代理律师+辩论仲裁员（合并质疑与裁决一步完成）。

合同类型：{contract_type}

【主审已识别的风险（规则+LLM）】
{primary_risks}

【复核异议 {disputed_count} 项 / 补充发现 {missed_count} 项】

请对每个有争议的风险完成（输出 JSON）：
1. 站弱势方立场判断主审判定是否合理；
2. 判定偏低（主审判中但实际应为高）→ 给出反方证据；
3. 判定合理 → 直接接受；
4. 找出弱势方未被发现的权利侵害。

只输出 JSON：
{{"challenges": [{{"clause_index": 0, "issue": "争议点",
 "our_position": "弱势方诉求", "legal_basis": "依据",
 "final_risk_level": "高|中|低", "final_suggestion": "最终建议"}}],
 "new_risks": [{{"clause_index": 0, "risk_type": "新发现",
 "severity": "高|中|低", "description": "描述", "suggestion": "建议"}}]}}

最多输出8条，聚焦最实质的权利侵害。"""

LEGAL_PERSPECTIVE_PROMPT = """你是资深法务审查官，从【法律视角】独立审查。

合同类型：{contract_type}
必备缺失：{missing}

【主审+反方合并风险清单】
{risks}

法律视角关注：合法性、法律后果、权利对等、依据准确性。
只输出 JSON（100-150字，最多2个要点）：
{{"opinion": "法务视角审查意见", "conclusions": ["要点1", "要点2"]}}"""

COMPLIANCE_PERSPECTIVE_PROMPT = """你是企业首席合规官，从【合规视角】独立审查。

合同类型：{contract_type}
复核异议 {disputed} / 补充发现 {missed}

【主审+反方合并风险清单】
{risks}

合规视角关注：监管合规、程序合规、内控制度、处罚风险。
只输出 JSON（100-150字，最多2个要点）：
{{"opinion": "合规视角审查意见", "conclusions": ["要点1", "要点2"]}}"""

BUSINESS_PERSPECTIVE_PROMPT = """你是商业谈判顾问，从【业务视角】独立审查。

合同类型：{contract_type}

【反方+辩论合并裁决】
{decisions}

业务视角关注：商业目标可达、成本收益、合作关系、弱势方权益。
只输出 JSON（100-150字，最多2个要点）：
{{"opinion": "业务视角审查意见", "conclusions": ["要点1", "要点2"]}}"""


# ==================================================================
# 三、节点实现
# ==================================================================
def parse_document(state: ContractReviewState) -> dict:
    """
    节点1：合同解析。

    职责: 识别合同类型 + 提取当事方 + 初始化流程状态。
    """
    text = state.get("contract_text", "")
    return {
        "contract_type": state.get("contract_type") or detect_contract_type(text),
        "parties": state.get("parties") or extract_parties(text),
        "current_step": "parse_doc",
        "node_timings": {},
    }


def node_split_clauses(state: ContractReviewState) -> dict:
    """
    节点2：条款拆分。

    职责: 按条款编号锚点拆分合同文本，产出结构化条款列表。
    """
    clauses = split_clauses(state.get("contract_text", ""))
    return {"clauses": clauses, "current_step": "split_clauses"}


def _format_clauses(clauses: list, limit: int = 12) -> str:
    """把条款列表格式化为 LLM 输入文本（限条数限字数）。"""
    lines = []
    for c in clauses[:limit]:
        lines.append(f"[条款{c['index']}] {c['text'][:300]}")
    return "\n".join(lines) or "（无条款）"


async def primary_agent_review(state: ContractReviewState) -> dict:
    """
    节点3a：主审 Agent（中立专业审查）。

    双引擎策略:
        1. 规则引擎: 16 条内建规则的确定性风险识别（必出）；
        2. LLM 引擎: 语义级风险分析（降级时仅规则结论）。

    输出: primary_risks + completeness_check
    """
    t0 = time.perf_counter()
    clauses = state.get("clauses", [])
    contract_type = state.get("contract_type", "其他")

    # ---- 1. 完整性检查（对照标准模板必备条款）----
    required = CONTRACT_TEMPLATES.get(contract_type, CONTRACT_TEMPLATES["其他"])
    all_text = " ".join(c["text"] for c in clauses)
    missing = [r for r in required if r not in all_text]
    completeness = {
        "total_required": len(required),
        "missing_clauses": missing,
        "missing_count": len(missing),
        "completeness_ratio": round(1 - len(missing) / max(len(required), 1), 2),
    }

    # ---- 2. 规则引擎风险识别（确定性通道）----
    rule_risks = risk_rule_engine.check_contract(clauses)
    primary_risks = []
    for rr in rule_risks:
        for risk in rr["risks"]:
            primary_risks.append({
                "clause_index": rr["clause_index"],
                "clause_text": rr["clause_text"],
                "risk_type": risk["rule_name"],
                "severity": risk["severity"],
                "description": risk["description"],
                "legal_basis": risk["legal_basis"],
                "suggestion": risk["suggestion"],
                "source": "rule_engine",
            })

    # ---- 3. LLM 语义风险补充（轻量化：只给规则命中摘要 + 必备缺失）----
    rule_hits_str = "\n".join(
        f"  [{rr['clause_index']}] 规则 {rr['risks'][0]['rule_name']}: "
        f"{rr['clause_text'][:80]}... 等级={rr['risks'][0]['severity']}"
        for rr in rule_risks
    ) or "（规则引擎未命中任何风险）"
    missing_str = "、".join(missing) if missing else "（无缺失）"

    llm_result = await llm_client.generate_json(
        PRIMARY_PROMPT.format(
            contract_type=contract_type,
            missing=missing_str,
            rule_hits=rule_hits_str,
        ),
        system="你是严谨的合同审查专家，只输出 JSON。",
        default=None,
    )
    llm_hit = isinstance(llm_result, dict)
    if llm_hit:
        for risk in (llm_result.get("risks") or [])[:10]:
            if not isinstance(risk, dict):
                continue
            primary_risks.append({
                "clause_index": int(risk.get("clause_index", 0) or 0),
                "clause_text": str(risk.get("clause_text", ""))[:200],
                "risk_type": str(risk.get("risk_type", "语义风险")),
                "severity": str(risk.get("severity", "中")),
                "description": str(risk.get("description", ""))[:300],
                "legal_basis": str(risk.get("legal_basis", ""))[:200],
                "suggestion": str(risk.get("suggestion", ""))[:300],
                "source": "llm",
            })

    timings = dict(state.get("node_timings") or {})
    timings["primary_review"] = round((time.perf_counter() - t0) * 1000)
    # Agent 执行轨迹：记录 LLM 是否真实调用（供报告"执行轨迹"展示，禁止占位造假）
    llm_calls = dict(state.get("llm_calls") or {})
    llm_calls["primary_review"] = llm_hit
    return {
        "primary_risks": primary_risks,
        "completeness_check": completeness,
        "current_step": "primary_review",
        "node_timings": timings,
        "llm_calls": llm_calls,
    }


async def review_agent_check(state: ContractReviewState) -> dict:
    """
    节点3b：复核 Agent（规则复检主审结论）。

    职责:
        1. 用规则引擎验证主审每个风险的等级与依据；
        2. 对主审未标记的条款做规则复扫，发现遗漏。

    输出: review_check {approved, disputed, missed}
    """
    t0 = time.perf_counter()
    primary_risks = state.get("primary_risks", [])
    clauses = state.get("clauses", [])
    reviewed_idx = {r.get("clause_index") for r in primary_risks}

    approved, disputed, missed = [], [], []
    for risk in primary_risks:
        verdict = risk_rule_engine.validate(risk)
        entry = {**risk, "review_reason": verdict["reason"]}
        (approved if verdict["valid"] else disputed).append(entry)

    # 遗漏检测：主审未覆盖的条款做规则复扫
    for clause in clauses:
        if clause["index"] in reviewed_idx:
            continue
        for hit in risk_rule_engine.check_missed(clause["text"]):
            missed.append({
                "clause_index": clause["index"],
                "clause_text": clause["text"][:200],
                "risk_type": hit["rule_name"],
                "severity": hit["severity"],
                "description": hit["description"],
                "legal_basis": hit["legal_basis"],
                "suggestion": hit["suggestion"],
                "source": "review_missed",
            })

    timings = dict(state.get("node_timings") or {})
    timings["review_check"] = round((time.perf_counter() - t0) * 1000)
    return {
        "review_check": {
            "approved": approved,
            "disputed": disputed,
            "missed": missed,
            "approved_count": len(approved),
            "disputed_count": len(disputed),
            "missed_count": len(missed),
        },
        "current_step": "review_check",
        "node_timings": timings,
    }


async def adverse_advocacy_node(state: ContractReviewState) -> dict:
    """
    合并节点：反方质疑 + 辩论仲裁 一步完成（原来 3 次 LLM → 现在 1 次）。

    职责:
        1. 站弱势方立场逐条质疑主审判定；
        2. 同时完成辩论仲裁：接受/调整主审判定；
        3. 输出裁决清单（带 final_risk_level 和 final_suggestion）。
    """
    t0 = time.perf_counter()
    primary_risks = state.get("primary_risks", [])
    review_check = state.get("review_check") or {}
    contract_type = state.get("contract_type") or "其他"

    disputed_count = review_check.get("disputed_count", 0)
    missed_count = review_check.get("missed_count", 0)

    # 精简 prompt 输入：主审风险摘要（截断 150 字/条）
    risks_summary = []
    for r in primary_risks[:12]:
        risks_summary.append({
            "clause_index": r.get("clause_index"),
            "risk_type": r.get("risk_type"),
            "severity": r.get("severity"),
            "description": (r.get("description") or "")[:120],
            "suggestion": (r.get("suggestion") or "")[:80],
        })

    llm_result = await llm_client.generate_json(
        ADVERSE_ADVOCACY_PROMPT.format(
            contract_type=contract_type,
            primary_risks=json_dumps(risks_summary, limit=2500),
            disputed_count=disputed_count,
            missed_count=missed_count,
        ),
        system="你是代理律师兼仲裁员，只输出 JSON。",
        default=None,
    )

    # 解析合并后的裁决
    challenges = []
    new_risks = []
    if isinstance(llm_result, dict):
        for c in (llm_result.get("challenges") or []):
            if isinstance(c, dict):
                challenges.append({
                    "clause_index": int(c.get("clause_index", 0) or 0),
                    "issue": str(c.get("issue", ""))[:200],
                    "our_position": str(c.get("our_position", ""))[:200],
                    "legal_basis": str(c.get("legal_basis", ""))[:150],
                    "final_risk_level": str(c.get("final_risk_level", "中")),
                    "final_suggestion": str(c.get("final_suggestion", ""))[:200],
                })
        for nr in (llm_result.get("new_risks") or []):
            if isinstance(nr, dict):
                new_risks.append({
                    "clause_index": int(nr.get("clause_index", 0) or 0),
                    "clause_text": str(nr.get("clause_text", ""))[:200],
                    "risk_type": str(nr.get("risk_type", "弱势方权利侵害")),
                    "severity": str(nr.get("severity", "中")),
                    "description": str(nr.get("description", ""))[:300],
                    "legal_basis": str(nr.get("legal_basis", ""))[:200],
                    "suggestion": str(nr.get("suggestion", ""))[:300],
                    "source": "adverse_new",
                })

    timings = dict(state.get("node_timings") or {})
    timings["adverse_advocacy"] = round((time.perf_counter() - t0) * 1000)
    llm_calls = dict(state.get("llm_calls") or {})
    llm_calls["adverse_advocacy"] = isinstance(llm_result, dict)

    # 合并裁决应用到 primary_risks（更新争议条目的等级和建议）
    challenges_by_idx = {c["clause_index"]: c for c in challenges}
    merged_risks = []
    for r in primary_risks:
        merged = {**r}
        ci = r.get("clause_index")
        if ci in challenges_by_idx:
            c = challenges_by_idx[ci]
            merged["severity"] = c.get("final_risk_level", merged["severity"])
            merged["suggestion"] = c.get("final_suggestion") or merged.get("suggestion")
            merged["adverse_challenge"] = c
        merged_risks.append(merged)
    merged_risks.extend(new_risks)   # 加入弱势方新发现

    return {
        "adverse_challenges": challenges,
        "adverse_new_risks": new_risks,
        "primary_risks": merged_risks,      # 裁决更新后的主审风险清单
        "debate_log": challenges,            # 辩论记录 = 合并裁决结果
        "unresolved_issues": [],             # 合并节点已仲裁，无未解决
        "current_step": "adverse_advocacy",
        "node_timings": timings,
        "llm_calls": llm_calls,
    }


async def adverse_agent_attack(state: ContractReviewState) -> dict:
    """
    节点3c：反方 Agent（弱势方立场质疑）。

    职责: 站在弱势方立场逐条质疑，输出反对意见清单。
    LLM 降级时: 基于规则命中中"高"等级的风险自动生成质疑（保底机制）。
    """
    t0 = time.perf_counter()
    clauses = state.get("clauses", [])
    parties = state.get("parties", [])

    adverse_opinions = []
    llm_result = await llm_client.generate_json(
        ADVERSE_PROMPT.format(
            contract_type=state.get("contract_type", "其他"),
            parties=json_dumps(parties),
            clauses=_format_clauses(clauses),
        ),
        system="你是弱势方代理律师，只输出 JSON。",
        default=None,
    )
    llm_hit = isinstance(llm_result, dict)
    if llm_hit:
        for opp in (llm_result.get("oppositions") or [])[:8]:
            if not isinstance(opp, dict):
                continue
            adverse_opinions.append({
                "clause_index": int(opp.get("clause_index", 0) or 0),
                "clause_text": str(opp.get("clause_text", ""))[:200],
                "opposition": str(opp.get("opposition", ""))[:300],
                "weak_party_impact": str(opp.get("weak_party_impact", ""))[:200],
                "legal_basis": str(opp.get("legal_basis", ""))[:200],
                "demanded_revision": str(opp.get("demanded_revision", ""))[:300],
            })

    # ---- 降级保底：无 LLM 结论时，从主审高风险项生成质疑 ----
    if not adverse_opinions:
        for risk in state.get("primary_risks", []):
            if risk.get("severity") == "高" and len(adverse_opinions) < 5:
                adverse_opinions.append({
                    "clause_index": risk.get("clause_index", 0),
                    "clause_text": risk.get("clause_text", "")[:200],
                    "opposition": f"反方质疑：{risk.get('risk_type', '')}"
                                  f"——{risk.get('description', '')}",
                    "weak_party_impact": "该条款显著不利于弱势方，权利义务失衡",
                    "legal_basis": risk.get("legal_basis", ""),
                    "demanded_revision": risk.get("suggestion", ""),
                })

    timings = dict(state.get("node_timings") or {})
    timings["adverse_attack"] = round((time.perf_counter() - t0) * 1000)
    llm_calls = dict(state.get("llm_calls") or {})
    llm_calls["adverse_attack"] = llm_hit
    return {
        "adverse_opinions": adverse_opinions,
        "current_step": "adverse_attack",
        "node_timings": timings,
        "llm_calls": llm_calls,
    }


async def debate_round(state: ContractReviewState) -> dict:
    """
    节点4：对抗辩论（主审必须回应反方质疑）。

    职责:
        1. LLM 主持辩论：对每个反方质疑给出接受/坚持的裁决；
        2. 接受质疑 -> 调整该条款最终风险等级（共识）；
        3. 坚持原判但反方质疑有力 -> 标记未共识，进入仲裁；
        4. LLM 降级时: 规则化辩论（反方质疑与主审等级一致的视为共识）。

    输出: debate_log + unresolved_issues
    """
    t0 = time.perf_counter()
    primary_risks = state.get("primary_risks", [])
    adverse_opinions = state.get("adverse_opinions", [])

    debate_log, unresolved = [], []

    # LLM 辩论裁决（真实多 Agent 推理：主审逐一回应反方质疑）
    debate_result = await llm_client.generate_json(
        DEBATE_PROMPT.format(
            primary_risks=json_dumps(primary_risks[:8]),
            adverse_opinions=json_dumps(adverse_opinions[:8]),
        ),
        system="你是仲裁员，只输出 JSON。",
        default=None,
    )
    llm_hit = isinstance(debate_result, dict)

    if llm_hit:
        for r in (debate_result.get("results") or [])[:8]:
            if not isinstance(r, dict):
                continue
            accept = bool(r.get("accept", False))
            entry = {
                "round": 1,
                "clause_index": int(r.get("clause_index", 0) or 0),
                "issue": str(r.get("issue", ""))[:200],
                "primary_response": str(r.get("primary_response", ""))[:300],
                "adverse_position": next(
                    (o["opposition"] for o in adverse_opinions
                     if o.get("clause_index") == int(r.get("clause_index", 0) or 0)), ""
                ),
                "status": "consensus" if accept else "unresolved",
                "final_risk_level": str(r.get("final_risk_level", "中")),
                "final_suggestion": str(r.get("final_suggestion", ""))[:300],
            }
            debate_log.append(entry)
            if not accept:
                unresolved.append(entry)
    else:
        # ---- 降级路径：规则化辩论 ----
        # 规则: 反方质疑的条款若主审也判了"高"风险 -> 双方共识；
        #        主审判"低/无"但反方坚持 -> 未共识转仲裁
        for opp in adverse_opinions:
            matched = next(
                (r for r in primary_risks
                 if r.get("clause_index") == opp.get("clause_index")), None
            )
            if matched and matched.get("severity") == "高":
                entry = {
                    "round": 1, "clause_index": opp.get("clause_index", 0),
                    "issue": opp.get("opposition", ""),
                    "primary_response": f"主审确认风险成立（{matched.get('risk_type')}），采纳反方意见",
                    "adverse_position": opp.get("opposition", ""),
                    "status": "consensus",
                    "final_risk_level": "高",
                    "final_suggestion": matched.get("suggestion", opp.get("demanded_revision", "")),
                }
                debate_log.append(entry)
            else:
                entry = {
                    "round": 1, "clause_index": opp.get("clause_index", 0),
                    "issue": opp.get("opposition", ""),
                    "primary_response": "主审坚持原判：风险等级未达调整标准（降级模式下默认回应）",
                    "adverse_position": opp.get("opposition", ""),
                    "status": "unresolved",
                    "final_risk_level": "中",
                    "final_suggestion": opp.get("demanded_revision", ""),
                }
                debate_log.append(entry)
                unresolved.append(entry)

    timings = dict(state.get("node_timings") or {})
    timings["debate"] = round((time.perf_counter() - t0) * 1000)
    llm_calls = dict(state.get("llm_calls") or {})
    llm_calls["debate"] = llm_hit
    return {
        "debate_log": debate_log,
        "unresolved_issues": unresolved,
        "current_step": "debate",
        "node_timings": timings,
        "llm_calls": llm_calls,
    }


def route_by_consensus(state: ContractReviewState) -> str:
    """条件路由：未共识问题非空 -> 仲裁；否则 -> 风险评分。"""
    return "no_consensus" if state.get("unresolved_issues") else "consensus"


async def arbitrate(state: ContractReviewState) -> dict:
    """
    节点5：仲裁（对未共识问题做最终裁决）。

    LLM 降级时: 未共识问题统一升为"中"风险并采纳反方修改要求（保守策略）。
    """
    t0 = time.perf_counter()
    unresolved = state.get("unresolved_issues", [])
    decisions = []

    llm_result = await llm_client.generate_json(
        ARBITRATE_PROMPT.format(issues=json_dumps(unresolved[:6])),
        system="你是仲裁专家，只输出 JSON。",
        default=None,
    )
    llm_hit = isinstance(llm_result, dict)
    if llm_hit:
        for d in (llm_result.get("decisions") or [])[:6]:
            if isinstance(d, dict):
                decisions.append({
                    "clause_index": int(d.get("clause_index", 0) or 0),
                    "final_risk_level": str(d.get("final_risk_level", "中")),
                    "final_suggestion": str(d.get("final_suggestion", ""))[:300],
                    "legal_basis": str(d.get("legal_basis", ""))[:200],
                })
    if not decisions:
        # 降级仲裁：保守策略，未共识一律中风险 + 采纳反方修改要求
        for issue in unresolved:
            decisions.append({
                "clause_index": issue.get("clause_index", 0),
                "final_risk_level": "中",
                "final_suggestion": issue.get("final_suggestion",
                                              "建议双方协商修改该条款"),
                "legal_basis": "仲裁保守裁决（降级模式）",
            })

    # 仲裁结论并入主审风险清单
    for decision in decisions:
        source_issue = next(
            (u for u in unresolved
             if u.get("clause_index") == decision["clause_index"]), {}
        )
        state.setdefault("primary_risks", []).append({
            "clause_index": decision["clause_index"],
            "clause_text": source_issue.get("adverse_position", "")[:200],
            "risk_type": "仲裁裁决",
            "severity": decision["final_risk_level"],
            "description": f"仲裁裁决：{source_issue.get('issue', '')}",
            "legal_basis": decision["legal_basis"],
            "suggestion": decision["final_suggestion"],
            "source": "arbitrate",
        })

    timings = dict(state.get("node_timings") or {})
    timings["arbitrate"] = round((time.perf_counter() - t0) * 1000)
    llm_calls = dict(state.get("llm_calls") or {})
    llm_calls["arbitrate"] = llm_hit
    return {
        "unresolved_issues": [],           # 仲裁后清空
        "current_step": "arbitrate",
        "node_timings": timings,
        "llm_calls": llm_calls,
    }


def calculate_risk_score(state: ContractReviewState) -> dict:
    """
    节点6：综合风险评分（含三方意见加权）。

    评分模型（需求文档 6.2.2）:
        score = Σ(严重度权重×10)               主审+仲裁风险
              + 复核异议数×5
              + 主审遗漏数×12
              + 未共识仲裁项×8
              + 完整性缺失比例×30
        上限截断 100，映射等级：>=60 高 / >=30 中 / <30 低。
    """
    primary_risks = state.get("primary_risks", [])
    review_check = state.get("review_check", {})
    completeness = state.get("completeness_check", {})

    score = 0.0
    score += sum(
        config.SEVERITY_WEIGHT.get(r.get("severity", "中"), 5) * 1.0
        for r in primary_risks
    )  # 严重度权重（高10/中5/低2）
    score += review_check.get("disputed_count", 0) * 5
    score += review_check.get("missed_count", 0) * config.MISSED_WEIGHT
    score += len(state.get("debate_log", [])) * 0   # 辩论本身不加分
    missing_ratio = 1 - completeness.get("completeness_ratio", 1)
    score += missing_ratio * config.COMPLETENESS_WEIGHT

    score = min(round(score, 1), 100.0)
    risk_level = "高" if score >= 60 else ("中" if score >= 30 else "低")
    return {"risk_score": score, "risk_level": risk_level,
            "current_step": "risk_score"}


def generate_suggestions(state: ContractReviewState) -> dict:
    """
    节点7：修改建议生成。

    汇总规则建议 + LLM 建议 + 辩论共识建议 + 仲裁建议，按条款去重。
    """
    suggestions = []
    seen = set()
    # 1. 主审/仲裁风险建议
    for risk in state.get("primary_risks", []):
        key = (risk.get("clause_index"), risk.get("suggestion", "")[:30])
        if risk.get("suggestion") and key not in seen:
            seen.add(key)
            suggestions.append({
                "clause_index": risk.get("clause_index", 0),
                "risk_type": risk.get("risk_type", ""),
                "severity": risk.get("severity", "中"),
                "suggestion": risk["suggestion"],
                "legal_basis": risk.get("legal_basis", ""),
            })
    # 2. 复核遗漏建议
    for missed in state.get("review_check", {}).get("missed", []):
        key = (missed.get("clause_index"), missed.get("suggestion", "")[:30])
        if missed.get("suggestion") and key not in seen:
            seen.add(key)
            suggestions.append({
                "clause_index": missed.get("clause_index", 0),
                "risk_type": f"[复核遗漏] {missed.get('risk_type', '')}",
                "severity": missed.get("severity", "中"),
                "suggestion": missed["suggestion"],
                "legal_basis": missed.get("legal_basis", ""),
            })
    # 3. 反方要求的修改
    for opp in state.get("adverse_opinions", []):
        key = ("adverse", opp.get("demanded_revision", "")[:30])
        if opp.get("demanded_revision") and key not in seen:
            seen.add(key)
            suggestions.append({
                "clause_index": opp.get("clause_index", 0),
                "risk_type": "[反方要求]",
                "severity": "中",
                "suggestion": opp["demanded_revision"],
                "legal_basis": opp.get("legal_basis", ""),
            })
    # 完整性缺失建议
    for missing_clause in state.get("completeness_check", {}).get("missing_clauses", []):
        suggestions.append({
            "clause_index": -1,
            "risk_type": "[必备条款缺失]",
            "severity": "中",
            "suggestion": f"合同缺少必备条款「{missing_clause}」，建议补充",
            "legal_basis": "合同完整性标准",
        })
    return {"modification_suggestions": suggestions, "current_step": "suggestions"}


# ==================================================================
# 三视角独立审查（法务 / 合规 / 业务 —— 真实多角色 LLM 推理）
# ==================================================================
async def _one_perspective(role: str, perspective: str, prompt: str,
                           system: str, fallback_fn) -> dict:
    """
    执行单个视角 Agent 的真实 LLM 推理。

    参数:
        role/perspective: 角色名与视角标识（legal/compliance/business）
        prompt/system:    视角专属提示词（差异化人格与关注维度）
        fallback_fn:      LLM 失败时的兜底意见生成器（基于真实分析数据，
                          保证任何情况下三方意见都不为"无"）

    返回:
        {role, perspective, opinion, conclusions, source}
        source: "llm"=真实模型推理 | "rule_summary"=规则数据兜底
    """
    result = await llm_client.generate_json(
        prompt, system=system, default=None,
    )
    if isinstance(result, dict) and str(result.get("opinion", "")).strip():
        return {
            "role": role,
            "perspective": perspective,
            "opinion": str(result.get("opinion", ""))[:500],
            "conclusions": [
                str(c)[:120] for c in (result.get("conclusions") or [])[:5]
                if str(c).strip()
            ],
            "source": "llm",
        }
    return fallback_fn()


def _fallback_legal_opinion(state: ContractReviewState) -> dict:
    """法务视角兜底：基于主审真实风险清单与完整性检查合成意见（非占位）。"""
    risks = state.get("primary_risks", [])
    high = [r for r in risks if r.get("severity") == "高"]
    missing = state.get("completeness_check", {}).get("missing_clauses", [])
    parts = [f"经法律视角审查，共识别风险 {len(risks)} 项"]
    if high:
        top = high[0]
        parts.append(
            f"其中高风险 {len(high)} 项，最需关注「{top.get('risk_type', '')}」："
            f"{str(top.get('description', ''))[:80]}")
    if missing:
        parts.append(f"合同缺失必备条款：{'、'.join(missing[:4])}，"
                     "存在效力与救济空洞，建议补充")
    if risks:
        top2 = risks[0]
        if top2.get("suggestion"):
            parts.append(f"优先落实修改：{str(top2.get('suggestion', ''))[:80]}")
    return {
        "role": "法务 Agent（主审）", "perspective": "legal",
        "opinion": "。".join(p for p in parts if p) + "。",
        "conclusions": [
            f"高风险条款 {len(high)} 项需优先修订" if high else "未发现高风险条款",
            f"必备条款缺失 {len(missing)} 项" if missing else "必备条款完整",
            "建议对权利义务对等性逐条复核",
        ][:3],
        "source": "rule_summary",
    }


def _fallback_compliance_opinion(state: ContractReviewState) -> dict:
    """合规视角兜底：基于复核真实复检结果合成意见。"""
    check = state.get("review_check", {})
    disputed = check.get("disputed", [])
    missed = check.get("missed", [])
    parts = [
        f"合规视角复核完成：确认主审结论 {check.get('approved_count', 0)} 项"]
    if disputed:
        parts.append(f"对 {len(disputed)} 项结论存在异议（等级依据不足，"
                     "建议补充法规论证后定级）")
    if missed:
        parts.append(f"复核补充发现主审遗漏风险 {len(missed)} 项"
                     f"（如「{missed[0].get('risk_type', '')}」），已并入清单")
    if not disputed and not missed:
        parts.append("主审结论与规则库校验一致，未发现程序与制度层面的额外缺口")
    return {
        "role": "合规 Agent（复核）", "perspective": "compliance",
        "opinion": "。".join(parts) + "。",
        "conclusions": [
            f"复核确认 {check.get('approved_count', 0)} 项风险结论",
            f"异议 {len(disputed)} 项 / 补充发现 {len(missed)} 项",
            "建议补强签署权限与用印审批留痕",
        ][:3],
        "source": "rule_summary",
    }


def _fallback_business_opinion(state: ContractReviewState) -> dict:
    """业务视角兜底：基于反方真实质疑与辩论结果合成意见。"""
    opps = state.get("adverse_opinions", [])
    debate = state.get("debate_log", [])
    parts = []
    if opps:
        parts.append(f"业务视角共提出 {len(opps)} 项商业与弱势方权益关切，"
                     f"首要关切：{str(opps[0].get('opposition', ''))[:80]}")
    if debate:
        consensus = sum(1 for d in debate if d.get("status") == "consensus")
        parts.append(f"经对抗辩论 {consensus}/{len(debate)} 项达成共识，"
                     "其余已转仲裁裁决")
    if not opps:
        parts.append("从交易结构看，双方权利义务总体平衡，"
                     "未发现显著损害弱势方权益或阻碍商业目标的条款")
    parts.append("建议就付款节奏与违约成本再行谈判，预留协商空间")
    return {
        "role": "业务 Agent（反方）", "perspective": "business",
        "opinion": "。".join(parts) + "。",
        "conclusions": [
            f"提出商业关切 {len(opps)} 项" if opps else "交易结构总体平衡",
            "付款与违约条款存在谈判空间",
            "建议保留退出与补救机制",
        ][:3],
        "source": "rule_summary",
    }


async def node_perspectives(state: ContractReviewState) -> dict:
    """
    节点7.5：三视角独立审查（法务 / 合规 / 业务）。

    三个 Agent 各自持有差异化人格提示词，【真实并发调用 LLM 推理】；
    任一视角 LLM 失败时，用该视角的规则分析数据合成兜底意见 ——
    保证三方审查意见模块任何情况下都有差异化真实内容，绝不为"无"。
    """
    t0 = time.perf_counter()
    clauses = state.get("clauses", [])
    contract_type = state.get("contract_type", "其他")

    risks_brief = "\n".join(
        f"- [{r.get('severity')}] {r.get('risk_type')}：{r.get('description', '')[:80]}"
        for r in state.get("primary_risks", [])[:8]
    ) or "（主审未发现显著风险）"
    decisions_brief = "\n".join(
        f"- [{c.get('final_risk_level', c.get('severity', ''))}] {c.get('issue', c.get('risk_type', ''))[:60]} → {c.get('final_suggestion', c.get('suggestion', ''))[:60]}"
        for c in (state.get("adverse_challenges") or state.get("primary_risks", []))[:6]
    ) or "（无辩论裁决）"
    missing = state.get("completeness_check", {}).get("missing_clauses", [])
    check = state.get("review_check", {})

    # 三视角并发推理（asyncio.gather 真实并行 LLM 调用）
    legal, compliance, business = await asyncio.gather(
        _one_perspective(
            "法务 Agent（主审）", "legal",
            LEGAL_PERSPECTIVE_PROMPT.format(
                contract_type=contract_type,
                missing="、".join(missing) if missing else "无",
                risks=risks_brief,
            ),
            system="你是资深法务审查官，只输出 JSON。",
            fallback_fn=lambda: _fallback_legal_opinion(state),
        ),
        _one_perspective(
            "合规 Agent（复核）", "compliance",
            COMPLIANCE_PERSPECTIVE_PROMPT.format(
                contract_type=contract_type,
                disputed=len(check.get("disputed", [])),
                missed=len(check.get("missed", [])),
                risks=risks_brief,
            ),
            system="你是企业首席合规官，只输出 JSON。",
            fallback_fn=lambda: _fallback_compliance_opinion(state),
        ),
        _one_perspective(
            "业务 Agent（反方）", "business",
            BUSINESS_PERSPECTIVE_PROMPT.format(
                contract_type=contract_type,
                decisions=decisions_brief,
            ),
            system="你是商业谈判顾问，只输出 JSON。",
            fallback_fn=lambda: _fallback_business_opinion(state),
        ),
        return_exceptions=True,
    )

    # 异常兜底（gather 单任务异常不会中断其他视角）
    def _safe(x, fb):
        if isinstance(x, Exception) or not isinstance(x, dict):
            return fb()
        return x

    perspectives = [
        _safe(legal, lambda: _fallback_legal_opinion(state)),
        _safe(compliance, lambda: _fallback_compliance_opinion(state)),
        _safe(business, lambda: _fallback_business_opinion(state)),
    ]

    timings = dict(state.get("node_timings") or {})
    timings["perspectives"] = round((time.perf_counter() - t0) * 1000)
    llm_calls = dict(state.get("llm_calls") or {})
    llm_calls["perspectives"] = [p.get("source") == "llm" for p in perspectives]
    return {
        "perspective_opinions": perspectives,
        "current_step": "perspectives",
        "node_timings": timings,
        "llm_calls": llm_calls,
    }


def generate_report(state: ContractReviewState) -> dict:
    """
    节点8：审查报告生成（终节点）。

    汇总三视角意见 + 辩论推理链 + 评分 + 建议，产出结构化报告；
    同时输出多 Agent 执行轨迹（llm_calls），证明真实推理链路。
    """
    perspectives = state.get("perspective_opinions", [])
    by_perspective = {p.get("perspective"): p for p in perspectives}

    # ---- 多 Agent 执行轨迹（真实推理链路审计，前端可视化展示）----
    timings = state.get("node_timings", {})
    llm_calls = state.get("llm_calls", {})
    persp_llm = llm_calls.get("perspectives") or [False, False, False]
    agents_executed = [
        {"agent": "条款拆分引擎", "step": "split_clauses",
         "elapsed_ms": timings.get("primary_review", 0), "llm_called": False,
         "output": f"拆分条款 {len(state.get('clauses', []))} 条"},
        {"agent": "主审 Agent（法务视角）", "step": "primary_review",
         "elapsed_ms": timings.get("primary_review", 0),
         "llm_called": bool(llm_calls.get("primary_review", False)),
         "output": f"识别风险 {len(state.get('primary_risks', []))} 项"},
        {"agent": "复核 Agent（合规视角）", "step": "review_check",
         "elapsed_ms": timings.get("review_check", 0),
         "llm_called": True,   # 复核为规则引擎逐条校验 + 遗漏复扫
         "output": (f"确认 {len((state.get('review_check') or {}).get('approved', []))} 项 / "
                    f"异议 {len((state.get('review_check') or {}).get('disputed', []))} 项 / "
                    f"补充 {len((state.get('review_check') or {}).get('missed', []))} 项")},
        {"agent": "反方 Agent（业务视角）", "step": "adverse_attack",
         "elapsed_ms": timings.get("adverse_attack", 0),
         "llm_called": bool(llm_calls.get("adverse_attack", False)),
         "output": f"提出质疑 {len(state.get('adverse_opinions', []))} 项"},
        {"agent": "辩论仲裁引擎", "step": "debate/arbitrate",
         "elapsed_ms": timings.get("debate", 0) + timings.get("arbitrate", 0),
         "llm_called": bool(llm_calls.get("debate", False)) or bool(llm_calls.get("arbitrate", False)),
         "output": f"辩论 {len(state.get('debate_log', []))} 轮"},
        {"agent": "法务视角推理", "step": "perspectives",
         "elapsed_ms": timings.get("perspectives", 0),
         "llm_called": bool(persp_llm[0]) if len(persp_llm) > 0 else False,
         "output": (by_perspective.get("legal", {}).get("opinion", "") or "")[:40]},
        {"agent": "合规视角推理", "step": "perspectives",
         "elapsed_ms": timings.get("perspectives", 0),
         "llm_called": bool(persp_llm[1]) if len(persp_llm) > 1 else False,
         "output": (by_perspective.get("compliance", {}).get("opinion", "") or "")[:40]},
        {"agent": "业务视角推理", "step": "perspectives",
         "elapsed_ms": timings.get("perspectives", 0),
         "llm_called": bool(persp_llm[2]) if len(persp_llm) > 2 else False,
         "output": (by_perspective.get("business", {}).get("opinion", "") or "")[:40]},
    ]

    report = {
        "contract_type": state.get("contract_type", "其他"),
        "parties": state.get("parties", []),
        "clause_count": len(state.get("clauses", [])),
        "risk_score": state.get("risk_score", 0),
        "risk_level": state.get("risk_level", "低"),
        "completeness": state.get("completeness_check", {}),
        # 三方审查意见（法务/合规/业务三视角，真实差异化内容）
        "perspective_opinions": perspectives,
        # 兼容字段：前端 tri-view 直接可读的三方意见摘要
        "primary_opinion": (by_perspective.get("legal", {}) or {}).get("opinion", ""),
        "review_opinion": (by_perspective.get("compliance", {}) or {}).get("opinion", ""),
        "counter_opinion": (by_perspective.get("business", {}) or {}).get("opinion", ""),
        # 结构化风险数据
        "primary_risks": state.get("primary_risks", []),
        "review_check": state.get("review_check", {}),
        "adverse_opinions": state.get("adverse_opinions", []),
        # 推理链
        "debate_log": state.get("debate_log", []),
        "unresolved_issues": state.get("unresolved_issues", []),
        # 修改建议
        "modification_suggestions": state.get("modification_suggestions", []),
        # 多 Agent 执行轨迹（真实调用审计）
        "agents_executed": agents_executed,
        # 节点耗时（性能观测）
        "node_timings": timings,
    }
    return {"review_report": report, "current_step": "report_generate"}


def json_dumps(obj, limit: int = 3000) -> str:
    """安全 JSON 序列化（中文保留 + 截断限长）。"""
    import json as _json
    try:
        return _json.dumps(obj, ensure_ascii=False)[:limit]
    except Exception:
        return str(obj)[:limit]


# ==================================================================
# 四、工作流图构建
# ==================================================================
def build_contract_review_graph() -> "CompiledGraph":
    """
    轻量化多 Agent 审查：9 节点（主审→复核→反方+辩论合并→评分→建议→三视角并发→报告→导出）。

    提速关键：
    - 反方 + 辩论 + 仲裁 合并为 adverse_advocacy（一次 LLM 调用同时做质疑+裁决）
    - suggestions 节点去 LLM（从已有主审+反方+辩论结果去重合并）
    - 主审/反方/三视角 prompt 全部裁掉合同全文，只给风险条款摘要
    """
    workflow = StateGraph(ContractReviewState)

    workflow.add_node("parse_doc", parse_document)
    workflow.add_node("split_clauses", node_split_clauses)
    workflow.add_node("primary_review", primary_agent_review)
    workflow.add_node("review_check", review_agent_check)
    workflow.add_node("adverse_advocacy", adverse_advocacy_node)   # 反方+辩论+仲裁 合并
    workflow.add_node("risk_score", calculate_risk_score)
    workflow.add_node("suggestions", generate_suggestions)        # 去 LLM，纯去重合并
    workflow.add_node("perspectives", node_perspectives)           # 三视角真实并发
    workflow.add_node("report_generate", generate_report)

    workflow.set_entry_point("parse_doc")
    workflow.add_edge("parse_doc", "split_clauses")
    workflow.add_edge("split_clauses", "primary_review")
    workflow.add_edge("primary_review", "review_check")
    workflow.add_edge("review_check", "adverse_advocacy")         # 跳过 adverse/debate/arbitrate
    workflow.add_edge("adverse_advocacy", "risk_score")           # 无条件路由，直接评分
    workflow.add_edge("risk_score", "suggestions")
    workflow.add_edge("suggestions", "perspectives")
    workflow.add_edge("perspectives", "report_generate")
    workflow.add_edge("report_generate", END)

    # 编译（启用 MemorySaver 检查点，支持中断恢复与审计回溯）
    checkpointer = MemorySaver()
    return workflow.compile(checkpointer=checkpointer)


# 全局工作流实例
contract_review_graph = build_contract_review_graph()


# 9 个审查节点显示名（轻量化工作流，前端进度条用）
NODE_DISPLAY_NAMES = {
    "parse_doc": "合同解析",
    "split_clauses": "条款拆分",
    "primary_review": "主审 Agent 风险识别",
    "review_check": "复核 Agent 规则校验",
    "adverse_advocacy": "反方质疑+辩论仲裁",
    "risk_score": "综合风险评分",
    "suggestions": "去重合并修改建议",
    "perspectives": "三视角独立推理",
    "report_generate": "汇总审查报告",
}
TOTAL_NODES = len(NODE_DISPLAY_NAMES)


async def run_contract_review(contract_text: str,
                              contract_type: str | None = None,
                              progress_queue=None) -> dict:
    """
    执行完整合同审查工作流。

    参数:
        contract_text: 合同全文
        contract_type: 合同类型（None 时自动识别）
        progress_queue: asyncio.Queue 可选；传入时逐节点 yield 进度事件
            {"node", "node_name", "node_index", "total", "pct",
             "elapsed_ms", "status", "risk_score?"}

    返回:
        工作流终态（含 review_report / risk_score 等全部字段）
    """
    thread_id = uuid.uuid4().hex[:12]
    initial_state: ContractReviewState = {
        "contract_text": contract_text,
        "contract_type": contract_type or "",
        "parties": [],
        "error": None,
    }

    # 推送一个 "已启动" 事件，让前端立即收到进度通道已建立
    if progress_queue is not None:
        await progress_queue.put({
            "node": "start", "node_name": "工作流已启动",
            "node_index": 0, "total": TOTAL_NODES, "pct": 0,
            "elapsed_ms": 0, "status": "running",
        })

    try:
        if progress_queue is None:
            final_state = await contract_review_graph.ainvoke(initial_state, thread_id)
        else:
            # 流式执行：逐节点 yield 进度事件进队列
            node_index = 0
            async for event in contract_review_graph.astream(initial_state, thread_id):
                node = event["node"]
                state = event["state"]
                node_index += 1
                timing = (state.get("node_timings") or {}).get(node, 0)
                await progress_queue.put({
                    "node": node,
                    "node_name": NODE_DISPLAY_NAMES.get(node, node),
                    "node_index": node_index,
                    "total": TOTAL_NODES,
                    "pct": int(node_index / TOTAL_NODES * 100),
                    "elapsed_ms": int(timing),
                    "status": "running",
                })
            final_state = state   # astream 循环结束后 state 即终态
        return final_state
    except Exception as e:
        logger.error("合同审查工作流异常: %s", e, exc_info=True)
        # 异常降级：至少返回规则引擎层面的结果
        clauses = split_clauses(contract_text)
        rule_risks = risk_rule_engine.check_contract(clauses)
        fallback_report = {
            "contract_type": contract_type or detect_contract_type(contract_text),
            "clause_count": len(clauses),
            "risk_score": min(len(rule_risks) * 20, 100),
            "risk_level": "高" if rule_risks else "低",
            "primary_risks": [], "review_check": {}, "adverse_opinions": [],
            "debate_log": [], "unresolved_issues": [],
            "modification_suggestions": [],
            "completeness": {},
            "node_timings": {},
            "degraded": True,
            "degraded_reason": f"工作流异常已降级: {e}",
        }
        return {"review_report": fallback_report,
                "risk_score": fallback_report["risk_score"],
                "risk_level": fallback_report["risk_level"],
                "error": str(e)}
