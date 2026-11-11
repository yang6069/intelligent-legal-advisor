# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 认证业务服务
==================================================================
模块职责：
    1. 用户登录（防爆破 + JWT 签发 + 角色菜单树）；
    2. 用户注册（密码强度校验，默认 user 角色）；
    3. 用户管理（列表/启用禁用/角色调整/重置密码——管理员功能）；
    4. 个人资料修改与密码修改（改密后主动失效缓存）；
    5. 种子账号幂等播种（admin / lawyer / user）。
==================================================================
"""
import logging

from fastapi import HTTPException, status

from src import config
from src import database
from src.auth import security
from src.audit.service import log_audit

logger = logging.getLogger("zhifatong.auth.service")


# ==================================================================
# 一、登录
# ==================================================================
def login_user(username: str, password: str, ip: str = "unknown") -> dict:
    """
    用户登录。

    返回:
        {"user": 用户信息, "token": JWT, "menus": 角色菜单树}

    安全规则:
        - 锁定账号直接拒绝（不校验密码，防止继续爆破）；
        - 用户不存在与密码错误统一提示，不泄露账号存在性；
        - 连续 5 次失败锁定 10 分钟。
    """
    # 1) 防爆破锁定检查
    if security.is_login_locked(username):
        log_audit(None, username, "LOGIN_LOCKED", "auth", f"账号连续失败被临时锁定: {username}", ip)
        raise HTTPException(status.HTTP_423_LOCKED, "登录失败次数过多，账号已临时锁定，请 10 分钟后重试")

    # 2) 查询用户
    user = database.query_one(
        "SELECT id, username, password_hash, real_name, role, email, phone, status, created_at, last_login_at "
        "FROM sys_users WHERE username = %s",
        (username,),
    )
    if user is None or not security.verify_password(password, user["password_hash"]):
        if user is not None:
            fails = security.record_login_failure(username)
            log_audit(user["id"], username, "LOGIN_FAIL", "auth", f"登录失败（第 {fails} 次）", ip)
        else:
            log_audit(None, username, "LOGIN_FAIL", "auth", "登录失败：用户不存在", ip)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")

    # 3) 账号状态检查
    if not user["status"]:
        log_audit(user["id"], username, "LOGIN_BLOCKED", "auth", "禁用账号尝试登录", ip)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被禁用，请联系管理员")

    # 4) 登录成功：清空失败计数、更新登录时间、签发令牌
    security.clear_login_failures(username)
    database.execute("UPDATE sys_users SET last_login_at = NOW() WHERE id = %s", (user["id"],))
    user.pop("password_hash", None)                       # 绝不下发密码哈希
    token = security.create_access_token(user["id"], user["username"], user["role"])
    menus = config.build_menu_tree(user["role"])
    log_audit(user["id"], username, "LOGIN", "auth", "登录成功", ip)
    return {"user": user, "token": token, "menus": menus}


# ==================================================================
# 二、注册
# ==================================================================
def register_user(username: str, password: str, real_name: str, ip: str = "unknown") -> dict:
    """
    用户注册（默认 user 角色，注册即登录）。
    """
    if not username or not (3 <= len(username) <= 32):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "用户名长度需在 3~32 字符之间")
    err = security.check_password_strength(password)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    if database.query_one("SELECT id FROM sys_users WHERE username = %s", (username,)):
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用")

    password_hash = security.hash_password(password)
    user_id = database.insert_return_id(
        "INSERT INTO sys_users (username, password_hash, real_name, role) VALUES (%s, %s, %s, 'user')",
        (username, password_hash, real_name or username),
    )
    user = database.query_one(
        "SELECT id, username, real_name, role, status, created_at FROM sys_users WHERE id = %s",
        (user_id,),
    )
    token = security.create_access_token(user["id"], user["username"], user["role"])
    log_audit(user["id"], username, "REGISTER", "auth", "新用户注册", ip)
    return {"user": user, "token": token, "menus": config.build_menu_tree("user")}


# ==================================================================
# 三、用户管理（管理员）
# ==================================================================
def list_users(page: int = 1, size: int = 20, keyword: str = "") -> dict:
    """分页查询用户列表，支持按用户名/姓名关键字过滤。"""
    where, args = "", ()
    if keyword:
        where = "WHERE username LIKE %s OR real_name LIKE %s"
        args = (f"%{keyword}%", f"%{keyword}%")
    total = database.query_one(f"SELECT COUNT(*) AS c FROM sys_users {where}", args)["c"]
    rows = database.query_all(
        f"SELECT id, username, real_name, role, email, phone, status, last_login_at, created_at "
        f"FROM sys_users {where} ORDER BY id DESC LIMIT %s OFFSET %s",
        args + (size, (page - 1) * size),
    )
    return {"total": total, "items": rows}


def update_user_role(user_id: int, role: str) -> None:
    """调整用户角色（仅允许三种合法角色）。"""
    if role not in config.ROLE_LEVELS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法角色")
    database.execute("UPDATE sys_users SET role = %s WHERE id = %s", (role, user_id))


def toggle_user_status(user_id: int, enable: bool) -> None:
    """启用/禁用用户。"""
    database.execute("UPDATE sys_users SET status = %s WHERE id = %s", (1 if enable else 0, user_id))


def reset_user_password(user_id: int, new_password: str) -> None:
    """管理员重置用户密码（同样执行强度校验）。"""
    err = security.check_password_strength(new_password)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    database.execute(
        "UPDATE sys_users SET password_hash = %s WHERE id = %s",
        (security.hash_password(new_password), user_id),
    )


# ==================================================================
# 四、个人资料与密码修改
# ==================================================================
def update_profile(user_id: int, real_name: str, email: str, phone: str) -> None:
    """修改个人资料；同时失效用户信息缓存。"""
    database.execute(
        "UPDATE sys_users SET real_name=%s, email=%s, phone=%s WHERE id=%s",
        (real_name, email, phone, user_id),
    )
    database.redis_del(f"user:info:{user_id}")


def change_password(user_id: int, old_password: str, new_password: str) -> None:
    """
    修改密码（需验证旧密码）。

    安全规则:
        - 旧密码校验失败返回 400；
        - 新密码执行强度校验（防降级）；
        - 成功后清空该用户的 Redis 缓存，确保权限/信息及时生效。
    """
    row = database.query_one("SELECT password_hash FROM sys_users WHERE id = %s", (user_id,))
    if row is None or not security.verify_password(old_password, row["password_hash"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "原密码不正确")
    err = security.check_password_strength(new_password)
    if err:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, err)
    database.execute(
        "UPDATE sys_users SET password_hash=%s WHERE id=%s",
        (security.hash_password(new_password), user_id),
    )
    database.redis_del(f"user:info:{user_id}")
    log_audit(user_id, None, "CHANGE_PWD", "auth", "用户修改密码", None)


# ==================================================================
# 五、种子账号播种（服务启动时调用，幂等）
# ==================================================================
SEED_ACCOUNTS = [
    # (username, password, real_name, role)
    ("admin", "admin123", "系统管理员", "admin"),
    ("lawyer", "lawyer123", "李法务", "legal_admin"),
    ("user", "user123456", "王小姐", "user"),
]


def seed_accounts() -> None:
    """幂等播种三角色演示账号（已存在则跳过）。"""
    for username, password, real_name, role in SEED_ACCOUNTS:
        if database.query_one("SELECT id FROM sys_users WHERE username = %s", (username,)):
            continue
        database.execute(
            "INSERT INTO sys_users (username, password_hash, real_name, role) VALUES (%s, %s, %s, %s)",
            (username, security.hash_password(password), real_name, role),
        )
        logger.info("播种账号: %s (%s)", username, role)
