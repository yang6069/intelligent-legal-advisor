# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 用户套餐配额服务（问答 + 合同审查 双功能共用余额池）
==================================================================
模块职责：
    1. 为普通用户（role=user）提供双功能免费每日额度 + 付费套餐
       统一余额池的检查、扣减、日末自动重置；
    2. 套餐购买、有效期管理、余额查询；
    3. admin / legal_admin 不受限制（跳过全部检查）。

配额模型（2026-09-24 调整）：
    - 问答免费：20 次/天（自然日 0 点重置）
    - 合同审查免费：10 次/天（独立计数，自然日 0 点重置）
    - 付费套餐：30 天内统一余额池（问答和合同审查共用，先扣免费再扣余额）
    - 付费余额消耗完毕或过期 -> 自动回退到 free。

设计要点：
    - 扣减逻辑统一入口 check_and_consume_quota(mode='qa'|'contract')；
    - 日重置使用"日期比对+UPDATE"方式，不依赖定时任务；
    - 并发安全：UPDATE ... WHERE used < limit / WHERE balance > 0；
    - 套餐信息 + 扣减记录落 MySQL，Redis 仅作可选缓存。
==================================================================
"""
import datetime as _dt
import logging
import uuid

from fastapi import HTTPException, status

from src import config, database
from src.audit.service import log_audit

logger = logging.getLogger("zhifatong.subscription")


# ==================================================================
# 一、套餐定义
# ==================================================================
PLAN_DEFINITIONS = {
    "free": {
        "label": "免费版",
        "price": 0,
        "qa_daily_free": 20,       # 免费版每日问答
        "contract_daily_free": 10, # 免费版每日合同审查
        "bonus_quota": 0,          # 统一付费余额池（问答+审查共用）
        "duration_days": 0,
        "tag": "info",
    },
    "basic": {
        "label": "基础套餐",
        "price": 39,
        "qa_daily_free": 0,        # 付费期内不限每日问答，从 bonus_quota 扣
        "contract_daily_free": 0,  # 付费期内不限每日合同审查，从 bonus_quota 扣
        "bonus_quota": 500,        # 统一余额池
        "duration_days": 30,
        "tag": "success",
    },
    "pro": {
        "label": "专业套餐",
        "price": 59,
        "qa_daily_free": 0,
        "contract_daily_free": 0,
        "bonus_quota": 1000,
        "duration_days": 30,
        "tag": "warning",
    },
    "elite": {
        "label": "精英套餐",
        "price": 99,
        "qa_daily_free": 0,
        "contract_daily_free": 0,
        "bonus_quota": 2000,
        "duration_days": 30,
        "tag": "danger",
    },
}


def public_plan_list() -> list[dict]:
    """对外展示的套餐列表。"""
    return [
        {
            "plan_type": k,
            "label": v["label"],
            "price": v["price"],
            # 统一余额池 + 叠加免费
            "bonus_quota": v["bonus_quota"],
            "qa_daily_free": v["qa_daily_free"],
            "contract_daily_free": v["contract_daily_free"],
            "duration_days": v["duration_days"],
            "tag": v["tag"],
        }
        for k, v in PLAN_DEFINITIONS.items()
    ]


# ==================================================================
# 二、确保用户有配额记录（首次查询时自动创建）
# ==================================================================
def _ensure_quota(user_id: int) -> dict:
    row = database.query_one(
        "SELECT * FROM sys_user_quota WHERE user_id = %s", (user_id,))
    if row is None:
        database.execute(
            "INSERT INTO sys_user_quota (user_id, plan_type, daily_free_date, daily_contract_date) "
            "VALUES (%s, 'free', CURDATE(), CURDATE())",
            (user_id,))
        row = database.query_one(
            "SELECT * FROM sys_user_quota WHERE user_id = %s", (user_id,))
    # 对老记录（ALTER 前已存在）确保新列有值
    if row.get("daily_contract_used") is None:
        database.execute(
            "UPDATE sys_user_quota SET daily_contract_used = 0, daily_contract_date = CURDATE() WHERE id = %s",
            (row["id"],))
        row = database.query_one("SELECT * FROM sys_user_quota WHERE id = %s", (row["id"],))
    return row


# ==================================================================
# 三、日末重置 + 过期套餐回退
# ==================================================================
def _refresh_quota_state(row: dict) -> dict:
    """
    检查配额记录是否需要刷新：
        1. 自然日变更 -> QA / Contract 的 daily_used 归零；
        2. 付费套餐过期且余额耗尽 -> 回退 free。
    """
    updated = False
    today = _dt.date.today()

    daily_date = row.get("daily_free_date")
    if daily_date is None or (isinstance(daily_date, _dt.date) and daily_date != today):
        database.execute(
            "UPDATE sys_user_quota SET daily_free_used = 0, daily_free_date = CURDATE() "
            "WHERE id = %s", (row["id"],))
        updated = True

    contract_date = row.get("daily_contract_date")
    if contract_date is None or (isinstance(contract_date, _dt.date) and contract_date != today):
        database.execute(
            "UPDATE sys_user_quota SET daily_contract_used = 0, daily_contract_date = CURDATE() "
            "WHERE id = %s", (row["id"],))
        updated = True

    plan_expire = row.get("plan_expire_at")
    if (row["plan_type"] != "free"
            and plan_expire is not None
            and isinstance(plan_expire, _dt.datetime)
            and plan_expire < _dt.datetime.now()):
        database.execute(
            "UPDATE sys_user_quota SET plan_type = 'free', plan_expire_at = NULL "
            "WHERE id = %s", (row["id"],))
        updated = True
        logger.info("用户 #%d 套餐已过期，已自动回退 free", row["user_id"])

    if updated:
        row = database.query_one(
            "SELECT * FROM sys_user_quota WHERE id = %s", (row["id"],))
    return row


# ==================================================================
# 四、配额消费 —— 统一入口（QA / Contract 共用）
# ==================================================================
def check_and_consume_quota(user: dict, mode: str = "qa") -> None:
    """
    检查用户配额，有则扣减，无则抛 429。

    参数:
        mode: "qa" 问答，"contract" 合同审查

    admin / legal_admin 直接跳过（无限制）。
    扣减顺序：付费余额优先（不占免费额度），用完再扣每日免费额度。
    """
    if user["role"] != "user":
        return
    if mode not in ("qa", "contract"):
        raise ValueError(f"未知配额模式 {mode}")

    row = _ensure_quota(user["user_id"])
    row = _refresh_quota_state(row)

    now = _dt.datetime.now()
    plan = PLAN_DEFINITIONS[row["plan_type"]]

    # ---- 路径 A：付费套餐余额充足且未过期 ----
    if (row["plan_type"] != "free"
            and (row["plan_expire_at"] is None or row["plan_expire_at"] > now)
            and row["total_balance"] > 0):
        affected = database.execute(
            "UPDATE sys_user_quota SET total_balance = total_balance - 1 "
            "WHERE id = %s AND total_balance > 0", (row["id"],))
        if affected:
            return

    # ---- 路径 B：每日免费额度 ----
    if mode == "qa":
        daily_limit = plan.get("qa_daily_free", config.FREE_DAILY_QA_LIMIT)
        affected = database.execute(
            "UPDATE sys_user_quota SET daily_free_used = daily_free_used + 1 "
            "WHERE id = %s AND daily_free_used < %s", (row["id"], daily_limit))
    else:  # contract
        daily_limit = plan.get("contract_daily_free", config.FREE_DAILY_CONTRACT_LIMIT)
        affected = database.execute(
            "UPDATE sys_user_quota SET daily_contract_used = daily_contract_used + 1 "
            "WHERE id = %s AND daily_contract_used < %s", (row["id"], daily_limit))

    if affected:
        return

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=_build_exhausted_message(row, plan, mode),
    )


def _build_exhausted_message(row: dict, plan: dict, mode: str) -> str:
    """生成额度用尽提示。"""
    mode_name = "问答" if mode == "qa" else "合同审查"
    if row["plan_type"] != "free" and row["total_balance"] == 0:
        return f"您当前付费套餐的{mode_name}次数已用完，可前往「我的套餐」购买更多次数。"
    if mode == "qa":
        daily_limit = plan.get("qa_daily_free", config.FREE_DAILY_QA_LIMIT)
    else:
        daily_limit = plan.get("contract_daily_free", config.FREE_DAILY_CONTRACT_LIMIT)
    return (f"今日免费{mode_name}次数已用完（{daily_limit} 次/天），"
            f"明天将自动恢复，或前往「我的套餐」购买次数。")


# ==================================================================
# 五、查询用户额度状态（前端展示用，返回 QA + Contract 两项）
# ==================================================================
def get_quota_status(user: dict) -> dict:
    if user["role"] != "user":
        return {
            "unlimited": True,
            "plan_type": "unlimited",
            "plan_label": "无限制",
            "qa": {"daily_limit": 0, "daily_used": 0, "daily_remaining": 0},
            "contract": {"daily_limit": 0, "daily_used": 0, "daily_remaining": 0},
            "total_balance": 0,
            "plan_expire_at": None,
        }

    row = _ensure_quota(user["user_id"])
    row = _refresh_quota_state(row)
    plan = PLAN_DEFINITIONS.get(row["plan_type"], PLAN_DEFINITIONS["free"])

    qa_daily_limit = plan.get("qa_daily_free", config.FREE_DAILY_QA_LIMIT)
    qa_daily_used = row["daily_free_used"] or 0
    contract_daily_limit = plan.get("contract_daily_free", config.FREE_DAILY_CONTRACT_LIMIT)
    contract_daily_used = row.get("daily_contract_used") or 0

    # 付费套餐期内有余额 -> 前端显示"不限"每日额度
    has_bonus = row["plan_type"] != "free" and row["total_balance"] > 0

    return {
        "unlimited": False,
        "plan_type": row["plan_type"],
        "plan_label": plan["label"],
        "qa": {
            "daily_limit": -1 if has_bonus else qa_daily_limit,
            "daily_used": qa_daily_used,
            "daily_remaining": (qa_daily_limit - qa_daily_used) if not has_bonus else -1,
        },
        "contract": {
            "daily_limit": -1 if has_bonus else contract_daily_limit,
            "daily_used": contract_daily_used,
            "daily_remaining": (contract_daily_limit - contract_daily_used) if not has_bonus else -1,
        },
        "total_balance": row["total_balance"] or 0,
        "plan_expire_at": row.get("plan_expire_at"),
        "tip": _build_tip(row, plan),
    }


def _build_tip(row: dict, plan: dict) -> str:
    if row["plan_type"] != "free" and row["total_balance"] > 0:
        expire = row.get("plan_expire_at")
        expire_str = expire.strftime("%Y-%m-%d") if expire else "永久"
        return f"{plan['label']} · 剩余 {row['total_balance']} 次 · 问答+合同审查共用 · 有效期至 {expire_str}"
    qa = plan.get("qa_daily_free", config.FREE_DAILY_QA_LIMIT)
    ctr = plan.get("contract_daily_free", config.FREE_DAILY_CONTRACT_LIMIT)
    qa_rem = qa - (row["daily_free_used"] or 0)
    ctr_rem = ctr - (row.get("daily_contract_used") or 0)
    return f"免费版 · 问答剩 {qa_rem}/{qa} · 审查剩 {ctr_rem}/{ctr}"


# ==================================================================
# 六、购买套餐（模拟支付）
# ==================================================================
def purchase_plan(user: dict, plan_type: str) -> dict:
    if user["role"] != "user":
        raise HTTPException(status_code=400, detail="管理员/法务无需购买套餐")
    if plan_type == "free":
        raise HTTPException(status_code=400, detail="免费版无需购买")
    if plan_type not in PLAN_DEFINITIONS:
        raise HTTPException(status_code=400, detail="非法套餐类型")

    plan = PLAN_DEFINITIONS[plan_type]
    row = _ensure_quota(user["user_id"])
    row = _refresh_quota_state(row)

    now = _dt.datetime.now()
    new_balance = row["total_balance"] + plan["bonus_quota"]

    if (row["plan_type"] != "free"
            and row.get("plan_expire_at")
            and row["plan_expire_at"] > now):
        new_expire = row["plan_expire_at"] + _dt.timedelta(days=plan["duration_days"])
    else:
        new_expire = now + _dt.timedelta(days=plan["duration_days"])

    database.execute(
        "UPDATE sys_user_quota SET plan_type = %s, total_balance = %s, "
        "total_purchased = total_purchased + %s, plan_expire_at = %s, "
        "last_purchase_at = NOW() WHERE id = %s",
        (plan_type, new_balance, plan["bonus_quota"], new_expire, row["id"]))

    order_no = f"ZFT{uuid.uuid4().hex[:16].upper()}"
    database.execute(
        "INSERT INTO plan_orders (user_id, plan_type, price, qa_quota, "
        "duration_days, order_no, status) VALUES (%s, %s, %s, %s, %s, %s, 'paid')",
        (user["user_id"], plan_type, plan["price"], plan["bonus_quota"],
         plan["duration_days"], order_no))

    log_audit(user["user_id"], user["username"], "PURCHASE_PLAN", "subscription",
              f"购买套餐 {plan['label']}（{plan['price']}元，+{plan['bonus_quota']}次，问答+审查共用）", None)

    logger.info("用户 #%d 购买套餐 %s，获得 %d 次（问答+审查共用），订单号 %s",
                user["user_id"], plan_type, plan["bonus_quota"], order_no)

    return {
        "plan_type": plan_type,
        "plan_label": plan["label"],
        "price": plan["price"],
        "added_quota": plan["bonus_quota"],
        "new_balance": new_balance,
        "plan_expire_at": new_expire,
        "order_no": order_no,
    }


# ==================================================================
# 七、管理员工具
# ==================================================================
def admin_grant_quota(user_id: int, total_quota: int, days: int = 30) -> dict:
    row = _ensure_quota(user_id)
    new_expire = _dt.datetime.now() + _dt.timedelta(days=days)
    database.execute(
        "UPDATE sys_user_quota SET total_balance = %s, plan_expire_at = %s, "
        "plan_type = CASE WHEN %s > 0 THEN 'pro' ELSE plan_type END "
        "WHERE id = %s",
        (total_quota, new_expire, total_quota, row["id"]))

    updated = database.query_one(
        "SELECT * FROM sys_user_quota WHERE id = %s", (row["id"],))
    logger.info("管理员调整用户 #%d 配额: balance=%d, expire=%s",
                user_id, total_quota, new_expire)
    return {
        "user_id": user_id,
        "total_balance": total_quota,
        "plan_expire_at": new_expire,
    }


def admin_list_quotas(page: int = 1, size: int = 20, keyword: str = "") -> dict:
    where = ""
    args: tuple = ()
    if keyword:
        where = "WHERE u.username LIKE %s OR u.real_name LIKE %s"
        args = (f"%{keyword}%", f"%{keyword}%")
    total_row = database.query_one(
        f"SELECT COUNT(*) AS c FROM sys_users u {where}", args)
    rows = database.query_all(
        f"SELECT q.user_id, u.username, u.real_name, u.role, "
        f"q.plan_type, q.daily_free_used, q.daily_contract_used, "
        f"q.daily_free_date, q.daily_contract_date, "
        f"q.total_balance, q.total_purchased, q.plan_expire_at, "
        f"q.last_purchase_at, q.updated_at "
        f"FROM sys_user_quota q JOIN sys_users u ON q.user_id = u.id "
        f"{where} ORDER BY q.updated_at DESC LIMIT %s OFFSET %s",
        args + (size, (page - 1) * size))
    return {"total": total_row["c"], "items": rows}
