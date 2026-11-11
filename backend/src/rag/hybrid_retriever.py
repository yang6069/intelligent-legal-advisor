# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 三路混合检索器（稠密 + 稀疏 + 图谱，RRF 融合 + 精排）
==================================================================
模块职责：
    需求文档 4.2.3 的核心检索组件——两阶段检索架构：
    第一阶段（粗排）:
        1. 稠密检索: ChromaDB HNSW 语义召回（bge-large-zh-v1.5）
        2. 稀疏检索: BM25 关键词召回（法条号强信号加权）
        3. 图谱检索: Neo4j 关联扩展（可选，降级容错）
        4. RRF 倒数排名融合三路结果（k=60）
    第二阶段（精排）:
        5. Cross-Encoder 精排（my-rerank 容器，bge-reranker-v2-m3）
           一次批量调用，输出 Top-N（默认 5）
==================================================================
"""
import logging
import time

from src import config
from src.core.reranker import rerank_service
from src.rag.sparse_retriever import bm25_index
from src.rag.vector_store import dense_retriever
from src.rag import graph_store

logger = logging.getLogger("zhifatong.rag.hybrid")


class HybridRetriever:
    """三路混合检索器。"""

    def __init__(self):
        self.dense = dense_retriever
        self.sparse = bm25_index
        self.graph_enabled = config.GRAPH_SEARCH_ENABLED

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    async def search(self, query: str, top_k: int | None = None,
                     rerank_top_n: int | None = None,
                     enable_graph: bool = True) -> dict:
        """
        完整两阶段检索。

        参数:
            query: 查询文本（通常为重写后的查询）
            top_k: 粗排各路召回数量（默认 config.RECALL_TOP_K）
            rerank_top_n: 精排输出数量（默认 config.RERANK_TOP_N）
            enable_graph: 是否启用图谱第三路

        返回:
            {
              "results": 精排后文档列表（含 rerank_score）,
              "stats": {
                "dense_hits", "sparse_hits", "graph_hits",
                "fused_count", "latency_ms", "graph_available"
              }
            }
        """
        t0 = time.perf_counter()
        top_k = top_k or config.RECALL_TOP_K
        rerank_top_n = rerank_top_n or config.RERANK_TOP_N

        # ===== 第一阶段：三路召回 =====
        # 1) 稠密检索（异步：embedding 经 Ollama 生成）
        dense_hits = await self.dense.search(query, top_k=top_k)

        # 2) 稀疏检索（内存 BM25，微秒级）
        sparse_hits = self.sparse.search(query, top_k=top_k)

        # 3) 图谱关联检索（同步 Cypher；降级返回 []）
        graph_hits = []
        graph_available = False
        if enable_graph and self.graph_enabled:
            graph_hits = graph_store.graph_search(query, top_k=top_k)
            graph_available = bool(graph_hits)

        # ===== RRF 融合 =====
        fused = self.reciprocal_rank_fusion(
            [dense_hits, sparse_hits, graph_hits], k=config.RRF_K
        )

        # ===== 第二阶段：Cross-Encoder 精排 =====
        results = await rerank_service.rerank(
            query=query, documents=fused[:max(rerank_top_n * 4, 20)],
            top_n=rerank_top_n,
        ) if fused else []

        latency = (time.perf_counter() - t0) * 1000
        stats = {
            "dense_hits": len(dense_hits),
            "sparse_hits": len(sparse_hits),
            "graph_hits": len(graph_hits),
            "fused_count": len(fused),
            "latency_ms": round(latency, 1),
            "graph_available": graph_available,
        }
        logger.info(
            "混合检索完成: 稠密 %d / 稀疏 %d / 图谱 %d -> 融合 %d -> 精排 %d, 耗时 %.0fms",
            len(dense_hits), len(sparse_hits), len(graph_hits),
            len(fused), len(results), latency,
        )
        return {"results": results, "stats": stats}

    # ------------------------------------------------------------------
    # RRF 融合算法
    # ------------------------------------------------------------------
    @staticmethod
    def reciprocal_rank_fusion(result_lists: list[list[dict]],
                               k: int = 60) -> list[dict]:
        """
        倒数排名融合（Reciprocal Rank Fusion）。

        公式: score(d) = Σ_i  1 / (k + rank_i(d) + 1)

        参数:
            result_lists: 多路检索结果列表（各路内部已按相关度降序）
            k: 平滑参数（60 为经典经验值，抑制头部结果独大）

        返回:
            融合去重后的文档列表（按 RRF 分数降序，附 rrf_score 字段）

        说明:
            RRF 只依赖排名而非分值分布，天然兼容三路异构评分体系
            （余弦相似度 / BM25 分 / 图谱基础分）。
        """
        scores: dict[str, dict] = {}
        for results in result_lists:
            for rank, doc in enumerate(results):
                doc_id = str(doc.get("id") or doc.get("text", "")[:64])
                if doc_id not in scores:
                    # 首次出现的文档建立条目（保留原始最高信息量的文档副本）
                    scores[doc_id] = {"score": 0.0, "doc": dict(doc)}
                scores[doc_id]["score"] += 1.0 / (k + rank + 1)

        ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        for item in ranked:
            item["doc"]["rrf_score"] = round(item["score"], 6)
        return [item["doc"] for item in ranked]


# 全局单例
hybrid_retriever = HybridRetriever()
