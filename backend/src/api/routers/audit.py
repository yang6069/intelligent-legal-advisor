# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 审计日志路由（admin 专属）
==================================================================
路由清单:
    GET /api/audit/logs   审计日志分页查询（模块/动作/用户过滤）
==================================================================
"""
import logging

from fastapi import APIRouter, Depends

from src.api.deps import require_role_level
from src.audit.service import list_logs

logger = logging.getLogger("zhifatong.api.audit")

router = APIRouter()


@router.get("/audit/logs")
def audit_logs(page: int = 1, size: int = 20, module: str = "",
               action: str = "", user_id: int | None = None,
               user: dict = Depends(require_role_level(3))):
    """
    审计日志查询（合规审计界面）。

    过滤维度: 模块（auth/qa/contract/kb/mcp/users...）、
    动作（LOGIN/UPLOAD/DELETE...）、用户 ID。
    """
    if size < 1 or size > 100:
        size = 20
    return list_logs(page=page, size=size, module=module,
                     action=action, user_id=user_id)
