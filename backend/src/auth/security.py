# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 认证安全层
==================================================================
模块职责：
    1. PBKDF2-SHA256 密码哈希与校验（12 万次迭代，加盐）；
    2. JWT 令牌签发与校验（HS256，默认 12 小时有效期）；
    3. 登录防爆破（Redis 计数，5 次失败锁定 10 分钟）。

安全设计：
    - 密码绝不存明文，仅存 "算法$迭代$盐$哈希" 格式；
    - 登录失败统一提示"用户名或密码错误"，不泄露具体原因；
    - 恒定时间比较防时序攻击；JWT 校验失败统一 401。
==================================================================
"""
import hashlib
import hmac
import jwt
import logging
import secrets
import time

from fastapi import HTTPException, status

from src import config
from src import database

logger = logging.getLogger("zhifatong.auth")


# ==================================================================
# 一、密码哈希（PBKDF2-SHA256）
# ==================================================================
def hash_password(password: str) -> str:
    """
    对明文密码做 PBKDF2-SHA256 加盐哈希。

    参数:
        password: 明文密码

    返回:
        "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>" 格式字符串
    """
    salt = secrets.token_hex(16)                       # 16 字节随机盐
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        salt.encode("utf-8"),
        config.PBKDF2_ITERATIONS, dklen=32,
    )
    return f"pbkdf2_sha256${config.PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """
    校验明文密码与存储哈希是否匹配（恒定时间比较防时序攻击）。"""
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            return False
        iterations, salt, stored_hex = int(parts[1]), parts[2], parts[3]
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            salt.encode("utf-8"), iterations, dklen=32,
        )
        return hmac.compare_digest(dk.hex(), stored_hex)
    except Exception as e:
        logger.warning("密码校验异常: %s", e)
        return False


def check_password_strength(password: str) -> str | None:
    """
    密码强度校验（注册与修改密码共用，防止密码降级）。

    返回:
        错误提示字符串；通过校验返回 None。
    """
    if not password or len(password) < 6:
        return "密码长度不得少于 6 位"
    if len(password) > 64:
        return "密码长度不得超过 64 位"
    if password.isdigit():
        return "密码不能为纯数字"
    return None


# ==================================================================
# 二、JWT 令牌
# ==================================================================
def create_access_token(user_id: int, username: str, role: str) -> str:
    """
    签发 JWT 访问令牌。

    payload 结构:
        sub=用户ID | username | role | role_level | iat | exp
    """
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "user_id": user_id,
        "username": username,
        "role": role,
        "role_level": config.ROLE_LEVELS.get(role, 1),
        "iat": now,
        "exp": now + config.JWT_EXPIRE_HOURS * 3600,
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """
    解码并校验 JWT。

    异常:
        401 - 令牌无效/过期/伪造
    """
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的身份令牌")


# ==================================================================
# 三、登录防爆破
# ==================================================================
def _fail_key(username: str) -> str:
    """防爆破计数键。"""
    return f"login:fail:{username}"


def is_login_locked(username: str) -> bool:
    """检查账号是否因连续失败被锁定。"""
    count = database.redis_get(_fail_key(username))
    return count is not None and int(count) >= config.LOGIN_MAX_ATTEMPTS


def record_login_failure(username: str) -> int:
    """记录一次登录失败，返回累计失败次数。"""
    return database.redis_incr(_fail_key(username), config.LOGIN_LOCK_SECONDS)


def clear_login_failures(username: str) -> None:
    """登录成功后清空失败计数。"""
    database.redis_del(_fail_key(username))
