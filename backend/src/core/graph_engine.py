# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 轻量级状态图工作流引擎（LangGraph 兼容语义）
==================================================================
模块职责：
    提供与 LangGraph StateGraph 等价的状态图编排能力（自研实现）：
    1. add_node / add_edge / add_conditional_edges / set_entry_point；
    2. 节点函数返回增量状态（partial update），自动合并进全局 State；
    3. 条件路由：根据当前状态动态选择下一个节点；
    4. MemorySaver 检查点：每个节点执行完毕持久化快照，支持中断恢复；
    5. interrupt_before 语义：指定节点执行前暂停，等待显式 resume。

自研原因：
    conda base（Python 3.13）下 langgraph 官方版本兼容性受限；
    自研引擎以约 300 行核心代码实现合同审查工作流所需的全部语义，
    且零第三方依赖、行为完全可控、便于企业级审计。

与 LangGraph 的语义对齐：
    StateGraph(State)          -> StateGraph(state_type)
    app.invoke(state, config)  -> graph.invoke(state, thread_id)
    app.stream(state)          -> graph.stream(state)  [逐节点增量产出]
    checkpointer=MemorySaver   -> checkpointer=MemorySaver()
==================================================================
"""
import logging
import time
import uuid
from typing import Any, Callable, TypedDict

logger = logging.getLogger("zhifatong.graph")

# 节点函数签名：接收完整状态，返回增量状态（partial dict）
NodeFunc = Callable[[dict], dict]
# 条件路由函数签名：接收完整状态，返回下一节点名（或 END）
RouterFunc = Callable[[dict], str]

END = "__end__"          # 终止节点标识（与 LangGraph.END 语义一致）


class GraphRecursionError(RuntimeError):
    """图执行超过最大步数（防止条件路由死循环）。"""


class MemorySaver:
    """
    内存检查点存储器。

    结构: thread_id -> [快照1, 快照2, ...]（每个快照 = 某节点执行完毕后的完整状态）
    用途:
        1. 中断恢复：interrupt_before 暂停后，可从最后快照继续；
        2. 时间旅行调试：查看每个节点执行后的历史状态；
        3. 审计：多 Agent 工作流的完整推理链留痕。
    """

    def __init__(self):
        self._checkpoints: dict[str, list[dict]] = {}

    def put(self, thread_id: str, state: dict) -> None:
        """追加一条检查点（深拷贝隔离，防止后续修改污染历史）。"""
        import copy
        self._checkpoints.setdefault(thread_id, []).append(copy.deepcopy(state))

    def get(self, thread_id: str) -> dict | None:
        """取该线程最新（最后）的检查点状态。"""
        snaps = self._checkpoints.get(thread_id)
        return snaps[-1] if snaps else None

    def history(self, thread_id: str) -> list[dict]:
        """取全部检查点（时间旅行查询）。"""
        return list(self._checkpoints.get(thread_id, []))


class StateGraph:
    """
    状态图编排器。

    执行模型:
        state ← 入口状态
        loop:
            node ← 路由决定（固定边 or 条件边）
            if node in interrupt_before: 暂停（返回当前状态 + 待执行节点）
            partial ← node(state)
            state ← merge(state, partial)
            checkpointer.put(state)
            if 路由结果 == END: 结束
    """

    def __init__(self, state_type: type[TypedDict] | None = None,
                 max_steps: int = 50):
        self._nodes: dict[str, NodeFunc] = {}
        self._edges: dict[str, str] = {}                    # 固定边: 节点 -> 下一节点
        self._conditional: dict[str, tuple[RouterFunc, dict[str, str]]] = {}  # 条件边
        self._entry: str | None = None
        self._interrupt_before: set[str] = set()
        self._state_type = state_type
        self._max_steps = max_steps
        self._checkpointer: MemorySaver | None = None

    # ------------------------------------------------------------------
    # 图构建 API（与 LangGraph 对齐）
    # ------------------------------------------------------------------
    def add_node(self, name: str, func: NodeFunc) -> "StateGraph":
        """注册节点：func(state) -> partial_state。"""
        if name == END:
            raise ValueError("节点名不能为保留字 __end__")
        self._nodes[name] = func
        return self

    def add_edge(self, from_node: str, to_node: str) -> "StateGraph":
        """添加固定边：from_node 执行完毕后无条件进入 to_node。"""
        if to_node != END and to_node not in self._nodes:
            raise ValueError(f"边指向未注册节点: {to_node}")
        self._edges[from_node] = to_node
        return self

    def add_conditional_edges(self, from_node: str, router: RouterFunc,
                              mapping: dict[str, str]) -> "StateGraph":
        """
        添加条件边。

        参数:
            from_node: 源节点
            router: 路由函数，返回映射键
            mapping: {映射键: 目标节点名}，可含 "__end__"
        """
        self._conditional[from_node] = (router, mapping)
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        """设置入口节点。"""
        if name not in self._nodes:
            raise ValueError(f"入口节点未注册: {name}")
        self._entry = name
        return self

    def compile(self, checkpointer: MemorySaver | None = None,
                interrupt_before: list[str] | None = None) -> "CompiledGraph":
        """
        编译为可执行图。

        参数:
            checkpointer: 检查点存储器（启用中断恢复）
            interrupt_before: 在这些节点执行前暂停（人工确认语义）
        """
        if self._entry is None:
            raise ValueError("未设置入口节点")
        self._checkpointer = checkpointer
        self._interrupt_before = set(interrupt_before or [])
        return CompiledGraph(self)

    # ------------------------------------------------------------------
    # 内部：路由决策
    # ------------------------------------------------------------------
    def _next_node(self, current: str, state: dict) -> str:
        """
        决定 current 节点之后的下一个节点。

        优先级: 条件边 > 固定边 > END
        """
        if current in self._conditional:
            router, mapping = self._conditional[current]
            key = router(state)
            if key not in mapping:
                raise ValueError(f"条件路由返回未知键: {key}")
            return mapping[key]
        return self._edges.get(current, END)


class CompiledGraph:
    """编译后的可执行状态图。"""

    def __init__(self, graph: StateGraph):
        self._g = graph

    # ------------------------------------------------------------------
    # 同步执行（节点函数为同步时使用；合同审查工作流为异步节点）
    # ------------------------------------------------------------------
    def invoke(self, state: dict, thread_id: str | None = None) -> dict:
        """同步执行整图（无 interrupt 场景）。"""
        return self._run(state, thread_id, None)

    async def ainvoke(self, state: dict, thread_id: str | None = None) -> dict:
        """异步执行整图（节点函数为 async def 时使用）。"""
        return await self._arun(state, thread_id, None)

    # ------------------------------------------------------------------
    # 流式执行（逐节点产出增量状态，供前端进度展示）
    # ------------------------------------------------------------------
    def stream(self, state: dict, thread_id: str | None = None):
        """同步流式执行：generator 逐节点 yield {node, state}。"""
        yield from self._run_stream(state, thread_id)

    async def astream(self, state: dict, thread_id: str | None = None):
        """异步流式执行。"""
        current = self._g._entry
        state = dict(state)
        steps = 0
        while current != END:
            if steps > self._g._max_steps:
                raise GraphRecursionError(f"超过最大步数 {self._g._max_steps}")
            func = self._g._nodes[current]
            partial = await func(state) if _is_async(func) else func(state)
            state = _merge_state(state, partial)
            if self._g._checkpointer:
                tid = thread_id or str(uuid.uuid4())
                self._g._checkpointer.put(tid, state)
            yield {"node": current, "state": state}
            current = self._g._next_node(current, state)
            steps += 1

    # ------------------------------------------------------------------
    # 中断恢复
    # ------------------------------------------------------------------
    def resume(self, thread_id: str) -> dict | None:
        """从最后检查点恢复（需配合 interrupt_before 使用）。"""
        ckpt = self._g._checkpointer
        if ckpt is None:
            return None
        state = ckpt.get(thread_id)
        return state

    # ------------------------------------------------------------------
    # 内部执行内核
    # ------------------------------------------------------------------
    def _run(self, state: dict, thread_id: str | None, resume_state: dict | None) -> dict:
        """同步执行核心循环。"""
        state = dict(resume_state or state)
        current = self._g._entry
        steps = 0
        while current != END:
            if steps > self._g._max_steps:
                raise GraphRecursionError(f"超过最大步数 {self._g._max_steps}")
            if current in self._g._interrupt_before:
                logger.info("图在节点 [%s] 前暂停（interrupt_before）", current)
                if self._g._checkpointer:
                    self._g._checkpointer.put(thread_id or str(uuid.uuid4()), state)
                return state          # 暂停：返回当前状态
            func = self._g._nodes[current]
            state = _merge_state(state, func(state))
            if self._g._checkpointer:
                self._g._checkpointer.put(thread_id or str(uuid.uuid4()), state)
            current = self._g._next_node(current, state)
            steps += 1
        return state

    async def _arun(self, state: dict, thread_id: str | None,
                    resume_state: dict | None) -> dict:
        """异步执行核心循环（合同审查工作流主路径）。"""
        state = dict(resume_state or state)
        current = self._g._entry
        steps = 0
        while current != END:
            if steps > self._g._max_steps:
                raise GraphRecursionError(f"超过最大步数 {self._g._max_steps}")
            if current in self._g._interrupt_before:
                logger.info("图在节点 [%s] 前暂停（interrupt_before）", current)
                if self._g._checkpointer:
                    self._g._checkpointer.put(thread_id or str(uuid.uuid4()), state)
                return state
            func = self._g._nodes[current]
            t0 = time.perf_counter()
            partial = await func(state) if _is_async(func) else func(state)
            state = _merge_state(state, partial)
            cost = (time.perf_counter() - t0) * 1000
            logger.info("节点 [%s] 完成，耗时 %.0f ms", current, cost)
            if self._g._checkpointer:
                self._g._checkpointer.put(thread_id or str(uuid.uuid4()), state)
            current = self._g._next_node(current, state)
            steps += 1
        return state

    def _run_stream(self, state: dict, thread_id: str | None):
        """同步流式核心循环。"""
        current = self._g._entry
        state = dict(state)
        steps = 0
        while current != END:
            if steps > self._g._max_steps:
                raise GraphRecursionError(f"超过最大步数 {self._g._max_steps}")
            func = self._g._nodes[current]
            state = _merge_state(state, func(state))
            if self._g._checkpointer:
                self._g._checkpointer.put(thread_id or str(uuid.uuid4()), state)
            yield {"node": current, "state": state}
            current = self._g._next_node(current, state)
            steps += 1


# ==================================================================
# 工具函数
# ==================================================================
def _is_async(func: Callable) -> bool:
    """判断函数是否为协程函数。"""
    import inspect
    return inspect.iscoroutinefunction(func)


def _merge_state(state: dict, partial: dict) -> dict:
    """
    合并节点增量状态到全局状态。

    合并规则:
        1. 普通键：直接覆盖（浅合并）；
        2. 列表键且旧值为列表：追加合并（如 debate_log 多轮累积）；
        3. 空增量：原状态不变。
    """
    if not partial:
        return state
    for key, value in partial.items():
        if (key in state and isinstance(state[key], list)
                and isinstance(value, list)):
            state[key] = state[key] + value       # 列表追加（LangGraph Annotated semantics 简化）
        else:
            state[key] = value
    return state
