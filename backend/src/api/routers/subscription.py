# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 用户套餐配额路由
==================================================================
路由清单:
    GET  /api/quota/status         获取当前用户配额状态（前端展示）
    GET  /api/quota/plans          可用套餐列表（公共）
    POST /api/quota/purchase       用户购买套餐（模拟支付）
    GET  /api/quota/admin/list     管理员查看全部用户配额（admin）
    POST /api/quota/admin/grant    管理员调整指定用户配额（admin）
    GET  /api/quota/admin/orders   管理员查看订单列表（admin）
==================================================================
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from src import database
from src.api.deps import get_current_user, require_role_level
from src.subscription import service as quota_service

router = APIRouter()


# ==================================================================
# 请求模型
# ==================================================================
class PurchaseRequest(BaseModel):
    plan_type: str = Field(..., description="套餐类型：basic / pro / elite")


class AdminGrantRequest(BaseModel):
    user_id: int = Field(..., description="目标用户ID")
    total_balance: int = Field(..., ge=0, description="调整后的总余额")
    duration_days: int = Field(30, ge=1, le=3650, description="有效期天数")


# ==================================================================
# 用户侧
# ==================================================================
@router.get("/quota/status")
def quota_status(user: dict = Depends(get_current_user)):
    """获取当前登录用户的配额状态。"""
    return quota_service.get_quota_status(user)


@router.get("/quota/plans")
def plan_list():
    """返回所有可用的付费套餐列表（公开）。"""
    return {"items": quota_service.public_plan_list()}


@router.post("/quota/purchase")
def purchase(body: PurchaseRequest, user: dict = Depends(get_current_user)):
    """购买指定套餐（当前为模拟实现；真实生产接入支付回调）。"""
    return quota_service.purchase_plan(user, body.plan_type)


# ==================================================================
# 管理员侧
# ==================================================================
@router.get("/quota/admin/list",
            dependencies=[Depends(require_role_level(3))])
def admin_list(page: int = Query(1, ge=1),
               size: int = Query(20, ge=1, le=100),
               keyword: str = Query("")):
    """管理员查看所有用户配额状态。"""
    return quota_service.admin_list_quotas(page, size, keyword)


@router.post("/quota/admin/grant",
             dependencies=[Depends(require_role_level(3))])
def admin_grant(body: AdminGrantRequest,
                user: dict = Depends(get_current_user)):
    """管理员手动调整/赠送用户配额。"""
    result = quota_service.admin_grant_quota(
        body.user_id, body.total_balance, body.duration_days)
    return result


@router.get("/quota/admin/orders",
            dependencies=[Depends(require_role_level(3))])
def admin_orders(page: int = Query(1, ge=1),
                 size: int = Query(20, ge=1, le=100)):
    """管理员查看套餐购买订单列表。"""
    total_row = database.query_one(
        "SELECT COUNT(*) AS c FROM plan_orders")
    rows = database.query_all(
        "SELECT * FROM plan_orders ORDER BY paid_at DESC LIMIT %s OFFSET %s",
        (size, (page - 1) * size))
    return {"total": total_row["c"], "items": rows}
