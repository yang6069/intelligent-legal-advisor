# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 知识库管理路由（legal_admin / admin）
==================================================================
路由清单:
    GET    /api/knowledge/docs          文档分页列表（类型/关键词过滤）
    POST   /api/knowledge/docs          新增文档（同步三索引：向量+BM25+图谱）
    GET    /api/knowledge/docs/{id}     文档全文
    PUT    /api/knowledge/docs/{id}     更新文档（重建索引）
    DELETE /api/knowledge/docs/{id}     删除文档
    POST   /api/knowledge/rebuild       全量重建索引
    GET    /api/knowledge/stats         知识库统计
    GET    /api/knowledge/graph         图谱可视化数据（ECharts 力导向图）
==================================================================
"""
import asyncio
import logging
import os
import tempfile

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from src import database
from src.api.deps import require_module
from src.audit.service import log_audit
from src.contract.parser import extract_text, validate_file
from src.rag.index_sync import publish_kb_index_changed
from src.rag.knowledge_service import knowledge_service
from src.utils.text import fix_mojibake

logger = logging.getLogger("zhifatong.api.kb")

# 模块鉴权：legal_admin 及以上
kb_guard = require_module("knowledge")
router = APIRouter()


# ==================================================================
# 请求模型
# ==================================================================
class DocCreateRequest(BaseModel):
    """知识库文档创建/更新请求。"""
    doc_type: str = Field(..., pattern="^(law|case|interpretation|template)$",
                          description="文档类型")
    title: str = Field(..., min_length=2, max_length=255)
    law_name: str = Field("", max_length=128)
    article_no: str = Field("", max_length=64)
    content: str = Field(..., min_length=10)
    effectiveness: str = Field("有效", pattern="^(有效|已废止|部分修订)$")
    publish_date: str | None = None


# ==================================================================
# 文档 CRUD
# ==================================================================
@router.get("/knowledge/docs")
def list_docs(page: int = 1, size: int = 20, doc_type: str = "",
              keyword: str = "", user: dict = Depends(kb_guard)):
    """知识库文档分页查询。"""
    return knowledge_service.list_docs(page=page, size=size,
                                        doc_type=doc_type, keyword=keyword)


@router.post("/knowledge/docs")
def create_doc(body: DocCreateRequest, user: dict = Depends(kb_guard)):
    """
    新增知识库文档（自动同步三路索引）。

    索引链路: 法律感知分块 -> BM25 内存索引 -> Chroma 向量库 -> Neo4j 图谱。
    """
    try:
        doc_id = knowledge_service.create_doc(
            body.doc_type, body.title, body.content,
            law_name=body.law_name, article_no=body.article_no,
            effectiveness=body.effectiveness,
            publish_date=body.publish_date, created_by=user["user_id"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("文档创建失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"文档创建失败: {e}")
    publish_kb_index_changed()
    log_audit(user["user_id"], user["username"], "CREATE", "kb",
              f"新增知识库文档 {body.title} (#{doc_id})", None)
    return {"doc_id": doc_id, "message": "文档已入库并建立索引"}


@router.post("/knowledge/docs/upload")
async def upload_doc(
    file: UploadFile,
    doc_type: str = Form("law"),
    title: str = Form(""),
    law_name: str = Form(""),
    article_no: str = Form(""),
    effectiveness: str = Form("有效"),
    publish_date: str = Form(""),
    user: dict = Depends(kb_guard),
):
    """
    上传文档文件入库（docx/pdf/txt/md，自动提取全文并同步三路索引）。

    流程: 安全校验 -> 临时落盘 -> 文本提取 -> 标题兜底（文件名）
          -> create_doc 入库（分块 + 向量 + BM25 + 图谱）。
    稳健性: 解析失败返回 400 业务提示；临时文件 finally 清理。
    """
    raw = await file.read()
    raw_filename = fix_mojibake(file.filename or "")
    try:
        validate_file(raw_filename, len(raw))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 临时文件落盘后按扩展名分派解析器（与合同上传同一套提取链路）
    suffix = os.path.splitext(raw_filename)[1].lower() or ".txt"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        content = await asyncio.to_thread(extract_text, tmp_path)
    except Exception as e:
        logger.warning("知识库文档文本提取失败 (%s): %s", raw_filename, e)
        raise HTTPException(
            status_code=400,
            detail="文档文本提取失败：文件可能已损坏或为扫描件，"
                   "请另存为 docx/txt 后重试")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    content = (content or "").strip()
    if len(content) < 10:
        raise HTTPException(status_code=400,
                            detail="未能提取到有效正文（可能是空文档或扫描件）")

    # 标题未填时以文件名（去扩展名）兜底
    doc_title = (title or "").strip() or (
        raw_filename.rsplit(".", 1)[0][:200] or "未命名文档")
    if doc_type not in ("law", "case", "interpretation", "template"):
        doc_type = "law"
    if effectiveness not in ("有效", "已废止", "部分修订"):
        effectiveness = "有效"

    try:
        # create_doc 含向量化耗时操作，放线程池避免阻塞事件循环
        doc_id = await asyncio.to_thread(
            knowledge_service.create_doc,
            doc_type, doc_title, content,
            law_name=law_name, article_no=article_no,
            effectiveness=effectiveness,
            publish_date=publish_date or None,
            created_by=user["user_id"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("上传文档入库失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"文档入库失败: {e}")

    publish_kb_index_changed()
    log_audit(user["user_id"], user["username"], "UPLOAD", "kb",
              f"上传知识库文档 {raw_filename} -> {doc_title} (#{doc_id})", None)
    return {"doc_id": doc_id, "message": "文档已上传入库并建立索引",
            "char_count": len(content)}


@router.get("/knowledge/docs/{doc_id}")
def get_doc(doc_id: int, user: dict = Depends(kb_guard)):
    """查询单个文档全文。"""
    doc = knowledge_service.get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return doc


@router.put("/knowledge/docs/{doc_id}")
def update_doc(doc_id: int, body: DocCreateRequest,
               user: dict = Depends(kb_guard)):
    """更新文档（内容变更后触发全量索引重建）。"""
    doc = knowledge_service.get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    database.execute(
        "UPDATE kb_documents SET doc_type = %s, title = %s, law_name = %s, "
        "article_no = %s, content = %s, effectiveness = %s, publish_date = %s, "
        "char_count = %s WHERE id = %s",
        (body.doc_type, body.title, body.law_name or body.title,
         body.article_no, body.content, body.effectiveness,
         body.publish_date, len(body.content), doc_id))
    # 内容变更 -> 全量重建索引（保证三路索引一致）
    knowledge_service.rebuild_all()
    publish_kb_index_changed()
    log_audit(user["user_id"], user["username"], "UPDATE", "kb",
              f"更新知识库文档 {body.title} (#{doc_id})", None)
    return {"message": "文档已更新，索引已重建"}


@router.delete("/knowledge/docs/{doc_id}")
def delete_doc(doc_id: int, user: dict = Depends(kb_guard)):
    """删除文档（自动重建索引）。"""
    doc = knowledge_service.get_doc(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    knowledge_service.delete_doc(doc_id)
    publish_kb_index_changed()
    log_audit(user["user_id"], user["username"], "DELETE", "kb",
              f"删除知识库文档 {doc['title']} (#{doc_id})", None)
    return {"message": "文档已删除，索引已重建"}


# ==================================================================
# 索引管理
# ==================================================================
@router.post("/knowledge/rebuild")
async def rebuild_index(user: dict = Depends(kb_guard)):
    """
    全量重建三路索引（向量 + BM25 + 图谱）。

    用途: 索引损坏修复 / 批量导入后统一重建 / 模型切换后重建。
    """
    try:
        # 重建含向量化耗时操作，放入线程池避免阻塞事件循环
        stats = await asyncio.to_thread(knowledge_service.rebuild_all)
        publish_kb_index_changed()
        log_audit(user["user_id"], user["username"], "REBUILD", "kb",
                  f"重建索引: {stats}", None)
        return {"message": "索引重建完成", "stats": stats}
    except Exception as e:
        logger.error("索引重建失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"索引重建失败: {e}")


@router.get("/knowledge/stats")
def kb_stats(user: dict = Depends(kb_guard)):
    """
    知识库统计（各类型文档量 / 字符量 / 三路索引状态）。

    三路索引统计逐项容错：任一索引异常时该项返回 0，
    统计接口永不 500（避免页面错误弹窗）。
    """
    row = database.query_one(
        "SELECT COUNT(*) AS total, SUM(char_count) AS chars, "
        "SUM(CASE WHEN doc_type='law' THEN 1 ELSE 0 END) AS laws, "
        "SUM(CASE WHEN doc_type='case' THEN 1 ELSE 0 END) AS cases, "
        "SUM(CASE WHEN doc_type='interpretation' THEN 1 ELSE 0 END) AS interps, "
        "SUM(CASE WHEN doc_type='template' THEN 1 ELSE 0 END) AS templates "
        "FROM kb_documents")

    def _safe(fn):
        """单项统计容错（索引瞬断/句柄失效时返回 0，不阻断整体）。"""
        try:
            return fn()
        except Exception as e:
            logger.warning("索引统计项异常（降级为 0）: %s", e)
            return 0

    from src.rag.vector_store import dense_retriever
    from src.rag.sparse_retriever import bm25_index
    from src.rag import graph_store
    graph = _safe(
        lambda: graph_store.graph_stats() if graph_store.ensure_graph() else {}
    ) or {}
    return {
        "total_docs": row["total"] or 0, "total_chars": row["chars"] or 0,
        "laws": row["laws"] or 0, "cases": row["cases"] or 0,
        "interpretations": row["interps"] or 0, "templates": row["templates"] or 0,
        "vector_count": _safe(dense_retriever.count),
        "bm25_count": _safe(lambda: len(bm25_index)),
        "graph_nodes": graph.get("nodes", 0),
        "graph_edges": graph.get("edges", 0),
    }


@router.get("/knowledge/graph")
def graph_visualization(limit: int = 80, user: dict = Depends(kb_guard)):
    """
    知识图谱可视化数据（法律-法条-案例-概念关联网络）。

    返回 ECharts graph series 需要的 nodes/links/categories 结构。
    """
    from src.rag import graph_store
    if not graph_store.ensure_graph():
        # 图谱不可用时返回空结构（前端展示降级提示）
        return {"available": False, "nodes": [], "links": [], "categories": []}
    return graph_store.export_visualization(limit=limit)
