# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 前端字典路由（业务枚举 / 选项 / 提示文案唯一下发口）
==================================================================
路由清单:
    GET /api/meta/dictionaries   全量业务字典（公开接口）

职责:
    前端所有页面"严禁硬编码业务数据"——下拉选项、枚举标签、
    工作流阶段、快捷提问、演示账号提示等全部由本接口动态下发，
    单一数据源为 src/config.py 业务字典中心（需求：数据驱动动态渲染）。

快捷提问数据策略:
    1. 优先从 qa_messages 提取近 30 天真实高频用户提问
       （去重、8~40 字的完整问句，按出现次数取 Top3）；
    2. 无数据 / 查询失败时回退 config.DEFAULT_QUICK_QUESTIONS。
==================================================================
"""
import logging
from collections import Counter

from fastapi import APIRouter

from src import config, database

logger = logging.getLogger("zhifatong.api.meta")

router = APIRouter()


def _hot_quick_questions(limit: int = 3) -> list[str]:
    """
    从真实咨询记录提取高频提问（数据驱动的快捷提问）。

    规则:
        近 30 天 role='user' 消息 -> 去首尾空白 -> 8~40 字视为完整问句
        -> 完全去重 -> 按频次 Top N。不足 limit 时由默认项补齐。
    """
    try:
        rows = database.query_all(
            "SELECT content FROM qa_messages "
            "WHERE role = 'user' AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
            "ORDER BY created_at DESC LIMIT 500")
        counter = Counter()
        for row in rows:
            text = (row.get("content") or "").strip()
            if 8 <= len(text) <= 40:
                counter[text] += 1
        hot = [q for q, _ in counter.most_common(limit)]
    except Exception as e:
        logger.warning("快捷提问统计降级: %s", e)
        hot = []
    # 不足时用默认项补齐（保持 3 条，保证引导区不空）
    for q in config.DEFAULT_QUICK_QUESTIONS:
        if len(hot) >= limit:
            break
        if q not in hot:
            hot.append(q)
    return hot[:limit]


@router.get("/meta/dictionaries")
def dictionaries():
    """
    全量业务字典（公开：登录页演示提示也需要）。

    返回:
        doc_types         知识库文档类型 [{value,label,tag}]
        roles             用户角色选项 [{value,label,tag}]
        audit_modules     审计模块 [{value,label}]
        audit_actions     审计行为枚举 [str]
        contract_stages   合同审查工作流阶段 [str]
        analytics_days    分析时间范围选项 [int]
        cluster_features  聚类特征中文标签 {key: label}
        quick_questions   智能问答快捷提问（真实高频数据驱动）[str]
        login_hint        登录页演示账号提示（由内置账号配置拼装）
    """
    hint = "演示账号：" + "、".join(
        f"{a['username']} / {a['password']}（{a['role']}）"
        for a in config.DEMO_ACCOUNTS)
    return {
        "doc_types": config.DOC_TYPES,
        "roles": config.ROLE_OPTIONS,
        "audit_modules": config.AUDIT_MODULES,
        "audit_actions": config.AUDIT_ACTIONS,
        "contract_stages": config.CONTRACT_STAGES,
        "analytics_days": config.ANALYTICS_DAYS,
        "cluster_features": config.CLUSTER_FEATURE_LABELS,
        "quick_questions": _hot_quick_questions(),
        "login_hint": hint,
    }
