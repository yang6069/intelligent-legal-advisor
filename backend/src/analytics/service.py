# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 数据分析模块（需求文档 4.6）
==================================================================
模块职责：
    1. 法律咨询热点分析（4.6.1）：领域分布 / 时间趋势 / 热点关键词；
    2. 法条引用统计分析（4.6.2）：高频引用 Top N / 共引网络；
    3. 用户聚类分析（4.6.3）：基于行为特征的 K-Means 聚类
       （NumPy 手写实现，无 sklearn 依赖）；
    4. 仪表盘统计：用户/问答/合同/知识库全维度运营指标。

数据来源：
    qa_messages（含 citations JSON）、qa_sessions、contracts、
    kb_documents、sys_users。
==================================================================
"""
import json
import logging
import re
from collections import Counter

from src import config, database
from src.utils.text import fix_mojibake

logger = logging.getLogger("zhifatong.analytics")

# 法律领域关键词表（咨询热点分类依据）
DOMAIN_KEYWORDS = [
    ("劳动用工", ["劳动", "工伤", "社保", "裁员", "辞退", "工资", "加班", "竞业"]),
    ("合同纠纷", ["合同", "违约", "定金", "解除", "履约", "买卖", "租赁"]),
    ("婚姻家事", ["离婚", "婚姻", "抚养", "继承", "财产分割", "彩礼"]),
    ("知识产权", ["商标", "专利", "著作权", "侵权", "知识产权"]),
    ("债权债务", ["借款", "欠款", "利息", "担保", "抵押", "债权", "债务"]),
    ("公司治理", ["股权", "公司", "股东", "设立", "清算", "破产", "董事"]),
    ("房产建筑", ["房屋", "房产", "装修", "物业", "施工", "不动产"]),
    ("侵权损害", ["赔偿", "侵权", "人身损害", "医疗", "交通", "事故"]),
]


# ==================================================================
# 一、工具函数
# ==================================================================
def _classify_domain(text: str) -> str:
    """
    按关键词表将文本归类到法律领域（命中最多者胜出，兜底「其他」）。
    """
    scores = {domain: sum(1 for kw in kws if kw in text)
              for domain, kws in DOMAIN_KEYWORDS}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "其他"


def _extract_keywords(texts: list[str], top_n: int = 20) -> list[tuple]:
    """
    热点关键词提取（jieba 分词 + 法律领域词典 + 停用词过滤）。

    2026-09-24 两次修复滑动窗口后，仍有"同中竞、动合同、条款有什"这类
    跨词边界的碎片。根本解法：用真正的中文分词器 jieba。
    """
    import jieba
    import jieba.posseg as pseg

    # ---- 法律领域关键词词典（jieba 用户词典，强制整词切出）----
    # 这些法律专有词不能被切成碎片，比如"竞业限制"不能变成"竞业/限制"
    LEGAL_WORDS = [
        # 合同
        "劳动合同", "房屋租赁合同", "买卖合同", "承揽合同", "借款合同",
        "合同纠纷", "合同解除", "合同违约", "合同效力", "合同约定",
        # 劳动法
        "竞业限制", "竞业禁止", "服务期", "违约金", "经济补偿", "经济赔偿金",
        "工伤保险", "社会保险", "加班工资", "违法解除", "代通知金",
        # 债权债务
        "民间借贷", "逾期利息", "双倍返还", "定金罚则", "连带责任",
        # 婚姻家事
        "离婚财产分割", "夫妻共同财产", "抚养费", "赡养费", "继承权",
        # 公司
        "股权转让", "公司清算", "破产清算", "股东知情权",
        # 房产
        "房屋所有权", "不动产登记", "房屋租赁",
        # 程序法
        "诉讼时效", "举证责任", "管辖法院", "仲裁条款",
        # 高频组合
        "法律规定", "法律要求", "合法权益", "违约责任", "侵权责任",
    ]
    for w in LEGAL_WORDS:
        jieba.add_word(w, freq=10000)

    # ---- 停用词（真正的虚词/疑问词/指代词）----
    STOPWORDS = {
        # 疑问词
        "什么", "怎么", "为何", "为什么", "怎样", "哪些", "哪", "吗", "呢",
        # 指代 / 代词
        "我", "你", "他", "她", "它", "自己", "本人", "对方", "大家",
        "这样", "那样", "这种", "那种", "这里", "那里",
        # 范围词
        "以上", "以下", "以外", "以内", "以后", "以前", "之后", "之前",
        "之内", "之外", "上面", "下面", "里面", "外面",
        # 虚词 / 介词 / 连词
        "的", "了", "吗", "吧", "呢", "过", "着", "是", "在", "有",
        "和", "或", "与", "及", "对", "为", "以", "依据", "按", "由",
        "经", "因", "所以", "但是", "不过", "而且", "还是", "只是",
        "就是", "都是", "也是", "虽然", "如果", "应该", "必须", "可以",
        "需要", "比如", "例如", "包括", "还有", "可能", "一定", "已经",
        "没有", "相关", "有关", "其他", "一些", "一个", "一种",
        # 高频无意义业务词
        "规定", "约定", "问题", "事情", "方面", "情况", "内容",
        "部分", "事项", "条款", "情况下", "规定的",
    }

    # ---- POS 过滤：只保留名词 / 动词 / 专业术语 ----
    # n=名词, vn=名动词, nz=专业术语, ng=名词性语素
    KEEP_POS = {'n', 'vn', 'nz', 'ng'}

    counter = Counter()
    for text in texts:
        if not text: continue
        # posseg 词性标注
        for word, flag in pseg.cut(text):
            w = word.strip()
            if not w or len(w) < 2: continue           # 单字扔掉
            if w in STOPWORDS: continue
            if w.isdigit(): continue
            # 如果有词性标注，优先用它
            if flag and flag.startswith(('n', 'vn', 'nz', 'ng')):
                counter[w] += 1
            elif not flag:
                # 无词性标注（英文/混合），按长度判断
                if len(w) >= 2 and w not in STOPWORDS:
                    counter[w] += 1

    return counter.most_common(top_n)


# ==================================================================
# 二、法律咨询热点分析（4.6.1）
# ==================================================================
def analyze_hot_topics(user_id: int, days: int = 30,
                       is_admin: bool = False) -> dict:
    """
    法律咨询热点分析（角色感知数据范围）。

    - 管理员：统计全平台全部角色的提问（全局运营视角）；
    - 法务 / 普通用户：仅统计本人的提问（数据隔离）。

    逻辑:
        1. 取近 N 天范围内提问记录；
        2. 按关键词表分类领域 -> 领域分布饼图数据；
        3. 按天统计咨询量 -> 时间趋势折线数据；
        4. 二元组词频 -> 热点关键词词云数据。

    返回:
        {"domain_distribution": {...}, "daily_trend": [...],
         "top_keywords": [...], "total_queries": int}
    """
    scope = "" if is_admin else " AND user_id = %s"
    args: tuple = (days,) if is_admin else (user_id, days)
    rows = database.query_all(
        "SELECT content, created_at FROM qa_messages "
        "WHERE role = 'user'" + scope +
        " AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY) "
        "ORDER BY created_at", args)
    if not rows:
        fallback_scope = "" if is_admin else " AND user_id = %s"
        fallback_args: tuple = () if is_admin else (user_id,)
        rows = database.query_all(
            "SELECT content, created_at FROM qa_messages "
            "WHERE role = 'user'" + fallback_scope +
            " ORDER BY created_at DESC LIMIT 500", fallback_args)

    # 1. 领域分布
    domain_counter = Counter(_classify_domain(r["content"] or "") for r in rows)

    # 2. 日趋势（YYYY-MM-DD -> 数量）
    daily = Counter(str(r["created_at"])[:10] for r in rows)
    daily_trend = [{"date": d, "count": c} for d, c in sorted(daily.items())]

    # 3. 热点关键词
    keywords = _extract_keywords([r["content"] or "" for r in rows])

    return {
        "total_queries": len(rows),
        "domain_distribution": dict(domain_counter),
        "daily_trend": daily_trend,
        "top_keywords": [{"keyword": k, "count": c} for k, c in keywords],
    }


# ==================================================================
# 三、法条引用统计分析（4.6.2）
# ==================================================================
def analyze_citation_patterns(user_id: int, is_admin: bool = False) -> dict:
    """
    法条引用统计分析（角色感知数据范围）。

    - 管理员：统计全平台全部角色的回答引用；
    - 法务 / 普通用户：仅统计本人回答携带的引用。

    逻辑:
        1. 汇总作用域内 assistant 消息的 citations JSON；
        2. 高频引用法条 Top 20（法律名+条款号）；
        3. 共引网络（同一回答中共同出现的法条对，Top 50）
           -> 前端关系图数据。
    """
    scope = "" if is_admin else " AND user_id = %s"
    args: tuple = () if is_admin else (user_id,)
    rows = database.query_all(
        "SELECT citations FROM qa_messages WHERE role = 'assistant'" + scope +
        " AND citations IS NOT NULL AND citations != 'null'", args)

    citation_counter = Counter()
    co_citation = Counter()
    for row in rows:
        try:
            citations = json.loads(row["citations"]) if row["citations"] else []
        except (json.JSONDecodeError, TypeError):
            continue
        keys = [f"{c.get('law_name', '')}{c.get('article_no', '')}".strip()
                for c in citations if isinstance(c, dict)]
        keys = [k for k in keys if k]
        citation_counter.update(keys)
        # 共引对（组合数 C(n,2)）
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                pair = tuple(sorted((keys[i], keys[j])))
                co_citation[pair] += 1

    return {
        "total_answers_with_citations": len(rows),
        "top_cited_articles": [
            {"article": k, "count": v} for k, v in citation_counter.most_common(20)],
        "co_citation_network": [
            {"source": a, "target": b, "count": c}
            for (a, b), c in co_citation.most_common(50)],
    }


# ==================================================================
# 四、用户聚类分析（4.6.3，NumPy 手写 K-Means）
# ==================================================================
def cluster_users(n_clusters: int = 4) -> dict:
    """
    用户行为聚类分析（K-Means，NumPy 手写实现）。

    权限说明：本接口为系统级运营分析（分析对象是用户账号群体），
    仅系统管理员可调用（路由层强制校验），法务/普通用户不可见——
    避免横向暴露其他用户的业务行为数据。

    特征向量（5 维，使用行为日志聚合）:
        [提问数, 合同审查数, 平均回答置信度, 活跃天数, 角色等级]

    算法:
        1. 特征聚合（SQL）+ 标准化（Z-Score）；
        2. K-Means：随机中心 -> 迭代分配/更新（<= 100 轮或收敛）；
        3. 聚类画像 = 各簇特征均值。

    返回: {"cluster_profiles": [...], "cluster_labels": {...},
           "cluster_distribution": {簇编号: 用户数}, "users": [...]}
    """
    import numpy as np

    # ---- 特征聚合 ----
    rows = database.query_all(
        "SELECT u.id, u.username, u.role, "
        "  (SELECT COUNT(*) FROM qa_messages m WHERE m.user_id = u.id AND m.role='user') AS query_count, "
        "  (SELECT COUNT(*) FROM contracts c WHERE c.user_id = u.id) AS contract_count, "
        "  (SELECT AVG(m.confidence) FROM qa_messages m WHERE m.user_id = u.id AND m.role='assistant') AS avg_confidence, "
        "  (SELECT COUNT(DISTINCT DATE(m.created_at)) FROM qa_messages m WHERE m.user_id = u.id) AS active_days "
        "FROM sys_users u WHERE u.status = 1")
    if not rows:
        return {"cluster_profiles": [], "cluster_labels": {},
                "cluster_distribution": {}, "users": []}

    role_level = {"user": 1, "legal_admin": 2, "admin": 3}
    features = np.array([[float(r["query_count"]), float(r["contract_count"]),
                          float(r["avg_confidence"] or 0), float(r["active_days"]),
                          float(role_level.get(r["role"], 1))] for r in rows])
    feature_names = ["query_count", "contract_count", "avg_confidence",
                     "active_days", "role_level"]

    # ---- Z-Score 标准化（方差为 0 的列保持原值）----
    mean = features.mean(axis=0)
    std = features.std(axis=0)
    std_safe = np.where(std == 0, 1.0, std)
    X = (features - mean) / std_safe

    # ---- K-Means 主过程 ----
    n = len(X)
    k = min(n_clusters, n)
    rng = np.random.default_rng(42)
    centers = X[rng.choice(n, size=k, replace=False)].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(100):
        # 分配阶段：欧氏距离最近的中心
        dists = np.linalg.norm(X[:, None, :] - centers[None, :, :], axis=2)
        new_labels = dists.argmin(axis=1)
        # 更新阶段：簇内均值（空簇保持原中心）
        for ci in range(k):
            mask = new_labels == ci
            if mask.any():
                centers[ci] = X[mask].mean(axis=0)
        if np.array_equal(labels, new_labels):
            break   # 收敛
        labels = new_labels

    # ---- 聚类画像（原始尺度均值）----
    # 群体标签由各簇真实特征均值自动推导（咨询/审查侧重、活跃度），
    # 避免静态标签与实际群体行为不符
    def _label_cluster(feat: dict) -> str:
        query = float(feat.get("query_count", 0))
        contract = float(feat.get("contract_count", 0))
        if contract > 0 and contract >= query:
            return "合同审查为主用户"
        if query >= 5:
            return "高频咨询用户"
        if query + contract > 0:
            return "常规咨询用户"
        return "低活跃用户"

    profiles, distribution = [], {}
    for ci in range(k):
        mask = labels == ci
        distribution[int(ci)] = int(mask.sum())
        feat = {name: round(float(features[mask, i].mean()), 2)
                for i, name in enumerate(feature_names)}
        profiles.append({
            "cluster": ci,
            "label": _label_cluster(feat),
            "size": int(mask.sum()),
            "features": feat,
        })

    users = [{"id": r["id"], "username": r["username"],
              "cluster": int(labels[i]), "query_count": r["query_count"],
              "contract_count": r["contract_count"]} for i, r in enumerate(rows)]

    # 群体标签字典（自动推导标签，供前端兜底回显）
    cluster_labels = {p["cluster"]: p["label"] for p in profiles}
    return {"cluster_profiles": profiles, "cluster_labels": cluster_labels,
            "cluster_distribution": distribution, "users": users}


# ==================================================================
# 五、仪表盘统计（运营总览）
# ==================================================================
def dashboard_stats(user_id: int, is_admin: bool = False) -> dict:
    """
    仪表盘统计总览（角色感知数据范围）。

    - 管理员：业务指标 = 全平台全部角色汇总（合同审查记录总数 /
      咨询总数），满足全局运营视角；
    - 法务 / 普通用户：业务指标 = 仅本人数据（数据隔离）；
    - 共享资源（知识库文档/图谱节点）与系统指标（注册用户数）为全局统计。
    """
    # 业务指标作用域：管理员全平台，其他角色仅本人
    qa_scope = "" if is_admin else " AND user_id = %s"
    qa_args: tuple = () if is_admin else (user_id,)

    # ---- 系统指标：注册用户数（账号维度，非业务数据） ----
    users = database.query_one(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN role='admin' THEN 1 ELSE 0 END) AS admins, "
        "SUM(CASE WHEN role='legal_admin' THEN 1 ELSE 0 END) AS legal_admins, "
        "SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) AS normal_users "
        "FROM sys_users WHERE status = 1") or {}

    # ---- 业务指标：按角色作用域 ----
    qa = database.query_one(
        "SELECT COUNT(*) AS total_messages, "
        "SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) AS total_consultations, "
        "AVG(CASE WHEN role='assistant' THEN latency_ms END) AS avg_latency, "
        "AVG(CASE WHEN role='assistant' THEN confidence END) AS avg_confidence "
        "FROM qa_messages WHERE 1=1" + qa_scope, qa_args) or {}
    today_qa = database.query_one(
        "SELECT COUNT(*) AS c FROM qa_messages "
        "WHERE role='user' AND DATE(created_at) = CURDATE()" + qa_scope,
        qa_args)["c"]

    # ---- 合同指标：按角色作用域（总数含审查中/失败；风险仅已完成） ----
    contract_all = database.query_one(
        "SELECT COUNT(*) AS c FROM contracts WHERE 1=1" + qa_scope,
        qa_args)["c"]
    contract = database.query_one(
        "SELECT COUNT(*) AS total, "
        "AVG(duration_ms) AS avg_duration, "
        "AVG(risk_score) AS avg_risk, "
        "SUM(CASE WHEN risk_level='高' THEN 1 ELSE 0 END) AS high_risk, "
        "SUM(CASE WHEN risk_level='中' THEN 1 ELSE 0 END) AS mid_risk, "
        "SUM(CASE WHEN risk_level='低' THEN 1 ELSE 0 END) AS low_risk "
        "FROM contracts WHERE status = 'completed'" + qa_scope,
        qa_args) or {}

    # ---- 共享资源：知识库（平台共享库，全量统计） ----
    knowledge = database.query_one(
        "SELECT COUNT(*) AS total_docs, "
        "SUM(char_count) AS total_chars, "
        "SUM(CASE WHEN doc_type='law' THEN 1 ELSE 0 END) AS laws, "
        "SUM(CASE WHEN doc_type='case' THEN 1 ELSE 0 END) AS cases, "
        "SUM(CASE WHEN doc_type='interpretation' THEN 1 ELSE 0 END) AS interpretations, "
        "SUM(CASE WHEN doc_type='template' THEN 1 ELSE 0 END) AS templates "
        "FROM kb_documents") or {}

    # ---- 最近合同审查记录：按角色作用域 ----
    recent_contracts = database.query_all(
        "SELECT id, title, contract_type, risk_score, risk_level, "
        "duration_ms, created_at FROM contracts "
        "WHERE status = 'completed'" + qa_scope +
        " ORDER BY id DESC LIMIT 10", qa_args)
    # ---- 风险分布（饼图数据）----
    risk_dist = [
        {"name": "高风险", "value": int(contract.get("high_risk") or 0)},
        {"name": "中风险", "value": int(contract.get("mid_risk") or 0)},
        {"name": "低风险", "value": int(contract.get("low_risk") or 0)},
    ]

    # ---- 知识图谱概况 ----
    graph = {"available": False}
    try:
        from src.rag import graph_store
        if graph_store.ensure_graph():
            graph = graph_store.graph_stats()
    except Exception as e:
        logger.debug("图谱统计降级: %s", e)

    def _r(v, nd=0):
        """None 安全取整函数（保留 nd 位小数）。"""
        return round(float(v), nd) if v is not None else 0

    return {
        "users": {"total": users.get("total") or 0,
                  "admins": users.get("admins") or 0,
                  "legal_admins": users.get("legal_admins") or 0,
                  "normal_users": users.get("normal_users") or 0},
        "qa": {"total_messages": qa.get("total_messages") or 0,
               "total_consultations": qa.get("total_consultations") or 0,
               "today_queries": today_qa,
               "avg_latency_ms": _r(qa.get("avg_latency")),
               "avg_confidence": _r(qa.get("avg_confidence"), 2)},
        "contract": {"total": contract_all,
                     "completed": contract.get("total") or 0,
                     "avg_duration_ms": _r(contract.get("avg_duration")),
                     "avg_risk_score": _r(contract.get("avg_risk"), 1),
                     "risk_distribution": risk_dist},
        "knowledge": {"total_docs": knowledge.get("total_docs") or 0,
                      "total_chars": knowledge.get("total_chars") or 0,
                      "laws": knowledge.get("laws") or 0,
                      "cases": knowledge.get("cases") or 0,
                      "interpretations": knowledge.get("interpretations") or 0,
                      "templates": knowledge.get("templates") or 0},
        "recent_contracts": recent_contracts,
        "graph": graph,
    }


def recent_contracts_page(user_id: int, page: int = 1, size: int = 5,
                          is_admin: bool = False) -> dict:
    """
    最近合同审查记录（分页版，仪表盘表格数据源；角色感知数据范围）。

    - 管理员：全部角色的已完成审查记录；
    - 其他角色：仅本人记录。

    参数:
        user_id: 当前登录用户；page: 页码（1 起）；size: 每页条数（1-50）

    返回:
        {"total": int, "items": [...]}
    """
    page = max(1, page)
    size = min(max(1, size), 50)
    scope = "" if is_admin else " AND user_id = %s"
    args: tuple = () if is_admin else (user_id,)
    total = database.query_one(
        "SELECT COUNT(*) AS c FROM contracts "
        "WHERE status = 'completed'" + scope, args)["c"]
    items = database.query_all(
        "SELECT id, title, contract_type, risk_score, risk_level, "
        "duration_ms, created_at FROM contracts "
        "WHERE status = 'completed'" + scope +
        " ORDER BY id DESC LIMIT %s OFFSET %s",
        args + (size, (page - 1) * size))
    return {"total": total, "items": items}


def repair_mojibake_rows() -> int:
    """
    历史数据乱码修复（幂等，启动时执行一次）。

    修复范围：
        1. 列默认值：首次建库时 init.sql 的中文 DEFAULT 被 latin1 误码
           （如 DEFAULT 'å…¶ä»–'），逐列检测并还原（'å…¶ä»–' -> '其他'）；
        2. 行数据：contracts.title / contract_type / file_name 中
           latin-1 误码的乱码还原；无法还原的 contract_type 归"其他"。
    """
    fixed = _repair_column_defaults()

    rows = database.query_all(
        "SELECT id, title, contract_type, file_name FROM contracts")
    for row in rows:
        title = fix_mojibake(row.get("title"))
        file_name = fix_mojibake(row.get("file_name"))
        ctype = fix_mojibake(row.get("contract_type"))
        # contract_type 白名单校验：乱码/未知类型归"其他"
        valid_types = {"买卖合同", "租赁合同", "劳动合同", "服务合同",
                       "借款合同", "保密协议", "技术合同", "其他"}
        if ctype not in valid_types:
            ctype = "其他"
        if (title != row.get("title") or file_name != row.get("file_name")
                or ctype != row.get("contract_type")):
            database.execute(
                "UPDATE contracts SET title = %s, file_name = %s, "
                "contract_type = %s WHERE id = %s",
                (title, file_name, ctype, row["id"]))
            fixed += 1
    if fixed:
        logger.info("历史乱码修复完成（含列默认值）：%d 处", fixed)
    return fixed


def _repair_column_defaults() -> int:
    """
    修复库内全部"中文默认值被 latin1 误码"的列（幂等）。

    原理：
        扫描 information_schema.COLUMNS 的 COLUMN_DEFAULT，
        含 latin-1 误码特征时还原并 ALTER COLUMN ... SET DEFAULT。
        数字/时间等默认值不含误码特征字符，天然跳过。
    """
    fixed = 0
    try:
        cols = database.query_all(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_DEFAULT "
            "FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND COLUMN_DEFAULT IS NOT NULL "
            "AND COLUMN_TYPE LIKE '%%char%%'",
            (config.MYSQL_DATABASE,))
        for col in cols:
            default_val = str(col["COLUMN_DEFAULT"])
            repaired = fix_mojibake(default_val)
            if repaired != default_val:
                database.execute(
                    f"ALTER TABLE `{col['TABLE_NAME']}` "
                    f"ALTER COLUMN `{col['COLUMN_NAME']}` SET DEFAULT %s",
                    (repaired,))
                logger.info("列默认值修复: %s.%s -> %s",
                            col["TABLE_NAME"], col["COLUMN_NAME"], repaired)
                fixed += 1
    except Exception as e:
        logger.warning("列默认值修复跳过: %s", e)
    return fixed
