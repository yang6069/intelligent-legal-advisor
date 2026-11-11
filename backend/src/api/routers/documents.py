# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 文书起草路由
==================================================================
路由清单:
    GET    /api/documents/templates     获取文书模板列表（全部角色）
    GET    /api/documents/drafts        我的草稿列表
    POST   /api/documents/drafts       新建空白草稿
    GET    /api/documents/drafts/{id}  草稿详情
    PUT    /api/documents/drafts/{id}  更新草稿
    DELETE /api/documents/drafts/{id}  删除草稿
    POST   /api/documents/ai-draft     AI 自动起草（返回新建草稿）
    POST   /api/documents/ai-rewrite   AI 重写现有草稿（覆盖写回）
    GET    /api/documents/{id}/export  导出（docx/md/txt/html）

数据隔离规则：与 qa_sessions 一致，仅创建者可访问自己的草稿。
==================================================================
"""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.deps import get_current_user
from src.documents import service as doc_service

logger = logging.getLogger("zhifatong.api.documents")

router = APIRouter()


# ==================================================================
# 请求模型
# ==================================================================
class DraftCreateRequest(BaseModel):
    title: str = Field("未命名文书", max_length=255)
    doc_type: str = Field(..., max_length=32, description="文书类型值")
    template_id: str = Field("", max_length=64)
    metadata: dict | None = None


class DraftUpdateRequest(BaseModel):
    title: str | None = Field(None, max_length=255)
    content: str | None = None
    metadata: dict | None = None
    status: str | None = Field(None, max_length=16)
    ai_prompt: str | None = None


class AIDraftRequest(BaseModel):
    doc_type: str = Field(..., max_length=32)
    ai_prompt: str = Field("", description="起草要点 / 事实描述")
    template_id: str = Field("", max_length=64)
    metadata: dict | None = None


class AIRewriteRequest(BaseModel):
    doc_id: int = Field(..., description="待重写的草稿 ID")
    instruction: str = Field("", max_length=2000,
                              description="附加重写要求（可为空）")


# ==================================================================
# 模板
# ==================================================================
@router.get("/documents/templates")
def templates():
    """返回全部文书模板元信息（前端下拉选择）。"""
    return {"items": doc_service.list_templates()}


# ==================================================================
# 草稿 CRUD
# ==================================================================
@router.get("/documents/drafts")
def list_drafts(doc_type: str = Query(""),
                keyword: str = Query(""),
                user: dict = Depends(get_current_user)):
    """我的草稿列表（按更新时间倒序）。"""
    return doc_service.list_drafts(user, doc_type=doc_type, keyword=keyword)


@router.post("/documents/drafts")
def create_draft(body: DraftCreateRequest,
                 user: dict = Depends(get_current_user)):
    """新建空白草稿（可带模板骨架）。"""
    return doc_service.create_draft(
        user, title=body.title, doc_type=body.doc_type,
        template_id=body.template_id, metadata=body.metadata)


@router.get("/documents/drafts/{doc_id}")
def get_draft(doc_id: int,
              user: dict = Depends(get_current_user)):
    """获取草稿详情（含 content 全文）。"""
    return doc_service.get_draft(user, doc_id)


@router.put("/documents/drafts/{doc_id}")
def update_draft(doc_id: int, body: DraftUpdateRequest,
                 user: dict = Depends(get_current_user)):
    """更新草稿（content / title / metadata / status 部分更新）。"""
    kwargs = body.model_dump(exclude_none=True)
    if not kwargs:
        raise HTTPException(status_code=400, detail="未提供更新内容")
    return doc_service.update_draft(user, doc_id, **kwargs)


@router.delete("/documents/drafts/{doc_id}")
def delete_draft(doc_id: int,
                 user: dict = Depends(get_current_user)):
    """删除草稿。"""
    doc_service.delete_draft(user, doc_id)
    return {"message": "已删除"}


# ==================================================================
# AI 起草
# ==================================================================
@router.post("/documents/ai-draft")
async def ai_draft(body: AIDraftRequest,
                   user: dict = Depends(get_current_user)):
    """
    调用 LLM 起草完整法律文书，创建草稿并返回。
    耗时较长（LLM 生成长文书），设长超时。
    """
    return await doc_service.ai_draft(
        user, doc_type=body.doc_type,
        ai_prompt=body.ai_prompt,
        template_id=body.template_id,
        metadata=body.metadata)


@router.post("/documents/ai-rewrite")
async def ai_rewrite(body: AIRewriteRequest,
                     user: dict = Depends(get_current_user)):
    """
    AI 重写：基于现有草稿全文润色/改写，结果覆盖写回原草稿。
    支持附加要求（如"语气更正式""补充违约条款"）。
    """
    return await doc_service.ai_rewrite(
        user, doc_id=body.doc_id, instruction=body.instruction)


# ==================================================================
# 导出
# ==================================================================
@router.get("/documents/{doc_id}/export")
def export_doc(doc_id: int,
               fmt: str = Query("docx", regex="^(docx|md|txt|html)$"),
               user: dict = Depends(get_current_user)):
    """
    导出文书。支持格式: docx / md / txt / html。
    响应 Content-Disposition 触发浏览器下载。
    """
    filename, data, content_type = doc_service.export_doc(user, doc_id, fmt)
    from src.audit.service import log_audit
    log_audit(user["user_id"], user["username"], "DOC_EXPORT", "documents",
              f"导出文书 #{doc_id}（{fmt}）", None)
    return StreamingResponse(
        iter([data]) if isinstance(data, bytes) else iter([data.encode("utf-8")]),
        media_type=content_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{filename.encode('utf-8').hex()}"
                if False else f"attachment; filename={filename}"
            ),
        },
    )
