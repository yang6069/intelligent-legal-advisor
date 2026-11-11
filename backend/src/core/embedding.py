# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | Embedding 服务（bge-large-zh-v1.5 @ Ollama）
==================================================================
模块职责：
    1. 调用 Ollama /api/embed 批量生成文本向量（1024 维，中文法律优化）；
    2. 向量维度探测（首次调用时自动检测，支持索引自动重建）；
    3. 降级容错：Ollama 嵌入不可用时回退到本地哈希向量（保证系统可用）。

设计要点：
    - 批量接口：一次请求最多 64 条文本，超出自动分批；
    - 哈希兜底向量：分词后对 token 哈希映射到固定维度桶，
      召回质量虽降但系统不中断（V6.0 可用性要求）。
==================================================================
"""
import hashlib
import logging

import httpx

from src import config

logger = logging.getLogger("zhifatong.embedding")

_BATCH_SIZE = 64          # Ollama 批量嵌入单次上限


class EmbeddingService:
    """文本向量化服务（Ollama bge-large-zh-v1.5 + 哈希兜底）。"""

    def __init__(self):
        self.model = config.EMBEDDING_MODEL
        self.dim = config.EMBEDDING_DIM
        self._http = httpx.AsyncClient(base_url=config.OLLAMA_BASE_URL, timeout=60)
        self._mode: str | None = None      # "ollama" | "hash"（懒探测缓存）
        self._actual_dim: int | None = None  # 实际向量维度（探测后确定）

    # ------------------------------------------------------------------
    # 可用性探测（启动自检）
    # ------------------------------------------------------------------
    async def health_check(self) -> bool:
        """探测 Ollama 嵌入模型是否可用，并顺带探测实际维度。"""
        try:
            vec = await self._embed_ollama(["维度探测"])
            self._mode = "ollama" if vec else "hash"
            return self._mode == "ollama"
        except Exception as e:
            logger.warning("Ollama 嵌入服务不可用（将降级哈希向量）: %s", e)
            self._mode = "hash"
            return False

    @property
    def mode(self) -> str:
        """当前嵌入模式（ollama=专业模型 / hash=降级兜底）。"""
        return self._mode or "ollama"

    @property
    def dimension(self) -> int:
        """实际向量维度（探测后返回真实值）。"""
        return self._actual_dim or config.EMBEDDING_DIM

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """
        批量文本向量化。

        参数:
            texts: 文本列表

        返回:
            与输入等长的向量列表（每条 1024 维，L2 归一化后可与余弦相似度等价）
        """
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i:i + _BATCH_SIZE]
            if self._mode != "hash":
                try:
                    vecs = await self._embed_ollama(batch)
                    if vecs:
                        vectors.extend(vecs)
                        continue
                except Exception as e:
                    logger.warning("批量嵌入失败，本批降级哈希向量: %s", e)
            vectors.extend(self._embed_hash(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """单条查询向量化（与文档同模型，保证语义空间一致）。"""
        vecs = await self.embed([text])
        return vecs[0] if vecs else self._embed_hash([text])[0]

    # ------------------------------------------------------------------
    # Ollama 专业嵌入
    # ------------------------------------------------------------------
    async def _embed_ollama(self, texts: list[str]) -> list[list[float]] | None:
        """
        调用 Ollama /api/embed。

        请求: {"model": ..., "input": [text, ...]}
        响应: {"embeddings": [[...], ...]}
        """
        resp = await self._http.post("/api/embed", json={
            "model": self.model, "input": texts,
        })
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings")
        if not embeddings:
            return None
        # 记录实际维度（向量维度变更检测，支持索引自动重建）
        if self._actual_dim is None and embeddings:
            self._actual_dim = len(embeddings[0])
            logger.info("Embedding 实际维度: %d", self._actual_dim)
        return embeddings

    # ------------------------------------------------------------------
    # 哈希兜底向量（降级模式）
    # ------------------------------------------------------------------
    def _embed_hash(self, texts: list[str]) -> list[list[float]]:
        """
        本地哈希词袋向量（降级方案）。

        算法:
            1. jieba 分词；
            2. 每个词 MD5 哈希 -> 维度桶索引，词权重 = 1/sqrt(df)（近似 IDF）；
            3. 累加后 L2 归一化。

        说明:
            召回质量远逊于 BGE，但保证 Ollama 宕机时检索链路可用。
        """
        import jieba
        dim = config.EMBEDDING_DIM
        results = []
        for text in texts:
            vec = [0.0] * dim
            tokens = [t for t in jieba.lcut(text) if len(t) > 1]
            for token in set(tokens):
                # MD5 哈希取模确定桶位置（确定性，查询与文档映射一致）
                bucket = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim
                vec[bucket] += 1.0
            # L2 归一化（与余弦相似度兼容）
            norm = sum(v * v for v in vec) ** 0.5
            if norm > 0:
                vec = [v / norm for v in vec]
            results.append(vec)
        return results


# 全局单例
embedding_service = EmbeddingService()
