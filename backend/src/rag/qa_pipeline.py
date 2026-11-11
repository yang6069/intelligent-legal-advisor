# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 法律 RAG 问答管线（完整链路）
==================================================================
模块职责（需求文档 6.1.1 LegalRAGPipeline 完整实现）：
    Step 1 查询预处理: 指代消解 -> 查询重写 ->（复杂）子查询分解
    Step 2 三路混合检索: 稠密 + 稀疏 + 图谱 -> RRF 融合
    Step 3 Cross-Encoder 精排: bge-reranker-v2-m3 Top-5
    Step 4 上下文构建: 精排结果拼装为带引用编号的检索上下文
    Step 5 LLM 生成: RAG 增强生成（支持 SSE 流式）
    Step 6 自评估: 法条准确性/逻辑完整性/事实一致性三维打分，
                   低置信度触发一次重检索（Self-RAG 精神）
    Step 7 引用校验: 回答中的法条引用与检索结果比对

对外接口:
    pipeline.query()        非流式完整问答（结构化响应）
    pipeline.stream_query() 流式问答（SSE 事件生成器）
==================================================================
"""
import asyncio
import json
import logging
import re
import time
import uuid

from src import config
from src.core.llm_client import llm_client
from src.rag.hybrid_retriever import hybrid_retriever
from src.rag.query_rewriter import query_optimizer
from src.utils.text import MarkdownStripper, strip_markdown

logger = logging.getLogger("zhifatong.qa.pipeline")

# 系统人设：法律专家 + RAG 约束 + 免责声明
# 【纯文本输出】禁止一切 Markdown 标记（前端与持久化均为纯文本）
SYSTEM_PROMPT = """你是"智法通"企业级智能法律顾问，一名严谨的中国法律专家。

回答规则：
1. 仅依据【检索资料】回答，不编造法条；资料未覆盖时明确说明并建议咨询执业律师
2. 引用法条时使用规范格式：《法律名》第X条，并标注其效力状态
3. 回答结构清晰：结论先行 -> 法律依据 -> 分析说明 -> 实务建议
4. 只输出纯文本：严禁使用任何 Markdown 标记（禁止 #、**、*、`、>、|、- 列表符）；
   层级结构用「一、二、三」「1. 2. 3.」等中文编号组织，段落之间用空行分隔
5. 结尾单独一行输出：以上内容由 AI 生成，仅供参考，不构成正式法律意见。"""

GENERATION_PROMPT = """请基于以下检索资料回答用户的法律问题。

【检索资料】
{context}

【用户问题】
{question}

请给出专业、准确的纯文本法律解答（不要任何 Markdown 符号）。
篇幅要求：在保证专业完整的前提下尽量精炼，直击关键结论、核心法条依据与实务建议，
不要重复罗列资料原文，总篇幅控制在 600 字以内。"""

SELF_EVAL_PROMPT = """请评估以下法律回答的质量，从三个维度打分（1-5 分）：

1. law_accuracy 法条准确性：引用法条是否真实存在且适用
2. logic_completeness 逻辑完整性：是否完整覆盖问题各方面
3. factual_consistency 事实一致性：是否与检索资料一致

【问题】{query}
【检索资料摘要】{context}
【回答】{answer}

只输出 JSON：
{{"law_accuracy": <1-5>, "logic_completeness": <1-5>, "factual_consistency": <1-5>,
  "confidence": <0-1>, "needs_reretrieval": <true/false>}}"""


class LegalQAPipeline:
    """法律 RAG 问答管线。"""

    def __init__(self):
        self.retriever = hybrid_retriever
        self.optimizer = query_optimizer
        self.max_reretrieve = 1     # 自评估触发重检索的最大次数

    # ==================================================================
    # 一、非流式完整问答
    # ==================================================================
    async def query(self, question: str, history: list[dict] | None = None,
                    enable_graph: bool = True) -> dict:
        """
        完整 RAG 问答（非流式）。

        返回:
            {
              "answer", "citations": [...], "confidence",
              "sub_queries", "retrieval_stats", "trace_id", "latency_ms"
            }
        """
        t0 = time.perf_counter()
        trace_id = uuid.uuid4().hex[:16]

        # ---- Step 1: 查询预处理 ----
        resolved = self.optimizer.resolve_coreference(question, history or [])
        rewritten = await self.optimizer.rewrite(resolved)
        is_complex = self.optimizer.is_complex(rewritten)
        sub_queries = (
            await self.optimizer.decompose(rewritten) if is_complex else [rewritten]
        )

        # ---- Step 2+3: 混合检索 + 精排（多子查询合并）----
        retrieval = await self._multi_query_retrieve(
            sub_queries, enable_graph=enable_graph
        )
        results, stats = retrieval["results"], retrieval["stats"]

        # ---- 自评估重检索闭环 ----
        answer = ""
        evaluation = {"confidence": 0.5}
        for attempt in range(self.max_reretrieve + 1):
            # ---- Step 4: 上下文构建 ----
            context, citations = self._build_context(results)
            # ---- Step 5: LLM 生成 ----
            answer = await llm_client.generate(
                GENERATION_PROMPT.format(context=context, question=question),
                system=SYSTEM_PROMPT,
                max_tokens=1024,     # 配合精炼指令，避免长回答拖慢端到端耗时
            )
            if not answer:      # LLM 降级
                answer, evaluation = self._fallback_answer(question, results, context)
                break
            # ---- Step 6: 自评估 ----
            evaluation = await self._self_evaluate(question, context, answer)
            needs_retry = (
                evaluation.get("needs_reretrieval")
                and evaluation.get("confidence", 1.0) < 0.6
                and attempt < self.max_reretrieve
            )
            if not needs_retry:
                break
            logger.info("自评估低置信度(%.2f)，触发重检索", evaluation.get("confidence", 0))
            # 扩大召回重新检索
            retrieval = await self._multi_query_retrieve(
                [rewritten], top_k=config.RECALL_TOP_K * 2, enable_graph=enable_graph
            )
            results, stats = retrieval["results"], retrieval["stats"]
            # 重评估后进入下一轮生成
            context, citations = self._build_context(results)

        # ---- Step 7: 引用校验 + 纯文本清洗 ----
        citations = self._extract_citations(results)
        answer = self._validate_citations(answer, citations)
        answer = strip_markdown(answer)

        latency_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "answer": answer,
            "citations": citations,
            "confidence": round(float(evaluation.get("confidence", 0.5)), 2),
            "sub_queries": sub_queries if is_complex else None,
            "retrieval_stats": stats,
            "trace_id": trace_id,
            "latency_ms": latency_ms,
        }

    # ==================================================================
    # 二、SSE 流式问答
    # ==================================================================
    async def stream_query(self, question: str, history: list[dict] | None = None,
                           enable_graph: bool = True):
        """
        流式问答：yield SSE 事件字典。

        事件协议（需求文档 3.7.2）:
            {"type": "meta",    "data": {trace_id, sub_queries, retrieval_stats}}
            {"type": "citation","data": {law_name, article_no, content, effectiveness}}
            {"type": "token",   "data": {"content": "增量文本"}}
            {"type": "confidence", "data": {"value": 0.87}}
            {"type": "done",    "data": {"latency_ms": ..., "answer": "完整回答"}}
            {"type": "error",   "data": {"message": "..."}}
        """
        t0 = time.perf_counter()
        trace_id = uuid.uuid4().hex[:16]
        try:
            # ---- 检索阶段（先完成，再流式生成）----
            resolved = self.optimizer.resolve_coreference(question, history or [])
            rewritten = await self.optimizer.rewrite(resolved)
            is_complex = self.optimizer.is_complex(rewritten)
            sub_queries = (
                await self.optimizer.decompose(rewritten) if is_complex else [rewritten]
            )
            retrieval = await self._multi_query_retrieve(
                sub_queries, enable_graph=enable_graph
            )
            results, stats = retrieval["results"], retrieval["stats"]

            # 元信息事件（子查询分解 + 检索统计，前端进度展示）
            yield {"type": "meta", "data": {
                "trace_id": trace_id,
                "rewritten_query": rewritten,
                "sub_queries": sub_queries if is_complex else [],
                "retrieval_stats": stats,
            }}

            # 引用事件（生成前下发，前端先渲染引用卡片）
            citations = self._extract_citations(results)
            for citation in citations:
                yield {"type": "citation", "data": citation}

            # ---- 流式生成（token 级增量 + Markdown 增量清洗）----
            # MarkdownStripper 保证跨分片标记（'**' '###' 被切碎）同样被清除，
            # 且每次只输出可安全下发的纯文本增量 -> 前端逐字渲染即纯文本
            context, _ = self._build_context(results)
            stripper = MarkdownStripper()
            full_answer = []
            async for piece in llm_client.generate_stream(
                GENERATION_PROMPT.format(context=context, question=question),
                system=SYSTEM_PROMPT,
                max_tokens=1024,     # 配合精炼指令，缩短生成阶段耗时
            ):
                if piece:
                    cleaned = stripper.feed(piece)
                    if cleaned:
                        full_answer.append(cleaned)
                        yield {"type": "token", "data": {"content": cleaned}}
            # 流结束：输出缓冲残留（完成跨片标记清洗）
            tail = stripper.flush()
            if tail:
                full_answer.append(tail)
                yield {"type": "token", "data": {"content": tail}}

            answer = "".join(full_answer)
            if not answer:
                # LLM 降级：模板回答（同样清洗为纯文本）
                answer = strip_markdown(
                    self._fallback_answer(question, results, context)[0])
                yield {"type": "token", "data": {"content": answer}}

            # ---- 自评估（置信度事件）----
            evaluation = await self._self_evaluate(question, context, answer)
            yield {"type": "confidence", "data": {
                "value": round(float(evaluation.get("confidence", 0.5)), 2)
            }}

            yield {"type": "done", "data": {
                "answer": answer,
                "citations": citations,
                "confidence": round(float(evaluation.get("confidence", 0.5)), 2),
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "trace_id": trace_id,
            }}
        except Exception as e:
            logger.error("流式问答异常: %s", e, exc_info=True)
            yield {"type": "error", "data": {"message": f"问答服务异常: {e}"}}

    # ==================================================================
    # 三、内部方法
    # ==================================================================
    async def _multi_query_retrieve(self, sub_queries: list[str],
                                    top_k: int | None = None,
                                    enable_graph: bool = True) -> dict:
        """
        多子查询并发检索 + 合并去重。

        各子查询独立走完整两阶段管线（三路召回 + RRF + 精排），
        结果按 rerank_score 全局排序后截断。
        """
        tasks = [
            self.retriever.search(q, top_k=top_k, enable_graph=enable_graph)
            for q in sub_queries
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        merged, stats = [], None
        for resp in responses:
            if isinstance(resp, Exception):
                logger.warning("子查询检索失败: %s", resp)
                continue
            if stats is None:
                stats = resp["stats"]
            else:
                # 合并各子查询统计
                for key in ("dense_hits", "sparse_hits", "graph_hits"):
                    stats[key] = stats.get(key, 0) + resp["stats"].get(key, 0)
            merged.extend(resp["results"])

        # 去重（按文本前缀）并按精排分数重排
        seen, deduped = set(), []
        for doc in sorted(merged, key=lambda d: d.get("rerank_score", 0), reverse=True):
            key = doc.get("text", "")[:100]
            if key not in seen:
                seen.add(key)
                deduped.append(doc)
        if stats is None:
            stats = {"dense_hits": 0, "sparse_hits": 0, "graph_hits": 0,
                     "fused_count": 0, "latency_ms": 0, "graph_available": False}
        return {"results": deduped[:config.RERANK_TOP_N * 2], "stats": stats}

    def _build_context(self, results: list[dict]) -> tuple[str, list[dict]]:
        """
        构建检索上下文（带 [1][2] 引用编号）。

        返回:
            (上下文字符串, 引用元数据列表)
        """
        if not results:
            return "（未检索到相关法律资料）", []
        parts, citations = [], []
        for i, doc in enumerate(results[:config.RERANK_TOP_N], start=1):
            law = doc.get("law_name") or doc.get("title", "资料")
            art = doc.get("article_no", "")
            eff = doc.get("effectiveness", "有效")
            parts.append(
                f"[{i}] 《{law}》{art}（{eff}）\n{doc.get('text', '')[:800]}"
            )
            citations.append({
                "law_name": law,
                "article_no": art,
                "content": doc.get("text", "")[:300],
                "effectiveness": eff,
                "source_url": None,
            })
        return "\n\n".join(parts), citations

    async def _self_evaluate(self, question: str, context: str,
                             answer: str) -> dict:
        """
        Self-RAG 自评估（LLM 三维打分）。

        性能: 评估输入截断至 800 字 + 输出限 128 tokens + 失败不重试，
        避免"回答已生成但 done 事件迟迟不下发"的尾延迟（曾贡献 20s+）。

        降级: LLM 失败时按检索命中数给出启发式置信度。
        """
        result = await llm_client.generate_json(
            SELF_EVAL_PROMPT.format(
                query=question, context=context[:800], answer=answer[:800],
            ),
            system="你只输出 JSON。", default=None, retry=0,
            max_tokens=128,
        )
        if isinstance(result, dict) and "confidence" in result:
            return result
        # 启发式降级：上下文越充分置信度越高
        return {"confidence": 0.65 if len(context) > 200 else 0.4,
                "law_accuracy": 3, "logic_completeness": 3,
                "factual_consistency": 3, "needs_reretrieval": False}

    @staticmethod
    def _extract_citations(results: list[dict]) -> list[dict]:
        """从精排结果提取法条引用列表。"""
        citations = []
        seen = set()
        for doc in results[:config.RERANK_TOP_N]:
            law = doc.get("law_name") or doc.get("title", "")
            if not law:
                continue
            key = f"{law}{doc.get('article_no', '')}"
            if key in seen:
                continue
            seen.add(key)
            citations.append({
                "law_name": law,
                "article_no": doc.get("article_no", ""),
                "content": doc.get("text", "")[:300],
                "effectiveness": doc.get("effectiveness", "有效"),
                "source_url": None,
                "score": round(float(doc.get("rerank_score", 0)), 4),
            })
        return citations

    @staticmethod
    def _validate_citations(answer: str, citations: list[dict]) -> str:
        """
        法条引用校验：回答中引用的法条若不在检索结果中，
        标注提示（降低幻觉风险，需求文档合规要求）。

        规则:
            正则提取回答中的《法律》第X条引用；
            未命中检索引用集 -> 在文末追加校验说明。
        """
        if not citations:
            return answer
        valid_refs = {
            f"{c['law_name']}{c['article_no']}" for c in citations
        }
        pattern = re.compile(r"《([^《》]{2,20})》\s*(第[一二三四五六七八九十百千零\d]+条)")
        cited_in_answer = {f"{m.group(1)}{m.group(2)}" for m in pattern.finditer(answer)}
        unsupported = cited_in_answer - valid_refs
        if unsupported:
            note = "\n\n> ⚠️ 引用校验：以下引用未在本次检索资料中直接命中，请核实原文：" \
                   + "、".join(sorted(unsupported)[:5])
            return answer + note
        return answer

    @staticmethod
    def _fallback_answer(question: str, results: list[dict],
                         context: str) -> tuple[str, dict]:
        """
        LLM 不可用时的降级回答（纯检索 + 模板，需求文档 2.3.4）。

        返回:
            (降级回答文本, 置信度评估)
        """
        if results:
            top = results[:3]
            lines = [f"**检索到的相关法律资料（AI 生成服务暂不可用，以下为原文摘要）：**", ""]
            for i, doc in enumerate(top, 1):
                lines.append(f"**[{i}] 《{doc.get('law_name', '')}》{doc.get('article_no', '')}**"
                             f"（{doc.get('effectiveness', '有效')}）")
                lines.append(doc.get("text", "")[:400])
                lines.append("")
            lines.append("> ⚠️ 当前为大模型降级模式，建议稍后重试以获取完整分析。")
            return "\n".join(lines), {"confidence": 0.45}
        return ("当前 AI 服务暂不可用且未检索到相关资料，请稍后重试。",
                {"confidence": 0.1})


# 全局单例
qa_pipeline = LegalQAPipeline()
