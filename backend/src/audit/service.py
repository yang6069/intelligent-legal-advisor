# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 审计日志服务
==================================================================
模块职责：
    全操作留痕（结构化记录：用户/动作/模块/详情/IP），
    满足《网络安全法》日志留存 >= 180 天的合规要求。
==================================================================
"""
import logging

from src import database

logger = logging.getLogger("zhifatong.audit")


def log_audit(user_id: int | None, username: str | None,
              action: str, module: str, detail: str, ip: str | None = None) -> None:
    """
    记录一条审计日志（异步安全：失败仅记警告，不影响主流程）。

    参数:
        user_id: 操作用户 ID（匿名操作为 None）
        username: 用户名（冗余存储，防止用户删除后无法追溯）
        action: 动作标识（LOGIN / QUERY / UPLOAD / DELETE ...）
        module: 所属模块（auth / qa / contract / kb / mcp / users ...）
        detail: 操作详情（脱敏后的明文描述）
        ip: 客户端 IP
    """
    try:
        database.execute(
            "INSERT INTO audit_logs (user_id, username, action, module, detail, ip) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, username or "anonymous", action, module,
             (detail or "")[:1000], ip or "unknown"),
        )
    except Exception as e:
        logger.warning("审计日志写入失败: %s", e)


def list_logs(page: int = 1, size: int = 20, module: str = "",
              action: str = "", user_id: int | None = None) -> dict:
    """
    分页查询审计日志，支持模块/动作/用户过滤（管理员界面使用）。
    """
    conds, args = ["1=1"], []
    if module:
        conds.append("module = %s")
        args.append(module)
    if action:
        conds.append("action = %s")
        args.append(action)
    if user_id:
        conds.append("user_id = %s")
        args.append(user_id)
    where = " AND ".join(conds)
    args = tuple(args)
    total = database.query_one(f"SELECT COUNT(*) AS c FROM audit_logs WHERE {where}", args)["c"]
    rows = database.query_all(
        f"SELECT al.*, u.real_name FROM audit_logs al "
        f"LEFT JOIN sys_users u ON u.id = al.user_id "
        f"WHERE {where} ORDER BY al.id DESC LIMIT %s OFFSET %s",
        args + (size, (page - 1) * size),
    )
    return {"total": total, "items": rows}
