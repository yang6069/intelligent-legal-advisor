# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 智能问答路由（含 SSE 流式接口）
==================================================================
路由清单:
    POST /api/qa/sessions            创建会话
    GET  /api/qa/sessions            会话列表（本人）
    GET  /api/qa/sessions/{id}/messages  会话消息历史
    DELETE /api/qa/sessions/{id}     删除会话
    POST /api/qa/ask                 非流式问答
    POST /api/qa/ask/stream          SSE 流式问答（token/citation/confidence/done）

数据流（需求文档 3.4.1）:
    提问 -> 查询重写/子查询分解 -> 三路混合检索 -> 精排 -> 生成
    -> 自评估（低置信度触发重检索）-> 持久化（会话+消息）
==================================================================
"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src import database
from src.api.deps import get_current_user, require_module
from src.audit.service import log_audit
from src.rag.qa_pipeline import qa_pipeline
from src.subscription.service import check_and_consume_quota

logger = logging.getLogger("zhifatong.api.qa")

router = APIRouter()


# ==================================================================
# 请求模型
# ==================================================================
class SessionCreateRequest(BaseModel):
    title: str = Field("新会话", max_length=128)


class AskRequest(BaseModel):
    """问答请求（需求文档 3.7.1 QARequest）。"""
    question: str = Field(..., min_length=1, max_length=2000, description="用户法律问题")
    session_id: int | None = Field(None, description="会话ID（多轮对话关联）")
    retrieval_top_k: int = Field(5, ge=1, le=20, description="精排输出条数")
    enable_graph: bool = Field(True, description="是否启用图谱关联检索")


# ==================================================================
# 会话管理
# ==================================================================
def _own_session(user: dict, session_id: int) -> dict:
    """
    校验会话归属（多用户数据隔离硬约束）。

    仅创建人本人可访问；会话不存在返回 404（不泄露存在性），
    非本人返回 403（拒绝越权）。所有角色一视同仁，无管理旁路。
    """
    row = database.query_one(
        "SELECT * FROM qa_sessions WHERE id = %s", (session_id,))
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    if row["user_id"] != user["user_id"]:
        logger.warning(
            "越权访问拦截: 用户 %s(#%d) 试图访问他人问答会话 #%d（归属用户 #%d）",
            user["username"], user["user_id"], session_id, row["user_id"])
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return row


@router.post("/qa/sessions")
def create_session(body: SessionCreateRequest,
                   user: dict = Depends(get_current_user)):
    """创建问答会话。"""
    session_id = database.insert_return_id(
        "INSERT INTO qa_sessions (user_id, title) VALUES (%s, %s)",
        (user["user_id"], body.title))
    return {"session_id": session_id, "title": body.title}


@router.get("/qa/sessions")
def list_sessions(user: dict = Depends(get_current_user)):
    """本人会话列表（按最近活跃倒序）。"""
    rows = database.query_all(
        "SELECT id, title, msg_count, created_at, updated_at "
        "FROM qa_sessions WHERE user_id = %s ORDER BY updated_at DESC LIMIT 100",
        (user["user_id"],))
    return {"items": rows}


@router.get("/qa/sessions/{session_id}/messages")
def session_messages(session_id: int,
                     user: dict = Depends(get_current_user)):
    """会话消息历史（含法条引用/置信度等 RAG 产物）。"""
    _own_session(user, session_id)
    rows = database.query_all(
        "SELECT id, role, content, citations, sub_queries, confidence, "
        "latency_ms, created_at FROM qa_messages WHERE session_id = %s "
        "ORDER BY id", (session_id,))
    return {"items": rows}


@router.delete("/qa/sessions/{session_id}")
def delete_session(session_id: int,
                   user: dict = Depends(get_current_user)):
    """删除会话及其全部消息。"""
    _own_session(user, session_id)
    database.execute("DELETE FROM qa_messages WHERE session_id = %s", (session_id,))
    database.execute("DELETE FROM qa_sessions WHERE id = %s", (session_id,))
    return {"message": "会话已删除"}


# ==================================================================
# 消息持久化（问答完成后落库）
# ==================================================================
def _persist_round(user: dict, session_id: int, question: str, answer: str,
                   citations: list, sub_queries: list, confidence: float,
                   latency_ms: int) -> None:
    """
    保存一轮问答（user + assistant 两条消息）并更新会话计数。

    失败安全: 仅记录警告，不影响主流程（回答已返回给用户）。
    """
    try:
        database.execute(
            "INSERT INTO qa_messages (session_id, user_id, role, content) "
            "VALUES (%s, %s, 'user', %s)",
            (session_id, user["user_id"], question))
        database.execute(
            "INSERT INTO qa_messages (session_id, user_id, role, content, "
            "citations, sub_queries, confidence, latency_ms) "
            "VALUES (%s, %s, 'assistant', %s, %s, %s, %s, %s)",
            (session_id, user["user_id"], answer,
             json.dumps(citations, ensure_ascii=False) if citations else None,
             json.dumps(sub_queries, ensure_ascii=False) if sub_queries else None,
             confidence, latency_ms))
        database.execute(
            "UPDATE qa_sessions SET msg_count = msg_count + 2, "
            "title = IF(msg_count = 0, %s, title) WHERE id = %s",
            (question[:60], session_id))
    except Exception as e:
        logger.warning("问答持久化失败: %s", e)


async def _build_history(session_id: int, limit: int = 6) -> list[dict]:
    """
    构建多轮对话历史（最近 limit 轮，供指代消解使用）。
    """
    rows = database.query_all(
        "SELECT role, content FROM qa_messages WHERE session_id = %s "
        "ORDER BY id DESC LIMIT %s", (session_id, limit * 2))
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    return history


# ==================================================================
# 问答接口
# ==================================================================
@router.post("/qa/ask")
async def ask(body: AskRequest, user: dict = Depends(get_current_user)):
    """
    非流式问答（一次性返回完整回答）。

    流程:
        配额检查 -> 会话自动创建 -> 历史指代消解 -> RAG 全链路 -> 持久化 -> 审计。
    """
    # ---- 配额检查（普通用户扣减 / 管理员和法务直接放行）----
    check_and_consume_quota(user, mode="qa")

    # 会话兜底创建
    session_id = body.session_id
    if not session_id:
        session_id = database.insert_return_id(
            "INSERT INTO qa_sessions (user_id, title) VALUES (%s, %s)",
            (user["user_id"], body.question[:60]))
    history = await _build_history(session_id)

    result = await qa_pipeline.query(
        body.question, history=history, enable_graph=body.enable_graph)

    _persist_round(user, session_id, body.question, result.get("answer", ""),
                   result.get("citations", []), result.get("sub_queries", []),
                   result.get("confidence", 0.0), result.get("latency_ms", 0))
    log_audit(user["user_id"], user["username"], "QUERY", "qa",
              body.question[:200], None)
    result["session_id"] = session_id
    return result


@router.post("/qa/ask/stream")
async def ask_stream(body: AskRequest,
                     user: dict = Depends(get_current_user)):
    """
    SSE 流式问答（需求文档 3.7.2 事件协议）。

    事件序列:
        meta（trace_id/子查询/检索统计）
        -> citation × N（法条引用卡片）
        -> token × N（增量文本）
        -> confidence（自评估置信度）
        -> done（完整回答 + 持久化）

    响应头:
        Content-Type: text/event-stream（Nginx 需关闭缓冲 proxy_buffering off）
    """
    # ---- 配额检查（普通用户扣减 / 管理员和法务直接放行）----
    check_and_consume_quota(user, mode="qa")

    session_id = body.session_id
    if not session_id:
        session_id = database.insert_return_id(
            "INSERT INTO qa_sessions (user_id, title) VALUES (%s, %s)",
            (user["user_id"], body.question[:60]))
    history = await _build_history(session_id)

    async def event_generator():
        """SSE 事件生成器：桥接 qa_pipeline 流式事件到 SSE 文本协议。"""
        full_answer = []
        citations, confidence, latency, sub_queries = [], 0.0, 0, []
        try:
            async for event in qa_pipeline.stream_query(
                    body.question, history=history,
                    enable_graph=body.enable_graph):
                etype, data = event["type"], event.get("data", {})
                if etype == "citation":
                    citations.append(data)
                elif etype == "token":
                    full_answer.append(data.get("content", ""))
                elif etype == "confidence":
                    confidence = data.get("value", 0.0)
                elif etype == "done":
                    latency = data.get("latency_ms", 0)
                elif etype == "meta":
                    sub_queries = data.get("sub_queries", [])
                # SSE 标准格式：event: <类型>\ndata: <JSON>\n\n
                yield f"event: {etype}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            # 流结束后持久化完整回答
            answer = "".join(full_answer)
            _persist_round(user, session_id, body.question, answer,
                           citations, sub_queries, confidence, latency)
            log_audit(user["user_id"], user["username"], "QUERY_STREAM", "qa",
                      body.question[:200], None)
            # 会话 ID 通过额外事件下发（前端更新路由）
            yield (f"event: session\ndata: "
                   f"{json.dumps({'session_id': session_id}, ensure_ascii=False)}\n\n")
        except Exception as e:
            logger.error("SSE 流式问答异常: %s", e, exc_info=True)
            yield (f"event: error\ndata: "
                   f"{json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n")
        finally:
            # 结束标记（部分代理需要显式 EOF 事件）
            yield "event: close\ndata: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # Nginx 禁用缓冲，保证 token 级实时推送
            "Connection": "keep-alive",
        })

