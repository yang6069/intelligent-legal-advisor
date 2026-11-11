# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | LLM 服务客户端（Ollama qwen2.5:3b）
==================================================================
模块职责：
    1. 封装 Ollama 原生 /api/chat 接口：普通生成 / 流式生成；
    2. 结构化 JSON 输出助手（robust JSON 提取 + 失败降级）；
    3. 服务可用性探测与降级（LLM 不可用返回模板回答，保证系统可用）。

设计要点：
    - 全部走 httpx 异步客户端，连接池复用；
    - 超时保护：生成 120s / 流式 180s；
    - JSON 提取容错：小参数模型偶发输出 markdown 代码块/前后缀噪声，
      采用"首个 { 到最后一个 }"截取 + 二次重试 + 默认值兜底。
==================================================================
"""
import asyncio
import json
import logging
import re
import time
from typing import AsyncGenerator, Callable, Any

import httpx

from src import config

logger = logging.getLogger("zhifatong.llm")


class OllamaLLMClient:
    """Ollama 大模型推理客户端（qwen2.5:3b，模型名可经 LLM_MODEL 环境变量覆盖）。"""

    def __init__(self):
        self.base_url = config.OLLAMA_BASE_URL
        self.model = config.LLM_MODEL
        self._http = httpx.AsyncClient(
            base_url=self.base_url, timeout=config.LLM_TIMEOUT,
        )
        self._available: bool | None = None    # 缓存可用性探测结果

    # ------------------------------------------------------------------
    # 可用性探测
    # ------------------------------------------------------------------
    async def health_check(self) -> bool:
        """
        探测 Ollama 服务与目标模型是否可用。

        返回:
            True=可用；False=不可用（上层走降级逻辑）
        """
        try:
            resp = await self._http.get("/api/tags", timeout=5)
            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            ok = any(m.startswith(self.model.split(":")[0]) for m in models)
            self._available = ok
            return ok
        except Exception as e:
            logger.warning("Ollama 不可用: %s", e)
            self._available = False
            return False

    # ------------------------------------------------------------------
    # 普通生成（一次性返回全文）
    # ------------------------------------------------------------------
    async def generate(self, prompt: str, system: str | None = None,
                       temperature: float | None = None,
                       max_tokens: int | None = None) -> str:
        """
        同步生成完整回答。

        参数:
            prompt: 用户提示词
            system: 系统角色设定（法律专家人设）
            temperature: 采样温度（默认取 config，法律场景 0.1）
            max_tokens: 生成上限

        返回:
            模型输出文本；服务不可用返回空串（调用方降级）
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = await self._http.post("/api/chat", json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": temperature if temperature is not None else config.LLM_TEMPERATURE,
                    "num_predict": max_tokens or config.LLM_MAX_TOKENS,
                },
            })
            resp.raise_for_status()
            return resp.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error("LLM 生成失败: %s", e)
            return ""

    # ------------------------------------------------------------------
    # 流式生成（SSE 逐 token 输出）
    # ------------------------------------------------------------------
    async def generate_stream(self, prompt: str, system: str | None = None,
                              temperature: float | None = None,
                              max_tokens: int | None = None) -> AsyncGenerator[str, None]:
        """
        流式生成：逐 token yield。

        Ollama /api/chat stream=True 时按行返回 JSON，
        每行 {"message": {"content": "增量文本"}, "done": false}。

        参数:
            max_tokens: 生成上限（默认 config.LLM_MAX_TOKENS）；
                        问答等短输出场景可收紧以降低端到端耗时
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            async with self._http.stream(
                "POST", "/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": temperature if temperature is not None else config.LLM_TEMPERATURE,
                        "num_predict": max_tokens or config.LLM_MAX_TOKENS,
                    },
                },
                timeout=config.LLM_STREAM_TIMEOUT,
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                        piece = chunk.get("message", {}).get("content", "")
                        if piece:
                            yield piece
                        if chunk.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error("LLM 流式生成失败: %s", e)
            yield ""

    # ------------------------------------------------------------------
    # 结构化 JSON 输出（关键能力：多 Agent 工作流的骨架）
    # ------------------------------------------------------------------
    async def generate_json(self, prompt: str, system: str | None = None,
                            retry: int = 1, default: Any = None,
                            max_tokens: int | None = None) -> Any:
        """
        生成并解析 JSON 结构化输出。

        容错策略:
            1. 首次生成 -> 提取 JSON（截取首 { 到末 }）；
            2. 解析失败 -> 带纠错指令重试 retry 次；
            3. 全部失败 -> 返回 default（调用方降级处理）。

        参数:
            max_tokens: 生成上限（JSON 输出通常很短，收紧可显著降延迟）

        返回:
            解析后的 Python 对象（dict/list）；失败返回 default。
        """
        attempt_prompt = prompt
        for attempt in range(retry + 1):
            raw = await self.generate(attempt_prompt, system=system,
                                      temperature=0.1, max_tokens=max_tokens)
            parsed = self._extract_json(raw)
            if parsed is not None:
                return parsed
            # 纠错重试：明确告知格式错误，要求纯 JSON 输出
            attempt_prompt = (
                f"{prompt}\n\n【重要】上一次输出不是合法 JSON，请只输出符合要求的纯 JSON，"
                f"不要包含任何解释文字或 markdown 代码块标记。"
            )
            logger.warning("JSON 解析失败（第 %d 次），触发纠错重试", attempt + 1)
        return default

    @staticmethod
    def _extract_json(text: str) -> Any:
        """
        从模型输出中稳健提取 JSON。

        步骤:
            1. 去除 ```json ... ``` 代码块标记；
            2. 截取首个 {（或 [）到最后一个 }（或 ]）之间的内容；
            3. json.loads 校验。
        """
        if not text:
            return None
        cleaned = re.sub(r"```(?:json)?", "", text).strip()
        # 优先尝试整体解析（模型理想输出）
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # 截取花括号/方括号区间
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start, end = cleaned.find(open_ch), cleaned.rfind(close_ch)
            if start != -1 and end > start:
                try:
                    return json.loads(cleaned[start:end + 1])
                except json.JSONDecodeError:
                    continue
        return None


# 全局单例
llm_client = OllamaLLMClient()


# ==================================================================
# 辅助：并发运行多个异步生成（三方 Agent 并行审查）
# ==================================================================
async def gather_llm(tasks: list[Callable]) -> list:
    """
    并发执行多个异步调用（asyncio.gather 包装）。

    参数:
        tasks: 返回 coroutine 的可调用对象列表

    返回:
        各任务结果列表（异常任务返回 None，不阻断整体）
    """
    results = await asyncio.gather(
        *[t() for t in tasks], return_exceptions=True
    )
    return [None if isinstance(r, Exception) else r for r in results]
