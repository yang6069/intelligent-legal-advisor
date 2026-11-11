# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | API 鉴权依赖（FastAPI Depends 注入体系）
==================================================================
模块职责：
    1. JWT 解析依赖：从请求头 Authorization: Bearer <token> 提取并
       校验用户身份（get_current_user）；
    2. SSE 专用鉴权：从 query param ?token= 提取 JWT
       （get_current_user_sse）——EventSource 不能设自定义 header；
    3. 模块级 RBAC 依赖：按 config.ROLE_MODULES 校验当前角色是否可
       访问目标 API 模块（require_module）；
    4. 角色等级依赖：要求 >= 指定角色等级（require_role_level）。

异常协议:
    401 未登录/令牌失效；430 角色权限不足。
==================================================================
"""
from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src import config
from src.auth.security import decode_token

# Bearer 方案自动提取 Authorization 头（auto_error=False: 自定义 401 文案）
_bearer = HTTPBearer(auto_error=False)


def _build_user(payload: dict) -> dict:
    role = payload.get("role", "user")
    return {
        "user_id": payload["user_id"],
        "username": payload["username"],
        "role": role,
        "level": config.ROLE_LEVELS.get(role, 1),
    }


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """
    解析 JWT 并返回当前用户上下文（从 Authorization: Bearer 头提取）。

    返回:
        {"user_id": int, "username": str, "role": str, "level": int}

    异常:
        401 - 未携带令牌 / 令牌过期 / 令牌非法
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="未登录或缺少访问令牌")
    payload = decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return _build_user(payload)


def get_current_user_sse(
    token: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """
    SSE 端点专用鉴权：兼容 query param ?token= 和 header Authorization。

    EventSource 原生不支持自定义 header，只能拼 URL query param。
    这里优先取 query param token，header 作为兜底（兼容 curl 等场景）。
    """
    raw = token or (credentials.credentials if credentials else None)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录或缺少访问令牌")
    payload = decode_token(raw)
    if not payload:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return _build_user(payload)


def require_module(module: str):
    """
    模块级 RBAC 依赖工厂。

    用法:
        @router.get("/...", dependencies=[Depends(require_module("knowledge"))])

    逻辑:
        按角色查 ROLE_MODULES 白名单，未授权模块返回 403。
    """
    def _checker(user: dict = Depends(get_current_user)) -> dict:
        allowed = config.ROLE_MODULES.get(user["role"], set())
        if module not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"当前角色（{config.ROLE_DISPLAY.get(user['role'], user['role'])}）"
                       f"无权访问该功能模块")
        return user
    return _checker


def require_role_level(min_level: int):
    """
    角色等级依赖工厂（等级 >= min_level 方可访问）。

    用法:
        @router.get("/...", dependencies=[Depends(require_role_level(3))])
    """
    def _checker(user: dict = Depends(get_current_user)) -> dict:
        if user["level"] < min_level:
            raise HTTPException(status_code=403, detail="角色权限不足")
        return user
    return _checker


def client_ip(request: Request) -> str:
    """提取客户端真实 IP（Nginx 反代场景优先取 X-Forwarded-For 首段）。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
