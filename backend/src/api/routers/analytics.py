# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 数据分析路由（全部角色可用，数据范围角色感知）
==================================================================
路由清单:
    GET /api/analytics/dashboard         仪表盘总览
    GET /api/analytics/recent-contracts  最近审查记录（分页）
    GET /api/analytics/hot-topics        咨询热点
    GET /api/analytics/citations         法条引用
    GET /api/analytics/clusters          用户聚类（仅管理员）

多用户数据范围模型：
    - 管理员：全平台全部角色汇总业务数据（合同审查记录总数 /
      咨询总数 / 全局图表），满足全局运营视角；
    - 法务 / 普通用户：仅本人产生的数据（数据隔离）；
    - 用户聚类分析对象是账号群体，属系统级运营功能，仅管理员可用。
==================================================================
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from src.api.deps import require_module
from src.analytics import service as analytics_service

logger = logging.getLogger("zhifatong.api.analytics")

# 模块鉴权：全部角色（数据范围在服务层按角色区分）
analytics_guard = require_module("analytics")
router = APIRouter()


@router.get("/analytics/dashboard")
def dashboard(page: int = 1, size: int = 5,
              user: dict = Depends(analytics_guard)):
    """
    仪表盘统计总览。

    管理员 = 全平台汇总（合同审查记录总数 / 咨询总数）；
    法务 / 普通用户 = 仅本人数据。
    """
    return analytics_service.dashboard_stats(
        user["user_id"], is_admin=(user["role"] == "admin"))


@router.get("/analytics/recent-contracts")
def recent_contracts(page: int = 1, size: int = 5,
                     user: dict = Depends(analytics_guard)):
    """
    最近合同审查记录（分页，仪表盘表格专用）。

    管理员 = 全部角色记录；其他角色 = 仅本人记录。
    """
    return analytics_service.recent_contracts_page(
        user["user_id"], page=page, size=size,
        is_admin=(user["role"] == "admin"))


@router.get("/analytics/hot-topics")
def hot_topics(days: int = 30, user: dict = Depends(analytics_guard)):
    """
    法律咨询热点分析。

    管理员 = 全平台提问统计；其他角色 = 仅本人提问。
    返回: 领域分布（饼图）/ 日咨询趋势（折线）/ 热点关键词（词云）。
    """
    if days < 1 or days > 365:
        days = 30
    return analytics_service.analyze_hot_topics(
        user["user_id"], days=days, is_admin=(user["role"] == "admin"))


@router.get("/analytics/citations")
def citations(user: dict = Depends(analytics_guard)):
    """
    法条引用统计分析。

    管理员 = 全平台回答引用；其他角色 = 仅本人回答引用。
    返回: 高频引用法条 Top20（柱图）/ 共引网络（关系图）。
    """
    return analytics_service.analyze_citation_patterns(
        user["user_id"], is_admin=(user["role"] == "admin"))


@router.get("/analytics/clusters")
def clusters(n_clusters: int = 4, user: dict = Depends(analytics_guard)):
    """
    用户聚类分析（K-Means）—— 仅系统管理员可用。

    分析对象是全平台用户账号群体（系统级运营功能）；
    法务负责人/普通用户访问返回 403（防止横向暴露他人业务行为数据）。
    """
    if user["role"] != "admin":
        logger.warning(
            "越权访问拦截: 用户 %s(#%d, %s) 试图访问用户聚类分析",
            user["username"], user["user_id"], user["role"])
        raise HTTPException(status_code=403, detail="用户聚类分析仅系统管理员可用")
    if n_clusters < 2 or n_clusters > 6:
        n_clusters = 4
    return analytics_service.cluster_users(n_clusters=n_clusters)
