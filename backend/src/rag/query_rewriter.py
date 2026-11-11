# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 查询优化器（查询重写 + 子查询分解 + 指代消解）
==================================================================
模块职责：
    1. 指代消解：结合对话历史把"它/该公司/上述条款"等代词还原为实体；
    2. 查询重写：口语化问题 -> 精确法律检索查询（补全法律术语）；
    3. 子查询分解：复杂问题拆解为多个单一法律要点的子问题；
    4. 复杂度判定：短问题跳过分解，节省 LLM 调用延迟。

降级策略：LLM 不可用时返回原始查询（检索链路继续可用）。
==================================================================
"""
import logging
import re

from src.core.llm_client import llm_client

logger = logging.getLogger("zhifatong.rag.query")

# 指代词模式（多轮对话场景）
_PRONOUN_PATTERN = re.compile(r"(它|该|此|上述|这家|该公?司?|上述条款|这个问题)")


class QueryOptimizer:
    """法律查询优化器。"""

    REWRITE_PROMPT = """你是法律查询优化专家。请把用户的口语化法律问题改写为更精确的法律检索查询。

规则：
1. 补全法律术语（如"辞退补偿"→"违法解除劳动合同的经济补偿金"）
2. 需要时补充相关法律领域关键词（如"劳动合同法""民法典"）
3. 严格保留用户原始意图，不改问题核心
4. 只输出改写后的查询本身，不要任何解释

原始问题：{query}
改写查询："""

    DECOMPOSE_PROMPT = """你是法律问题分析专家。请把复杂法律问题拆解为多个独立可检索的子问题。

规则：
1. 每个子问题聚焦单一法律要点
2. 子问题合起来需覆盖原问题的全部方面
3. 输出 JSON 数组格式：["子问题1", "子问题2"]

原始问题：{query}
JSON 输出："""

    # ------------------------------------------------------------------
    # 指代消解
    # ------------------------------------------------------------------
    def resolve_coreference(self, question: str, history: list[dict]) -> str:
        """
        结合对话历史消解指代。

        参数:
            question: 当前问题
            history: [{"role": "user"|"assistant", "content": "..."}]

        策略:
            检测到指代词且存在历史时，把最近一轮问答的核心实体
            拼接进查询（轻量规则法，避免额外 LLM 调用延迟）。
        """
        if not history or not _PRONOUN_PATTERN.search(question):
            return question
        # 取最近一条用户消息（含原始实体表述）
        recent_user = next(
            (h["content"] for h in reversed(history) if h.get("role") == "user"), ""
        )
        if not recent_user:
            return question
        # 提取上一问中的引号/书名号实体与关键词
        entities = re.findall(r"《([^》]{2,20})》", recent_user)
        if entities:
            return f"{question}（关联：{'、'.join(entities[:2])}）"
        # 无显式实体时拼接上一问的关键片段（前 30 字）
        return f"{question}（承接上文：{recent_user[:30]}）"

    # ------------------------------------------------------------------
    # 查询重写
    # ------------------------------------------------------------------
    async def rewrite(self, question: str) -> str:
        """
        LLM 查询重写。

        性能: 重写输出为一行短查询，收紧 max_tokens 防止小模型
        跑题输出长篇解释（曾导致整条问答链路增加 10s+ 延迟）。

        返回:
            重写后的查询；失败/为空时返回原查询（降级）。
        """
        result = await llm_client.generate(
            self.REWRITE_PROMPT.format(query=question),
            system="你是法律检索查询优化助手，输出精炼。",
            temperature=0.1,
            max_tokens=120,
        )
        # 清洗：去换行、限长
        if result:
            result = result.strip().splitlines()[0][:200]
            if len(result) >= 6:      # 过短视为失败
                return result
        return question

    # ------------------------------------------------------------------
    # 子查询分解
    # ------------------------------------------------------------------
    async def decompose(self, question: str) -> list[str]:
        """
        复杂问题子查询分解。

        性能: 子查询 JSON 输出很短，收紧 max_tokens 且失败不重试
        （重试一次等于翻倍延迟，降级单查询对检索质量影响有限）。

        返回:
            子查询列表；LLM 失败返回 [question]（降级单查询）。
        """
        raw = await llm_client.generate_json(
            self.DECOMPOSE_PROMPT.format(query=question),
            system="你只输出 JSON 数组。", retry=0, default=None,
            max_tokens=256,
        )
        # generate_json 已完成解析（list / dict / None），此处仅做结构校验
        if isinstance(raw, list) and raw and all(isinstance(s, str) for s in raw):
            subs = [s.strip() for s in raw if 4 <= len(s.strip()) <= 200][:4]
            if subs:
                return subs
        return [question]

    # ------------------------------------------------------------------
    # 复杂度判定（是否需要分解）
    # ------------------------------------------------------------------
    @staticmethod
    def is_complex(question: str) -> bool:
        """
        复杂查询启发式判定。

        判定规则（满足其一即复杂）:
            1. 含复合连词（"以及/同时/还有/并且/又/而且"）；
            2. 含疑问拆分词（"分别/各自/几个问题"）；
            3. 长度超过 40 字符。
        """
        if any(w in question for w in ("以及", "同时", "还有", "并且", "而且", "分别", "各自")):
            return True
        return len(question) > 40


# 全局单例
query_optimizer = QueryOptimizer()
