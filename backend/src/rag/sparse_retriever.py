# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 稀疏检索器（BM25 + jieba 分词）
==================================================================
模块职责：
    1. 基于内存 BM25 索引的关键词检索（精确法条号/专有名词匹配通道，
       补充稠密检索的语义漂移问题）；
    2. jieba 中文分词 + 停用词过滤；
    3. 索引与 Chroma 矢量库同步构建/重建（由 knowledge_service 驱动）。

算法说明（BM25 Okapi）:
    score(q, d) = Σ_t IDF(t) * (tf * (k1+1)) / (tf + k1*(1-b+b*|d|/avgdl))
    参数 k1=1.5（词频饱和度）、b=0.75（长度归一化）为经典经验值。

自研原因：
    rank_bm25 包无法写入系统 site-packages（沙箱限制），
    自研实现约 60 行且可针对法律文本定制（法条号加权）。
==================================================================
"""
import logging
import math
import re
from collections import Counter

import jieba

logger = logging.getLogger("zhifatong.rag.sparse")

# 中文停用词（法律检索场景精简版）
_STOPWORDS = set("""
的 了 和 是 就 都 而 与 及 着 或 一个 没有 我们 你们 他们 它们
对此 关于 上述 可以 应当 不得 按照 根据 依据 为了 对于 予以
""".split())

# 法条编号模式（第X条）：分词后作为强信号 token 保留
_ARTICLE_TOKEN = re.compile(r"第[一二三四五六七八九十百千零\d]+条")

K1 = 1.5      # 词频饱和参数
B = 0.75      # 长度归一化参数


def tokenize(text: str) -> list[str]:
    """
    法律文本分词（jieba + 停用词过滤 + 法条号保留）。

    说明:
        jieba 对"第四百九十七条"会切碎，因此先正则提取法条号
        作为整体 token，再对剩余文本分词。
    """
    tokens = []
    # 提取法条编号作为强信号
    for m in _ARTICLE_TOKEN.finditer(text):
        tokens.append(m.group(0))
    # 常规分词
    for word in jieba.lcut(text):
        word = word.strip()
        if not word or word in _STOPWORDS or len(word) < 2:
            continue
        if re.match(r"^\d+$", word):        # 纯数字无检索意义
            continue
        tokens.append(word)
    return tokens


class BM25Index:
    """
    内存 BM25 索引。

    结构:
        self._docs:    [块字典]（与 Chroma 入库块保持一致）
        self._tokens:  [分词后的 token 列表]
        self._df:      token -> 文档频率
        self._avgdl:   平均文档长度
    """

    def __init__(self):
        self._docs: list[dict] = []
        self._tokens: list[list[str]] = []
        self._df: Counter = Counter()
        self._avgdl: float = 0.0

    # ------------------------------------------------------------------
    # 索引构建
    # ------------------------------------------------------------------
    def build(self, chunks: list[dict]) -> int:
        """
        全量构建索引（先清空）。

        参数:
            chunks: 与 Chroma 相同的块列表

        返回:
            索引块数量
        """
        self._docs, self._tokens, self._df = [], [], Counter()
        for chunk in chunks:
            tokens = tokenize(chunk["text"])
            self._docs.append(chunk)
            self._tokens.append(tokens)
            for t in set(tokens):
                self._df[t] += 1
        self._avgdl = (
            sum(len(t) for t in self._tokens) / len(self._tokens)
            if self._tokens else 0.0
        )
        logger.info("BM25 索引构建完成：%d 块，词表 %d", len(self._docs), len(self._df))
        return len(self._docs)

    def __len__(self) -> int:
        return len(self._docs)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------
    def search(self, query: str, top_k: int = 20) -> list[dict]:
        """
        BM25 关键词检索。

        参数:
            query: 查询文本
            top_k: 返回条数

        返回:
            [{"id", "text", "score": BM25分, ...元数据}]，按分数降序
        """
        if not self._docs:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        scores = []
        n_docs = len(self._docs)
        for idx, doc_tokens in enumerate(self._tokens):
            score = self._score(q_tokens, doc_tokens, idx, n_docs)
            scores.append((idx, score))

        # 过滤零分并取 Top-K
        scores = [(i, s) for i, s in scores if s > 0]
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in scores[:top_k]:
            hit = dict(self._docs[idx])
            hit["score"] = float(score)
            results.append(hit)
        return results

    def _score(self, q_tokens: list[str], doc_tokens: list[str],
               doc_idx: int, n_docs: int) -> float:
        """
        计算 BM25 Okapi 得分。

        IDF 公式: log((N - df + 0.5) / (df + 0.5) + 1)（平滑，避免负值）
        法条号 token 双倍加权（用户查询"第585条"时应精准命中）。
        """
        tf_map = Counter(doc_tokens)
        dl = len(doc_tokens)
        avgdl = self._avgdl or 1.0
        score = 0.0
        for t in q_tokens:
            tf = tf_map.get(t, 0)
            if tf == 0:
                continue
            df = self._df.get(t, 0)
            idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)
            numerator = tf * (K1 + 1)
            denominator = tf + K1 * (1 - B + B * dl / avgdl)
            weight = 2.0 if _ARTICLE_TOKEN.match(t) else 1.0   # 法条号强信号
            score += weight * idf * numerator / denominator
        return score


# 全局单例
bm25_index = BM25Index()
