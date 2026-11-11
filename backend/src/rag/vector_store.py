# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 稠密向量检索器（ChromaDB + bge-large-zh-v1.5）
==================================================================
模块职责：
    1. 管理法律知识库向量索引（ChromaDB 持久化 + HNSW 近似最近邻）；
    2. 文档块批量入库（embedding 批量生成 + 自动分批）；
    3. 语义检索（余弦相似度 Top-K + 时效性元数据过滤）；
    4. 索引自动重建：向量维度变更检测（集合维度与当前 Embedding 维度
       不一致时自动清空重建，V6.0 关键能力）。

元数据 Schema（用于检索过滤）:
    doc_id / doc_type / title / law_name / article_no / effectiveness
==================================================================
"""
import logging
import asyncio
import uuid

import chromadb
from chromadb.config import Settings

from src import config
from src.core.embedding import embedding_service

logger = logging.getLogger("zhifatong.rag.dense")

COLLECTION = "legal_knowledge"      # 集合名（对应需求文档 CHROMA_CONFIG）


class DenseRetriever:
    """ChromaDB 稠密检索器。"""

    def __init__(self):
        # 持久化客户端：本地磁盘模式，容器内挂载 /app/data/chroma 卷
        self._client = chromadb.PersistentClient(
            path=str(config.CHROMA_DIR),
            settings=Settings(anonymized_telemetry=False, allow_reset=True),
        )
        self._collection = None

    # ------------------------------------------------------------------
    # 集合管理
    # ------------------------------------------------------------------
    def _get_collection(self):
        """懒加载集合；维度变更时自动重建。"""
        if self._collection is not None:
            return self._collection
        try:
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION,
                metadata={"hnsw:space": "cosine"},   # 余弦距离（与 L2 归一化向量兼容）
            )
            self._check_dimension_consistency()
        except Exception as e:
            logger.error("Chroma 集合初始化失败: %s", e)
        return self._collection

    def _check_dimension_consistency(self) -> None:
        """
        向量维度一致性检测（索引自动重建机制）。

        场景: Embedding 模型切换导致维度变化（如 768 -> 1024），
              旧索引无法与新查询向量匹配（Chroma 报维度错误）。
        策略: 抽样一条已有向量对比当前 Embedding 服务维度，不一致则清空重建。
        """
        try:
            if self._collection is None or self._collection.count() == 0:
                return
            sample = self._collection.get(limit=1, include=["embeddings"])
            emb = sample.get("embeddings")
            # numpy ndarray / list 双兼容判空（不能直接对 ndarray 使用 not）
            if emb is None:
                return
            try:
                if len(emb) == 0:
                    return
            except TypeError:
                return
            old_dim = len(emb[0])
            cur_dim = int(embedding_service.dimension)
            if old_dim != cur_dim:
                logger.warning(
                    "向量维度变更检测: 索引=%d 维，当前服务=%d 维 -> 自动重建索引",
                    old_dim, cur_dim,
                )
                self._client.delete_collection(COLLECTION)
                self._collection = self._client.get_or_create_collection(
                    name=COLLECTION, metadata={"hnsw:space": "cosine"},
                )
        except Exception as e:
            logger.warning("维度一致性检测异常: %s", e)

    def count(self) -> int:
        """
        当前索引块数量。

        自愈：多进程部署（uvicorn --workers 2）下，其他进程的全量重建
        会删除并新建 Chroma 集合，本进程缓存的旧集合句柄会失效
        （"Collection ... does not exist"）。此处失效即重挂载重试一次。
        """
        for attempt in (1, 2):
            coll = self._get_collection()
            if coll is None:
                return 0
            try:
                return coll.count()
            except Exception as e:
                self._collection = None     # 句柄失效，置空触发重新挂载
                if attempt == 2:
                    logger.error("向量集合计数失败（已重试）: %s", e)
                    return 0
                logger.warning("向量集合句柄失效，重挂载后重试: %s", e)
        return 0

    def reset(self) -> None:
        """清空索引（知识库全量重建用）。"""
        try:
            self._client.delete_collection(COLLECTION)
        except Exception:
            pass
        self._collection = None
        self._get_collection()

    # ------------------------------------------------------------------
    # 入库
    # ------------------------------------------------------------------
    async def add_chunks(self, chunks: list[dict]) -> int:
        """
        批量写入文档块。

        参数:
            chunks: chunker 产出的块列表（含 text + 元数据）

        返回:
            成功写入数量
        """
        if not chunks:
            return 0
        coll = self._get_collection()
        if coll is None:
            return 0

        # 批量生成向量（embedding_service 内部自动分批）
        vectors = await embedding_service.embed([c["text"] for c in chunks])
        batch_size = 128                     # Chroma 单批写入上限（自控）
        # 集合句柄失效（其他进程重建）时重挂载重试一次
        for attempt in (1, 2):
            try:
                written = 0
                for i in range(0, len(chunks), batch_size):
                    batch_chunks = chunks[i:i + batch_size]
                    batch_vecs = vectors[i:i + batch_size]
                    ids = [str(uuid.uuid4()) for _ in batch_chunks]
                    metadatas = [_clean_meta(c) for c in batch_chunks]
                    documents = [c["text"] for c in batch_chunks]
                    coll.add(ids=ids, embeddings=batch_vecs,
                             documents=documents, metadatas=metadatas)
                    written += len(batch_chunks)
                logger.info("向量索引写入 %d 块（当前总量 %d）", written, coll.count())
                return written
            except Exception as e:
                self._collection = None
                coll = self._get_collection()
                if coll is None or attempt == 2:
                    raise
                logger.warning("向量写入集合句柄失效，重挂载后重试: %s", e)
                continue
        return 0

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    async def search(self, query: str, top_k: int = 20,
                     where: dict | None = None) -> list[dict]:
        """
        稠密语义检索。

        参数:
            query: 查询文本
            top_k: 召回数量
            where: Chroma 元数据过滤条件（如 {"effectiveness": "有效"}）

        返回:
            [{"id": 块ID, "text": 块文本, "score": 余弦相似度(0-1), ...元数据}]
            无索引 / 服务异常时返回空列表（降级由上层 RRF 兜底）。
        集合句柄失效（其他进程重建）时自动重挂载重试一次。
        """
        for attempt in (1, 2):
            coll = self._get_collection()
            if coll is None:
                return []
            try:
                total = coll.count()
                if total == 0:
                    return []
                query_vec = await embedding_service.embed_query(query)
                # 召回 top_k*3 倍候选，为元数据过滤与 RRF 融合留余量
                result = coll.query(
                    query_embeddings=[query_vec],
                    n_results=min(top_k * 3, total),
                    where=where,
                    include=["documents", "metadatas", "distances"],
                )
                hits = []
                docs = result.get("documents", [[]])[0]
                metas = result.get("metadatas", [[]])[0]
                dists = result.get("distances", [[]])[0]
                ids = result.get("ids", [[]])[0]
                for cid, doc, meta, dist in zip(ids, docs, metas, dists):
                    # Chroma cosine 距离 = 1 - 余弦相似度
                    similarity = max(0.0, 1.0 - float(dist))
                    if similarity < config.SIMILARITY_THRESHOLD:
                        continue
                    hit = {"id": cid, "text": doc, "score": similarity}
                    if meta:
                        hit.update({k: v for k, v in meta.items() if v is not None})
                    hits.append(hit)
                return hits[:top_k]
            except Exception as e:
                self._collection = None     # 句柄失效，置空触发重新挂载
                if attempt == 2:
                    logger.error("稠密检索失败（已重试）: %s", e)
                    return []
                logger.warning("稠密检索集合句柄失效，重挂载后重试: %s", e)
        return []


def _clean_meta(chunk: dict) -> dict:
    """清洗元数据（Chroma 不接受 None 值）。"""
    keys = ("doc_id", "doc_type", "title", "law_name",
            "article_no", "effectiveness", "chunk_index")
    return {k: str(chunk.get(k, "") or "") for k in keys}


# 全局单例
dense_retriever = DenseRetriever()
