# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 知识库管理服务
==================================================================
模块职责：
    1. 文档 CRUD（法律法规/案例/司法解释/合同模板四类）；
    2. 索引管线：文档 -> 法律感知分块 -> Chroma 向量入库 + BM25 索引同步
       + Neo4j 图谱写入（三存储同步）；
    3. 启动时自动索引（索引为空时全量构建）；
    4. 全量重建接口（向量维度变更 / 手动刷新）；
    5. 知识库统计（类型分布/时效性分布）。
==================================================================
"""
import logging
import time

from src import config
from src import database
from src.rag.chunker import chunk_document
from src.rag import graph_store
from src.rag.sparse_retriever import bm25_index
from src.rag.vector_store import dense_retriever

logger = logging.getLogger("zhifatong.kb")

DOC_TYPES = {"law": "法律法规", "case": "裁判案例",
             "interpretation": "司法解释", "template": "合同模板"}


class KnowledgeService:
    """法律知识库管理服务。"""

    # ------------------------------------------------------------------
    # 文档 CRUD
    # ------------------------------------------------------------------
    def list_docs(self, page: int = 1, size: int = 20, doc_type: str = "",
                  keyword: str = "") -> dict:
        """分页查询知识库文档。"""
        conds, args = ["1=1"], []
        if doc_type:
            conds.append("doc_type = %s")
            args.append(doc_type)
        if keyword:
            conds.append("(title LIKE %s OR law_name LIKE %s OR content LIKE %s)")
            args.extend([f"%{keyword}%"] * 3)
        where = " AND ".join(conds)
        args = tuple(args)
        total = database.query_one(
            f"SELECT COUNT(*) AS c FROM kb_documents WHERE {where}", args
        )["c"]
        rows = database.query_all(
            f"SELECT id, doc_type, title, law_name, article_no, effectiveness, "
            f"publish_date, char_count, indexed, source, created_at "
            f"FROM kb_documents WHERE {where} ORDER BY id DESC LIMIT %s OFFSET %s",
            args + (size, (page - 1) * size),
        )
        return {"total": total, "items": rows}

    def create_doc(self, doc_type: str, title: str, content: str,
                   law_name: str = "", article_no: str = "",
                   effectiveness: str = "有效", publish_date: str | None = None,
                   created_by: int | None = None) -> int:
        """
        新增文档并同步建立索引（向量 + BM25 + 图谱）。

        返回: 新文档 ID
        """
        if doc_type not in DOC_TYPES:
            raise ValueError(f"非法文档类型: {doc_type}")
        doc_id = database.insert_return_id(
            "INSERT INTO kb_documents (doc_type, title, law_name, article_no, content, "
            "effectiveness, publish_date, char_count, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (doc_type, title, law_name or title, article_no, content,
             effectiveness, publish_date, len(content), created_by),
        )
        # 同步索引（新文档增量入库）
        self.index_documents([self.get_doc(doc_id)])
        return doc_id

    def get_doc(self, doc_id: int) -> dict | None:
        """查询单个文档全文字段。"""
        return database.query_one(
            "SELECT * FROM kb_documents WHERE id = %s", (doc_id,)
        )

    def delete_doc(self, doc_id: int) -> None:
        """删除文档（索引全量重建由调用方决定是否触发）。"""
        database.execute("DELETE FROM kb_documents WHERE id = %s", (doc_id,))
        self.rebuild_all()

    # ------------------------------------------------------------------
    # 索引管线
    # ------------------------------------------------------------------
    async def index_documents_async(self, docs: list[dict]) -> dict:
        """异步索引文档（向量化耗时操作走事件循环）。"""
        return self._index_core(docs)

    def index_documents(self, docs: list[dict]) -> dict:
        """同步索引文档入口。"""
        return self._index_core(docs)

    def _index_core(self, docs: list[dict]) -> dict:
        """
        索引核心逻辑（三存储同步）。

        步骤:
            1. chunk_document 法律感知分块；
            2. BM25 内存索引重建（含全量块）；
            3. Chroma 向量入库（异步 embedding 由调用方保证已就绪，
               此处通过事件循环桥接）；
            4. Neo4j 图谱写入。

        返回:
            {"chunks": 块数, "vector_count": 向量库总量, "graph": {...}}
        """
        valid_docs = [d for d in docs if d]
        if not valid_docs:
            return {"chunks": 0, "vector_count": dense_retriever.count(),
                    "graph": {"available": False}}

        # 1. 分块
        all_chunks = []
        for doc in valid_docs:
            all_chunks.extend(chunk_document(doc))

        # 2. BM25 全量重建（自内存索引，重建成本低）
        all_docs_rows = database.query_all(
            "SELECT * FROM kb_documents WHERE indexed = 1 OR id IN (%s)"
            % ",".join(str(d["id"]) for d in valid_docs)
        ) if valid_docs else []
        # 简化策略：BM25 始终对全部文档重建（数据量万级以内毫秒级）
        bm25_source = database.query_all("SELECT * FROM kb_documents")
        bm25_chunks = []
        for doc in bm25_source:
            bm25_chunks.extend(chunk_document(doc))
        bm25_index.build(bm25_chunks)

        # 3. Chroma 向量入库（异步 embedding：用事件循环桥接）
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        async def _add():
            return await dense_retriever.add_chunks(all_chunks)

        if loop and loop.is_running():
            # 已在事件循环中：创建任务排队执行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                written = pool.submit(asyncio.run, _add()).result()
        else:
            written = asyncio.run(_add())

        # 标记索引完成
        for doc in valid_docs:
            database.execute(
                "UPDATE kb_documents SET indexed = 1 WHERE id = %s", (doc["id"],)
            )

        # 4. 图谱写入
        graph_result = graph_store.build_graph_from_documents(valid_docs)

        logger.info("索引完成：%d 文档 / %d 块 / 向量库 %d",
                    len(valid_docs), len(all_chunks), dense_retriever.count())
        return {"chunks": len(all_chunks), "vector_count": dense_retriever.count(),
                "graph": graph_result}

    def rebuild_all(self) -> dict:
        """全量重建三存储索引（清空 Chroma -> 全量重灌）。"""
        t0 = time.perf_counter()
        dense_retriever.reset()
        docs = database.query_all("SELECT * FROM kb_documents")
        import asyncio
        result = asyncio.run(self.index_documents_async(docs))
        result["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        return result

    def rebuild_bm25_from_db(self) -> int:
        """
        从 MySQL 全量重建本进程 BM25 内存索引（毫秒级，无需重嵌入）。

        用途：
            1. 跨进程索引同步（Redis 通知后各 worker 自建）；
            2. 进程重启后的 BM25 轻量恢复（ensure_bootstrapped 场景 2）。

        返回:
            重建后的块数量
        """
        chunks = []
        for doc in database.query_all("SELECT * FROM kb_documents"):
            chunks.extend(chunk_document(doc))
        return bm25_index.build(chunks)

    def ensure_bootstrapped(self) -> None:
        """
        启动时自动索引（应用启动钩子）。

        规则:
            1. Chroma 与 BM25 均有数据 -> 跳过（正常热重启）；
            2. Chroma 有数据但 BM25 为空（内存索引重启丢失）->
               仅重建 BM25（毫秒级，无需重嵌入）；
            3. 两者皆空 且 MySQL 有文档 -> 全量构建（首次部署即开即用）。
        """
        try:
            vector_count = dense_retriever.count()
            bm25_count = len(bm25_index)
            if vector_count > 0 and bm25_count > 0:
                logger.info("索引已存在（向量 %d 块 / BM25 %d 块），跳过自动构建",
                            vector_count, bm25_count)
                return
            doc_count = database.query_one(
                "SELECT COUNT(*) AS c FROM kb_documents"
            )["c"]
            if doc_count == 0:
                logger.info("知识库为空，等待种子数据导入")
                return
            if vector_count > 0:
                # 场景 2：仅 BM25 丢失（进程重启），轻量恢复
                logger.info("BM25 内存索引丢失（向量 %d 块健在）-> 轻量重建",
                            vector_count)
                self.rebuild_bm25_from_db()
                return
            logger.info("检测到空索引 + %d 篇文档 -> 启动全量构建", doc_count)
            stats = self.rebuild_all()
            logger.info("启动索引构建完成: %s", stats)
        except Exception as e:
            logger.error("启动索引构建失败: %s", e)

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def stats(self) -> dict:
        """知识库统计（类型分布 + 时效性分布 + 索引状态）。"""
        by_type = {
            row["doc_type"]: row["c"]
            for row in database.query_all(
                "SELECT doc_type, COUNT(*) AS c FROM kb_documents GROUP BY doc_type"
            )
        }
        by_eff = {
            row["effectiveness"]: row["c"]
            for row in database.query_all(
                "SELECT effectiveness, COUNT(*) AS c FROM kb_documents GROUP BY effectiveness"
            )
        }
        return {
            "total": sum(by_type.values()),
            "by_type": by_type,
            "by_effectiveness": by_eff,
            "vector_count": dense_retriever.count(),
            "bm25_count": len(bm25_index),
            "graph": graph_store.graph_stats(),
        }


# 全局单例
knowledge_service = KnowledgeService()
