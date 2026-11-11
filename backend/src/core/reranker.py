# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | Reranker 精排服务客户端（bge-reranker-v2-m3）
==================================================================
模块职责：
    1. 调用 my-rerank 容器（http://localhost:9999/rerank）做 Cross-Encoder 精排；
    2. 一次 HTTP 请求完成 20 候选批量打分（约 0.5s，替代 LLM 逐条 10s+）；
    3. 降级容错：服务不可用时回退到稠密检索相似度排序（保持链路可用）。

接口契约（my-rerank 容器）:
    POST /rerank
    {"query": "...", "documents": ["...", ...], "top_n": N}
    ->
    {"results": [{"index": 0, "score": 0.71, "document": "..."}], "total": 3,
     "model": "BAAI/bge-reranker-v2-m3"}
==================================================================
"""
import logging
import math

import httpx

from src import config

logger = logging.getLogger("zhifatong.reranker")


_TIMEOUT = 15.0     # 精排请求超时（秒）


def _sigmoid(x: float) -> float:
    """
    Cross-Encoder 原始 logit -> 相关性概率（0-1）。

    bge-reranker-v2-m3 输出的是未归一化 logit（可为任意实数，如 1.15、-3.2），
    直接透传会导致前端显示"相关度 115%"这类离谱数值；
    过 sigmoid 后映射到 (0, 1) 区间，语义即"该文档与查询相关的概率"。
    对超大负值做分段保护，避免 math.exp 溢出。
    """
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


class RerankService:
    """Cross-Encoder 精排服务客户端。"""

    def __init__(self):
        self.base_url = config.RERANK_BASE_URL.rstrip("/")
        self._http = httpx.AsyncClient(timeout=_TIMEOUT)

    # ------------------------------------------------------------------
    # 可用性探测
    # ------------------------------------------------------------------
    async def health_check(self) -> bool:
        """探测精排服务是否可用（发一条最小请求）。"""
        try:
            resp = await self._http.post(f"{self.base_url}/rerank", json={
                "query": "健康探测", "documents": ["服务连通性测试"], "top_n": 1,
            })
            ok = resp.status_code == 200 and "results" in resp.json()
            logger.info("Rerank 服务探测: %s", "可用" if ok else "异常")
            return ok
        except Exception as e:
            logger.warning("Rerank 服务不可用（将降级原始相似度排序）: %s", e)
            return False

    # ------------------------------------------------------------------
    # 精排接口
    # ------------------------------------------------------------------
    async def rerank(self, query: str, documents: list[dict],
                     top_n: int | None = None) -> list[dict]:
        """
        对候选文档做 Cross-Encoder 精排。

        参数:
            query: 用户查询
            documents: 候选文档列表（每项含 text 字段与其他元数据）
            top_n: 输出条数（默认 config.RERANK_TOP_N）

        返回:
            按 rerank 分数降序的前 top_n 条文档（附 rerank_score 字段）；
            服务不可用时按原始相似度降序返回（降级路径）。
        """
        if not documents:
            return []
        top_n = top_n or config.RERANK_TOP_N
        texts = [str(d.get("text", ""))[:1500] for d in documents]   # 截断保护

        try:
            resp = await self._http.post(f"{self.base_url}/rerank", json={
                "query": query, "documents": texts, "top_n": min(top_n, len(documents)),
            })
            resp.raise_for_status()
            data = resp.json()
            results = []
            for item in data.get("results", []):
                idx = item.get("index", -1)
                if 0 <= idx < len(documents):
                    # 原始 logit -> sigmoid 概率（0-1），保证前端"相关度"百分比合法
                    doc = {**documents[idx],
                           "rerank_score": _sigmoid(float(item.get("score", 0.0)))}
                    results.append(doc)
            if results:
                return results
            # 空结果视为异常，走降级
        except Exception as e:
            logger.warning("精排调用失败（降级为相似度排序）: %s", e)

        # ---- 降级路径：按稠密相似度排序 ----
        ranked = sorted(
            documents,
            key=lambda d: d.get("score", d.get("rerank_score", 0.0)),
            reverse=True,
        )
        for d in ranked:
            # 稠密余弦相似度本身在 [-1, 1]，裁剪到 [0, 1] 保证百分比显示合法
            d["rerank_score"] = min(1.0, max(0.0, float(d.get("score", 0.0))))
        return ranked[:top_n]


# 全局单例
rerank_service = RerankService()
