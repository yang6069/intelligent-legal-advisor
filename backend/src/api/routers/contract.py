# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 合同审查路由（多 Agent 对抗式审查）
==================================================================
路由清单:
    POST /api/contract/upload            上传合同并发起审查（后台异步执行）
    POST /api/contract/review-text       粘贴文本审查（立即返回 contract_id + sse_url）
    GET  /api/contract/{id}/progress     SSE 节点级进度流（11 个节点真实推送）
    GET  /api/contract/{id}/export       导出 AI 修改后 .docx（同步返回）
    GET  /api/contract/{id}/export-progress  SSE 导出进度流
    GET  /api/contract/list              审查记录列表
    GET  /api/contract/{id}              审查报告详情
    DELETE /api/contract/{id}            删除记录

进度机制:
    上传/文本审查时，后端创建 asyncio.Queue 存入 _PROGRESS_QUEUES。
    run_contract_review 用 astream 逐节点 yield 进度事件进队列，
    SSE 端点从队列里读事件推给前端。切页面/刷新后重连 SSE 即可恢复。
==================================================================
"""
import asyncio
import json
import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from src import config, database
from src.api.deps import get_current_user, get_current_user_sse
from src.audit.service import log_audit
from src.subscription.service import check_and_consume_quota
from src.contract.parser import (build_safe_path, detect_contract_type,
                                 extract_text, validate_file)
from src.contract.workflow import run_contract_review, NODE_DISPLAY_NAMES, TOTAL_NODES
from src.utils.text import fix_mojibake

logger = logging.getLogger("zhifatong.api.contract")

router = APIRouter()

# ------------------------------------------------------------------
# 进程内进度队列：contract_id -> asyncio.Queue
# ------------------------------------------------------------------
_PROGRESS_QUEUES: dict[int, asyncio.Queue] = {}


class ReviewTextRequest(BaseModel):
    """直接粘贴合同文本审查（无文件上传场景）。"""
    title: str = "未命名合同"
    contract_text: str


# ==================================================================
# 后台审查任务（异步执行，接口立即返回）
# ==================================================================
async def _run_review_task(contract_id: int, user: dict,
                           contract_text: str, title: str = "") -> None:
    """
    后台执行多 Agent 审查工作流并落库（带进度推送）。

    progress_queue 已在调用前存入 _PROGRESS_QUEUES；
    工作流节点进度由 run_contract_review 内部 astream 推进队列。
    本函数在开始/结束/失败时额外推送"已启动/完成/失败"事件。
    """
    t0 = time.perf_counter()
    q = _PROGRESS_QUEUES.get(contract_id)

    async def _send(event: dict):
        if q is not None:
            try:
                await q.put(event)
            except Exception:
                pass

    try:
        detected_type = detect_contract_type(
            f"{(title or '').strip()}\n{contract_text}") or "其他"
        state = await run_contract_review(
            contract_text, contract_type=detected_type,
            progress_queue=q)
        report = state.get("review_report", {})
        duration = int((time.perf_counter() - t0) * 1000)
        risk_count = (len(state.get("primary_risks") or [])
                      + len((state.get("review_check") or {}).get("missed", []))
                      + len(state.get("unresolved_issues") or []))
        contract_type = report.get("contract_type") or detected_type or "其他"
        database.execute(
            "UPDATE contracts SET status = 'completed', risk_score = %s, "
            "risk_level = %s, clause_count = %s, risk_count = %s, "
            "contract_type = %s, report = %s, duration_ms = %s, "
            "error_msg = NULL WHERE id = %s",
            (float(state.get("risk_score", 0)), state.get("risk_level", "中"),
             len(state.get("clauses") or []), risk_count,
             contract_type,
             json.dumps(report, ensure_ascii=False), duration, contract_id))
        # 最终完成事件
        await _send({
            "node": "done", "node_name": "审查完成",
            "node_index": TOTAL_NODES, "total": TOTAL_NODES, "pct": 100,
            "elapsed_ms": duration, "status": "done",
            "risk_score": float(state.get("risk_score", 0)),
            "risk_level": state.get("risk_level", "中"),
        })
        logger.info("合同审查完成 #%d：得分 %s 等级 %s 耗时 %dms",
                    contract_id, state.get("risk_score"),
                    state.get("risk_level"), duration)
    except Exception as e:
        logger.error("合同审查任务失败 #%d: %s", contract_id, e, exc_info=True)
        database.execute(
            "UPDATE contracts SET status = 'failed', error_msg = %s "
            "WHERE id = %s", (str(e)[:500], contract_id))
        await _send({
            "node": "failed", "node_name": "审查失败",
            "node_index": 0, "total": TOTAL_NODES, "pct": 0,
            "elapsed_ms": 0, "status": "failed",
            "error": str(e)[:200],
        })
    finally:
        # 10 分钟后自动清理队列（防止前端断连队列堆积）
        async def _cleanup():
            await asyncio.sleep(600)
            _PROGRESS_QUEUES.pop(contract_id, None)
        asyncio.create_task(_cleanup())


@router.post("/contract/upload")
async def upload_contract(file: UploadFile, user: dict = Depends(get_current_user)):
    """
    上传合同文件并发起审查。

    校验: 扩展名白名单 + 大小上限 + 文件名路径穿越检测。
    存储: data/uploads/<user_id>/<contract_id>_<安全文件名>。
    稳健性: 文件名乱码修复（latin-1 误码）+ 文本提取异常兜底
    （解析失败返回 400 业务提示而非 500）。
    """
    # ---- 配额检查：合同审查（mode="contract"） ----
    check_and_consume_quota(user, mode="contract")
    raw = await file.read()
    # multipart 文件名乱码修复：浏览器以 UTF-8 发送、HTTP 层按 latin-1 解码
    raw_filename = fix_mojibake(file.filename or "")
    try:
        validate_file(raw_filename, len(raw))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 先落库拿到合同 ID，再构建安全存储路径
    title = raw_filename.rsplit(".", 1)[0][:200] or "未命名合同"
    contract_id = database.insert_return_id(
        "INSERT INTO contracts (user_id, title, file_name, file_path, status, "
        "contract_type) VALUES (%s, %s, %s, %s, 'pending', '其他')",
        (user["user_id"], title, raw_filename, "", ))
    path = build_safe_path(user["user_id"], contract_id, raw_filename or "contract.txt")
    path.write_bytes(raw)
    database.execute(
        "UPDATE contracts SET file_path = %s WHERE id = %s",
        (str(path), contract_id))

    # 提取全文（同步 IO 放线程池；解析异常转 400 业务错误，杜绝 500）
    try:
        contract_text = await asyncio.to_thread(extract_text, str(path))
    except Exception as e:
        logger.warning("合同文本提取失败 #%d (%s): %s",
                       contract_id, raw_filename, e)
        msg = "合同文本提取失败：文件可能已损坏或为扫描件，请另存为 docx/txt 后重试"
        database.execute(
            "UPDATE contracts SET status = 'failed', error_msg = %s WHERE id = %s",
            (msg, contract_id))
        raise HTTPException(status_code=400, detail=msg)
    if not contract_text or len(contract_text.strip()) < 30:
        database.execute(
            "UPDATE contracts SET status = 'failed', error_msg = %s WHERE id = %s",
            ("未能提取有效合同文本（可能是扫描件或空文档）", contract_id))
        raise HTTPException(status_code=400, detail="未能提取有效合同文本")

    # 后台异步审查 + 创建进度队列
    q: asyncio.Queue = asyncio.Queue()
    _PROGRESS_QUEUES[contract_id] = q
    asyncio.create_task(_run_review_task(contract_id, user, contract_text, title))
    log_audit(user["user_id"], user["username"], "UPLOAD", "contract",
              f"上传合同 {raw_filename} (#{contract_id})", None)
    return {
        "contract_id": contract_id, "title": title,
        "message": "已受理，正在执行多 Agent 审查",
        "status": "processing",
        "sse_url": f"/api/contract/{contract_id}/progress",
    }


@router.post("/contract/review-text")
async def review_text(body: ReviewTextRequest, user: dict = Depends(get_current_user)):
    """
    粘贴合同文本直接审查（后台异步，立即返回 progress 订阅地址）。
    前端拿到 contract_id 后立刻订阅 SSE 进度流，不用等完整报告。
    """
    check_and_consume_quota(user, mode="contract")
    if len(body.contract_text.strip()) < 30:
        raise HTTPException(status_code=400, detail="合同文本过短（至少30字）")
    contract_id = database.insert_return_id(
        "INSERT INTO contracts (user_id, title, file_name, file_path, status, "
        "contract_type) VALUES (%s, %s, %s, %s, 'pending', '其他')",
        (user["user_id"], body.title[:200], "-", "inline:text"))
    try:
        inline_dir = config.UPLOAD_DIR / "inline" / str(user["user_id"])
        inline_dir.mkdir(parents=True, exist_ok=True)
        inline_path = inline_dir / f"{contract_id}.txt"
        inline_path.write_text(body.contract_text, encoding="utf-8")
        database.execute("UPDATE contracts SET file_path = %s WHERE id = %s",
                         (str(inline_path), contract_id))
    except Exception as e:
        logger.warning("inline 合同原文持久化失败 #%d: %s", contract_id, e)

    q: asyncio.Queue = asyncio.Queue()
    _PROGRESS_QUEUES[contract_id] = q
    asyncio.create_task(_run_review_task(
        contract_id, user, body.contract_text, body.title))
    log_audit(user["user_id"], user["username"], "REVIEW_TEXT", "contract",
              f"文本审查 {body.title[:100]} (#{contract_id})", None)
    return {
        "contract_id": contract_id, "title": body.title,
        "message": "已受理，正在执行多 Agent 审查",
        "status": "processing",
        "sse_url": f"/api/contract/{contract_id}/progress",
    }


# ------------------------------------------------------------------
# SSE 节点级进度流
# ------------------------------------------------------------------
@router.get("/contract/{contract_id}/progress")
async def contract_progress(contract_id: int,
                            user: dict = Depends(get_current_user_sse)):
    """
    SSE 节点级进度流。

    两种场景:
        1. 审查进行中 —— 从 _PROGRESS_QUEUES[contract_id] 实时读事件
           并推给前端；前端 EventSource 断线自动重连。
        2. 审查已完成/失败 —— 查数据库拿最终状态，一次性推 done/failed
           事件后立即关闭连接。

    每个 SSE 事件是一个 JSON 对象：
        {node, node_name, node_index, total, pct, elapsed_ms, status, ...}
    """
    _own_contract(contract_id, user)   # 归属校验

    async def _stream():
        q = _PROGRESS_QUEUES.get(contract_id)
        if q is None:
            # 任务队列已清理，查数据库最新状态一次性推
            row = database.query_one(
                "SELECT status, risk_score, risk_level, duration_ms, error_msg "
                "FROM contracts WHERE id = %s", (contract_id,))
            if row and row["status"] == "completed":
                yield _sse_event({
                    "node": "done", "node_name": "审查完成",
                    "node_index": TOTAL_NODES, "total": TOTAL_NODES, "pct": 100,
                    "elapsed_ms": row.get("duration_ms", 0), "status": "done",
                    "risk_score": float(row.get("risk_score", 0)),
                    "risk_level": row.get("risk_level", "中"),
                })
            elif row and row["status"] == "failed":
                yield _sse_event({
                    "node": "failed", "node_name": "审查失败",
                    "node_index": 0, "total": TOTAL_NODES, "pct": 0,
                    "elapsed_ms": 0, "status": "failed",
                    "error": (row.get("error_msg") or "")[:200],
                })
            else:
                yield _sse_event({
                    "node": "unknown", "node_name": "进度未知",
                    "node_index": 0, "total": TOTAL_NODES, "pct": 0,
                    "elapsed_ms": 0, "status": "unknown",
                })
            return

        # 正常模式：从队列持续读事件推给前端
        # 超时 120s 没新事件就关（前端 EventSource 自动重连）
        idle_deadline = asyncio.get_event_loop().time() + 120
        while True:
            remaining = idle_deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(q.get(), timeout=remaining)
                idle_deadline = asyncio.get_event_loop().time() + 120
                yield _sse_event(event)
                if event.get("status") in ("done", "failed"):
                    break
            except asyncio.TimeoutError:
                break

    return StreamingResponse(
        _stream(), media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 禁止 Nginx 缓冲 SSE
        })


def _sse_event(obj: dict) -> str:
    """把 dict 序列化为 SSE data 行。"""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.get("/contract/list")
def list_contracts(page: int = 1, size: int = 10,
                   user: dict = Depends(get_current_user)):
    """
    审查记录列表（角色感知数据范围）。

    - 管理员：查看系统内全部角色的合同审查记录（全平台视角）；
    - 法务负责人 / 普通用户：仅本人创建的记录（数据隔离）。
    """
    if user["role"] == "admin":
        where, args = "1=1", ()
    else:
        where, args = "user_id = %s", (user["user_id"],)
    total = database.query_one(
        f"SELECT COUNT(*) AS c FROM contracts WHERE {where}", args)["c"]
    rows = database.query_all(
        f"SELECT c.id, c.title, c.file_name, c.contract_type, c.status, "
        f"c.risk_score, c.risk_level, c.clause_count, c.risk_count, "
        f"c.duration_ms, c.error_msg, c.created_at, u.username, u.real_name "
        f"FROM contracts c JOIN sys_users u ON u.id = c.user_id "
        f"WHERE {where} ORDER BY c.id DESC LIMIT %s OFFSET %s",
        args + (size, (page - 1) * size))
    return {"total": total, "items": rows}


def _own_contract(contract_id: int, user: dict) -> dict:
    """
    校验合同归属（角色感知数据范围）。

    - 管理员：可访问系统内全部角色的合同记录（全平台视角）；
    - 其他角色：仅创建人本人，非本人返回 403（拒绝越权），
      并记录越权告警日志；记录不存在返回 404（不泄露存在性）。
    """
    row = database.query_one(
        "SELECT c.*, u.username FROM contracts c JOIN sys_users u ON u.id = c.user_id "
        "WHERE c.id = %s", (contract_id,))
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row["user_id"] != user["user_id"]:
        if user["role"] != "admin":
            logger.warning(
                "越权访问拦截: 用户 %s(#%d) 试图访问他人合同 #%d（归属用户 #%d）",
                user["username"], user["user_id"], contract_id, row["user_id"])
            raise HTTPException(status_code=403, detail="无权访问该合同")
        logger.info(
            "管理员跨用户访问: %s(#%d) 查看用户 #%d 的合同 #%d",
            user["username"], user["user_id"], row["user_id"], contract_id)
    return row


@router.get("/contract/{contract_id}")
def contract_detail(contract_id: int, user: dict = Depends(get_current_user)):
    """审查报告详情（完整报告：三视角意见/辩论记录/推理链/耗时；仅本人）。"""
    row = _own_contract(contract_id, user)
    # report JSON 反序列化
    if isinstance(row.get("report"), str):
        try:
            row["report"] = json.loads(row["report"])
        except (json.JSONDecodeError, TypeError):
            row["report"] = {}
    return row


@router.get("/contract/{contract_id}/export")
async def export_revised_contract(contract_id: int,
                                  user: dict = Depends(get_current_user)):
    """
    导出 AI 修改后合同（.docx，含修改痕迹标记）。

    流程:
        1. 读取审查报告与原始合同文本（文本审查场景回读 file_path /
           inline 报告 clause 摘要兜底）；
        2. 复用报告中已缓存的修订（report.revisions），
           未缓存时调用 LLM 逐条款真实改写并写回缓存；
        3. python-docx 渲染修改痕迹（高亮/删除线/标签）+ 痕迹清单，
           以附件流返回（RFC 5987 中文文件名）。

    权限: 与报告详情一致（本人或 admin）。
    """
    from src.contract.exporter import (build_revised_docx, generate_revisions,
                                       safe_doc_filename)

    # 数据隔离：仅创建人本人可导出
    row = _own_contract(contract_id, user)
    if row["status"] != "completed":
        raise HTTPException(status_code=400, detail="合同尚未审查完成，无法导出")

    report = row.get("report")
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except (json.JSONDecodeError, TypeError):
            report = {}
    report = report or {}

    # ---- 原始合同文本：文件存储回读，文本审查场景从文件路径/回退标题 ----
    contract_text = ""
    file_path = row.get("file_path") or ""
    if file_path and file_path != "inline:text":
        try:
            from pathlib import Path
            contract_text = await asyncio.to_thread(extract_text, file_path)
        except Exception as e:
            logger.warning("导出回读原文失败 #%d: %s", contract_id, e)

    # ---- 修订生成（报告缓存优先，未缓存则真实 LLM 改写并落库缓存；
    #      缓存键带版本号，修订生成逻辑升级后自动重新生成）----
    revisions = report.get("revisions_v2")
    if not revisions:
        revisions = await generate_revisions(contract_text, report)
        report["revisions_v2"] = revisions
        database.execute(
            "UPDATE contracts SET report = %s WHERE id = %s",
            (json.dumps(report, ensure_ascii=False), contract_id))

    doc_bytes = build_revised_docx(contract_text or row.get("title", "合同"),
                                   report, revisions)
    filename = safe_doc_filename(row.get("title") or f"合同{contract_id}")
    encoded = quote(filename)

    log_audit(user["user_id"], user["username"], "EXPORT", "contract",
              f"导出 AI 修改合同 #{contract_id}", None)
    return Response(
        content=doc_bytes,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"),
        headers={
            # ASCII 兜底 + RFC 5987 UTF-8 文件名（兼容各类浏览器）
            "Content-Disposition":
                f"attachment; filename=\"revised_{contract_id}.docx\"; "
                f"filename*=UTF-8''{encoded}",
            "Cache-Control": "no-store",
        })


@router.delete("/contract/{contract_id}")
def delete_contract(contract_id: int, user: dict = Depends(get_current_user)):
    """
    删除审查记录（操作权限：仅创建人本人，任何角色不可代删他人数据；
    管理员的数据权限为查看全部记录，不含删除操作）。
    """
    row = database.query_one(
        "SELECT id, user_id, file_path FROM contracts WHERE id = %s",
        (contract_id,))
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row["user_id"] != user["user_id"]:
        logger.warning(
            "越权删除拦截: 用户 %s(#%d) 试图删除他人合同 #%d（归属用户 #%d）",
            user["username"], user["user_id"], contract_id, row["user_id"])
        raise HTTPException(status_code=403, detail="无权删除该合同")
    # 同步清理上传文件（容错：文件不存在时忽略）
    try:
        if row["file_path"] and row["file_path"] != "inline:text":
            from pathlib import Path
            Path(row["file_path"]).unlink(missing_ok=True)
    except Exception as e:
        logger.debug("合同文件清理失败: %s", e)
    database.execute("DELETE FROM contracts WHERE id = %s", (contract_id,))
    log_audit(user["user_id"], user["username"], "DELETE", "contract",
              f"删除合同记录 #{contract_id}", None)
    return {"message": "记录已删除"}
