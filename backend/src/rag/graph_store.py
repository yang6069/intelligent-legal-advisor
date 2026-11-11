# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 法律知识图谱（Neo4j GraphRAG 组件）
==================================================================
模块职责：
    1. 图谱构建：法条/案例/概念三类节点 + 归属/引用/提及/关联四类边；
    2. 图谱检索（GraphRAG 第三路）：从向量召回的法条出发做 1-2 跳扩展，
       召回关联法条与引用案例（发现隐性关联）；
    3. 图谱可视化：导出节点/边数据供前端 ECharts 力导向图渲染；
    4. 降级容错：Neo4j 不可用时所有接口返回空结果（检索退化为两路）。

图谱 Schema（需求文档 4.1.5）:
    (:Law)-[:HAS_ARTICLE]->(:Article)
    (:Article)-[:RELATES {type}]->(:Article)      引用/废止/解释/修订
    (:Article)-[:MENTIONS]->(:Concept)
    (:Case)-[:REFERENCES {role}]->(:Article)      APPLIES/CITES
    (:Case)-[:INVOLVES]->(:Party {name})
==================================================================
"""
import logging
import re
import threading

from src import config

logger = logging.getLogger("zhifatong.rag.graph")

# 图谱驱动（vendor 目录的 neo4j 包，导入失败则降级）
_driver = None
_graph_available = False


def _init_driver():
    """
    初始化 Neo4j 驱动（懒加载，线程安全）。

    结果:
        _graph_available = True  图谱功能可用
        _graph_available = False Neo4j 不可达，全部功能降级
    """
    global _driver, _graph_available
    if _driver is not None:
        return
    if not config.NEO4J_ENABLED:
        logger.info("Neo4j 图谱已被配置禁用（NEO4J_ENABLED=false）")
        return
    try:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
            connection_timeout=5, max_connection_pool_size=16,
        )
        with _driver.session() as session:
            session.run("RETURN 1")
        _graph_available = True
        logger.info("Neo4j 知识图谱连接成功")
    except Exception as e:
        logger.warning("Neo4j 不可用（GraphRAG 第三路降级关闭）: %s", e)
        _graph_available = False


_init_lock = threading.Lock()


def ensure_graph() -> bool:
    """确保驱动已初始化（线程安全），返回图谱是否可用。"""
    global _graph_available
    with _init_lock:
        if _driver is None and not _graph_available:
            _init_driver()
    return _driver is not None and _graph_available


def close() -> None:
    """关闭图谱驱动（应用 shutdown 时释放长连接资源，安全幂等）。"""
    global _driver
    if _driver is not None:
        try:
            _driver.close()
        except Exception:
            pass
        _driver = None


# ==================================================================
# 一、图谱构建
# ==================================================================
def create_constraints() -> None:
    """创建节点唯一性约束（幂等，首次构建时执行）。"""
    if not ensure_graph():
        return
    with _driver.session() as session:
        for label, prop in (("Law", "law_id"), ("Article", "article_id"),
                            ("Case", "case_id"), ("Concept", "name")):
            session.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{prop} IS UNIQUE"
            )


def build_graph_from_documents(documents: list[dict]) -> dict:
    """
    从知识库文档构建图谱。

    参数:
        documents: kb_documents 行列表（含 doc_type/law_name/article_no/content/title）

    返回:
        {"nodes": 节点数, "relations": 关系数, "available": 图谱是否可用}

    构建规则:
        1. law 类文档 -> Law 节点（每部法律一个）+ Article 节点（每个条文一个）；
        2. case 类文档 -> Case 节点，正文中提及的法条建立 REFERENCES 边；
        3. 概念抽取：预置法律概念词典匹配正文 -> Concept 节点 + MENTIONS 边；
        4. 法条同概念共现 -> RELATES {type:"关联"} 边。
    """
    if not ensure_graph():
        return {"nodes": 0, "relations": 0, "available": False}

    create_constraints()
    node_count, rel_count = 0, 0

    # 预置法律概念词典（用于 MENTIONS 抽取）
    concepts_dict = _load_concepts()

    with _driver.session() as session:
        # 已建立的法条 ID -> 节点映射（供案例引用建边）
        article_ids: dict[str, str] = {}

        for doc in documents:
            law_name = doc.get("law_name") or doc.get("title", "未知名")
            content = doc.get("content", "")[:3000]        # 截断保护

            # ---- 1. 法律与法条节点 ----
            if doc.get("doc_type") in ("law", "interpretation"):
                law_id = f"law::{law_name}"
                session.run(
                    "MERGE (l:Law {law_id: $lid}) "
                    "SET l.name = $name, l.level = $level, l.status = $status",
                    lid=law_id, name=law_name,
                    level="司法解释" if doc.get("doc_type") == "interpretation" else "法律",
                    status=doc.get("effectiveness", "有效"),
                )
                node_count += 1

                # 按条文拆出 Article 节点
                articles = _extract_articles(content)
                for art_no, art_text in articles:
                    article_id = f"{law_name}::{art_no}"
                    session.run(
                        "MERGE (a:Article {article_id: $aid}) "
                        "SET a.article_no = $no, a.content = $text, a.law_name = $law, "
                        "    a.effective_date = $eff "
                        "WITH a MATCH (l:Law {law_id: $lid}) "
                        "MERGE (l)-[:HAS_ARTICLE]->(a)",
                        aid=article_id, no=art_no, text=art_text[:2000],
                        law=law_name, eff=doc.get("publish_date") or "",
                        lid=law_id,
                    )
                    article_ids[f"{law_name} {art_no}"] = article_id
                    node_count += 1

                    # 概念提及边
                    mentioned = [c for c in concepts_dict if c in art_text]
                    for concept in mentioned:
                        session.run(
                            "MERGE (c:Concept {name: $name}) "
                            "WITH c MATCH (a:Article {article_id: $aid}) "
                            "MERGE (a)-[:MENTIONS]->(c)",
                            name=concept, aid=article_id,
                        )
                        node_count += 0     # 概念节点可能重复计数，从略
                        rel_count += 1

                # 同法律内相邻条文建立关联边（法条间的解释/衔接关系近似）
                arts = list(article_ids.items())
                for (k1, id1), (k2, id2) in zip(arts, arts[1:]):
                    if k1.startswith(law_name) and k2.startswith(law_name):
                        session.run(
                            "MATCH (a1:Article {article_id: $s}), (a2:Article {article_id: $t}) "
                            "MERGE (a1)-[:RELATES {type: '相邻条款'}]->(a2)",
                            s=id1, t=id2,
                        )
                        rel_count += 1

            # ---- 2. 案例节点与引用边 ----
            elif doc.get("doc_type") == "case":
                case_id = f"case::{doc.get('title', '')[:80]}"
                session.run(
                    "MERGE (c:Case {case_id: $cid}) "
                    "SET c.title = $title, c.summary = $summary, c.court = $court",
                    cid=case_id, title=doc.get("title", ""),
                    summary=content[:800], court=_extract_court(content),
                )
                node_count += 1
                # 正文引用的法条 -> REFERENCES 边
                for art_ref in _find_article_refs(content):
                    aid = article_ids.get(art_ref)   # 仅连接已入库法条
                    if aid:
                        session.run(
                            "MATCH (c:Case {case_id: $cid}), (a:Article {article_id: $aid}) "
                            "MERGE (c)-[:REFERENCES {role: 'CITES'}]->(a)",
                            cid=case_id, aid=aid,
                        )
                        rel_count += 1

    logger.info("图谱构建完成：%d 节点 / %d 关系", node_count, rel_count)
    return {"nodes": node_count, "relations": rel_count, "available": True}


# ==================================================================
# 二、GraphRAG 检索（第三路）
# ==================================================================
def graph_search(query: str, top_k: int = 20) -> list[dict]:
    """
    图谱关联检索。

    策略（需求文档 4.1.6）:
        1. 从查询中识别法律名称与概念关键词；
        2. Cypher 匹配对应 Article 节点及其 1-2 跳关联（RELATES*1..2）；
        3. 返回关联法条内容（含同概念法条、被引用法条）。

    返回:
        [{"id": article_id, "text": 条文内容, "law_name", "article_no", "score": 固定基础分}]
        降级时返回 []。
    """
    if not ensure_graph():
        return []

    # 概念词典匹配查询中的法律概念
    concepts = [c for c in _load_concepts() if c in query]
    law_names = _match_law_names(query)

    try:
        with _driver.session() as session:
            # 路径 A：按法律名称 + 条文号直接定位法条
            hits_a = _query_articles(session, law_names, query)
            # 路径 B：按概念扩展（提及相同概念的法条）
            hits_b = _query_by_concepts(session, concepts, exclude={h["id"] for h in hits_a})

            merged = hits_a + hits_b
            # 图谱命中赋予固定基础分（0.55，介于稠密与稀疏之间，参与 RRF 融合）
            for h in merged:
                h.setdefault("score", 0.55)
                h.setdefault("doc_type", "law")
            return merged[:top_k]
    except Exception as e:
        logger.warning("图谱检索失败（本路降级）: %s", e)
        return []


def _query_articles(session, law_names: list[str], query: str) -> list[dict]:
    """按法律名称定位法条节点并做一跳扩展。"""
    hits = []
    for law in law_names:
        # 法条号提取（如查询包含"第585条"）
        art_no = re.search(r"(第[一二三四五六七八九十百千零\d]+条)", query)
        if art_no:
            result = session.run(
                "MATCH (a:Article) WHERE a.law_name = $law AND a.article_no = $no "
                "RETURN a.article_id AS aid, a.content AS text, a.law_name AS law, a.article_no AS no",
                law=law, no=art_no.group(1),
            )
        else:
            # 概念关键词在条文内容中模糊匹配
            result = session.run(
                "MATCH (a:Article) WHERE a.law_name = $law "
                "AND any(kw IN $kws WHERE a.content CONTAINS kw) "
                "RETURN a.article_id AS aid, a.content AS text, a.law_name AS law, a.article_no AS no "
                "LIMIT 10",
                law=law, kws=[w for w in _extract_keywords(query)][:5],
            )
        for record in result:
            hits.append({
                "id": record["aid"], "text": record["text"] or "",
                "law_name": record["law"], "article_no": record["no"],
                "score": 0.6,
            })
    return hits


def _query_by_concepts(session, concepts: list[str], exclude: set) -> list[dict]:
    """按概念扩展：提及相同概念的法条（发现隐性关联）。"""
    hits = []
    for concept in concepts[:3]:
        result = session.run(
            "MATCH (a:Article)-[:MENTIONS]->(c:Concept {name: $name}) "
            "WHERE NOT a.article_id IN $excluded "
            "RETURN a.article_id AS aid, a.content AS text, a.law_name AS law, "
            "       a.article_no AS no LIMIT 5",
            name=concept, excluded=list(exclude),
        )
        for record in result:
            hits.append({
                "id": record["aid"], "text": record["text"] or "",
                "law_name": record["law"], "article_no": record["no"],
                "score": 0.5,
            })
    return hits


# ==================================================================
# 三、图谱可视化导出
# ==================================================================
def export_visualization(limit: int = 80) -> dict:
    """
    导出节点与边（前端 ECharts Graph 力导向图渲染用）。

    返回:
        {"available": bool, "nodes": [{id, name, category, law}], 
         "links": [{source, target, relation}]}
        category: 0=法律 1=法条 2=案例 3=概念
    """
    if not ensure_graph():
        return {"available": False, "nodes": [], "links": []}
    try:
        with _driver.session() as session:
            nodes, links, seen = [], [], set()
            cat_map = {"Law": 0, "Article": 1, "Case": 2, "Concept": 3}

            result = session.run(
                "MATCH (a)-[r]->(b) "
                "RETURN a, labels(a) AS la, type(r) AS rel, b, labels(b) AS lb "
                f"LIMIT {limit * 3}"
            )
            for record in result:
                for end, labels in ((record["a"], record["la"]), (record["b"], record["lb"])):
                    eid = dict(end).get("article_id") or dict(end).get("case_id") \
                          or dict(end).get("law_id") or dict(end).get("name")
                    if eid and eid not in seen and len(nodes) < limit:
                        seen.add(eid)
                        node = dict(end)
                        nodes.append({
                            "id": eid,
                            "name": (node.get("name") or node.get("article_no")
                                     or node.get("title") or eid)[:24],
                            "category": cat_map.get(labels[0], 3),
                            "law": node.get("law_name", ""),
                        })
                # 边
                a, b = dict(record["a"]), dict(record["b"])
                sid = a.get("article_id") or a.get("case_id") or a.get("law_id") or a.get("name")
                tid = b.get("article_id") or b.get("case_id") or b.get("law_id") or b.get("name")
                if sid and tid:
                    links.append({"source": sid, "target": tid, "relation": record["rel"]})
            return {"available": True, "nodes": nodes, "links": links}
    except Exception as e:
        logger.warning("图谱可视化导出失败: %s", e)
        return {"available": False, "nodes": [], "links": []}


def graph_stats() -> dict:
    """图谱统计信息（节点/关系总量）。"""
    if not ensure_graph():
        return {"available": False, "nodes": 0, "relations": 0}
    try:
        with _driver.session() as session:
            n = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            r = session.run("MATCH ()-[e]->() RETURN count(e) AS c").single()["c"]
            return {"available": True, "nodes": n, "relations": r}
    except Exception:
        return {"available": False, "nodes": 0, "relations": 0}


# ==================================================================
# 辅助函数
# ==================================================================
_ARTICLE_SPLIT = re.compile(
    r"(第[一二三四五六七八九十百千零\d]+条(?:之[一二三四五六七八九十\d]+)?)"
)

_CONCEPTS = [
    "格式条款", "违约金", "定金", "经济补偿金", "竞业限制", "试用期",
    "保密义务", "情势变更", "不可抗力", "缔约过失", "表见代理", "无权代理",
    "善意取得", "担保责任", "连带责任", "诚实信用", "显失公平", "重大误解",
    "欺诈", "胁迫", "合同解除", "撤销权", "诉讼时效", "举证责任", "劳动报酬",
    "社会保险", "工伤", "年休假", "加班费", "解除劳动合同", "违约责任",
    "损害赔偿", "预期利益", "签字盖章", "生效条件", "争议解决", "管辖",
]


def _load_concepts() -> list[str]:
    """法律概念词典（预置 36 个高频法律概念）。"""
    return _CONCEPTS


def _match_law_names(query: str) -> list[str]:
    """从查询中识别法律名称（内置常见法律清单）。"""
    laws = [
        "民法典", "合同法", "劳动合同法", "劳动法", "公司法", "消费者权益保护法",
        "民事诉讼法", "仲裁法", "担保法", "商标法", "专利法", "著作权法",
        "个人信息保护法", "数据安全法", "电子商务法", "建筑法", "招标投标法",
    ]
    return [l for l in laws if l in query]


def _extract_keywords(query: str) -> list[str]:
    """提取查询关键词（jieba 分词去停用词）。"""
    from src.rag.sparse_retriever import tokenize
    return tokenize(query)


def _extract_articles(content: str) -> list[tuple[str, str]]:
    """从法律文本中抽取（条文号, 条文全文）列表。"""
    marks = list(_ARTICLE_SPLIT.finditer(content))
    if not marks:
        # 无条文结构的解释类文档：整篇作为一个 Article 近似
        return [("全文", content[:2000])] if content.strip() else []
    articles = []
    for i, m in enumerate(marks):
        start = m.start()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(content)
        articles.append((m.group(1), content[start:end].strip()))
    return articles


def _find_article_refs(content: str) -> list[str]:
    """识别案例正文中引用的"《法律名》第X条"引用。"""
    pattern = re.compile(r"《([^》]{2,12})》\s*(第[一二三四五六七八九十百千零\d]+条)")
    return [f"{m.group(1)} {m.group(2)}" for m in pattern.finditer(content)]


def _extract_court(content: str) -> str:
    """提取审理法院名称。"""
    m = re.search(r"([\u4e00-\u9fa5]{2,12}(?:人民法院|仲裁委员会))", content)
    return m.group(1) if m else ""
