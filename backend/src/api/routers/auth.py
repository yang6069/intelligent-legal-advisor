# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | 认证与用户管理路由
==================================================================
路由清单:
    POST /api/auth/login        登录（返回 JWT + 角色菜单树）
    POST /api/auth/register     注册（默认普通用户角色）
    GET  /api/auth/profile      个人信息
    PUT  /api/auth/profile      更新个人信息
    PUT  /api/auth/password     修改密码
    GET  /api/users             用户列表（admin）
    PUT  /api/users/role        调整用户角色（admin）
    PUT  /api/users/status      启用/禁用用户（admin）
    PUT  /api/users/password    重置用户密码（admin）
==================================================================
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src import config
from src.api.deps import client_ip, get_current_user, require_module, require_role_level
from src.auth import service as auth_service
from src.audit.service import log_audit
from fastapi import Request

logger = logging.getLogger("zhifatong.api.auth")

router = APIRouter()


# ==================================================================
# 请求模型（Pydantic 强类型校验）
# ==================================================================
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32, description="用户名")
    password: str = Field(..., min_length=6, max_length=64, description="密码")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=64)
    real_name: str = Field("", max_length=64)


class ProfileUpdateRequest(BaseModel):
    real_name: str = Field(..., max_length=64)
    email: str = Field("", max_length=128)
    phone: str = Field("", max_length=32)


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(..., min_length=6, max_length=64)
    new_password: str = Field(..., min_length=6, max_length=64)


class RoleUpdateRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    role: str = Field(..., pattern="^(admin|legal_admin|user)$")


class StatusUpdateRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    enable: bool


class PasswordResetRequest(BaseModel):
    user_id: int = Field(..., gt=0)
    new_password: str = Field(..., min_length=6, max_length=64)


# ==================================================================
# 登录 / 注册
# ==================================================================
@router.post("/auth/login")
def login(body: LoginRequest, request: Request):
    """
    用户登录。

    流程（服务层完成）:
        1. 防爆破锁定检查（Redis 计数，连续 5 次失败锁定 10 分钟）；
        2. 密码校验（PBKDF2-SHA256）；
        3. 签发 JWT（12 小时有效期）；
        4. 审计留痕（成功/失败/锁定均记录）。

    返回:
        {"user": 用户信息, "token": JWT, "menus": 角色菜单树,
         "role_display": 角色显示名}
        不同角色登录后返回不同的菜单树（前端按权限渲染导航）。
    """
    ip = client_ip(request)
    result = auth_service.login_user(body.username, body.password, ip=ip)
    role = result["user"]["role"]
    result["role_display"] = config.ROLE_DISPLAY.get(role, role)
    return result


@router.post("/auth/register")
def register(body: RegisterRequest, request: Request):
    """用户注册（默认 user 角色，注册即返回可登录令牌）。"""
    ip = client_ip(request)
    return auth_service.register_user(body.username, body.password,
                                      body.real_name, ip=ip)


# ==================================================================
# 个人中心
# ==================================================================
@router.get("/auth/profile")
def get_profile(user: dict = Depends(get_current_user)):
    """查询个人信息（不含密码哈希）。"""
    from src import database
    profile = database.query_one(
        "SELECT id, username, real_name, role, email, phone, status, "
        "last_login_at, created_at FROM sys_users WHERE id = %s",
        (user["user_id"],))
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")
    profile["role_display"] = config.ROLE_DISPLAY.get(profile["role"], profile["role"])
    return profile


@router.put("/auth/profile")
def update_profile(body: ProfileUpdateRequest,
                   user: dict = Depends(get_current_user),
                   request: Request = None):
    """更新个人信息（姓名/邮箱/电话）。"""
    auth_service.update_profile(user["user_id"], body.real_name,
                                body.email, body.phone)
    log_audit(user["user_id"], user["username"], "UPDATE", "profile",
              "更新个人信息", None)
    return {"message": "个人信息已更新"}


@router.put("/auth/password")
def change_password(body: PasswordChangeRequest,
                    user: dict = Depends(get_current_user)):
    """修改本人密码（需验证旧密码，服务层抛 400 HTTPException）。"""
    auth_service.change_password(user["user_id"], body.old_password,
                                 body.new_password)
    log_audit(user["user_id"], user["username"], "PASSWORD_CHANGE", "auth",
              "修改登录密码", None)
    return {"message": "密码修改成功，请重新登录"}


# ==================================================================
# 用户管理（admin 专属）
# ==================================================================
@router.get("/users")
def list_users(page: int = 1, size: int = 20, keyword: str = "",
               user: dict = Depends(require_role_level(3))):
    """分页查询用户列表（支持用户名/姓名关键词）。"""
    return auth_service.list_users(page=page, size=size, keyword=keyword)


@router.put("/users/role")
def update_role(body: RoleUpdateRequest,
                user: dict = Depends(require_role_level(3))):
    """
    调整用户角色（RBAC 权限变更）。

    保护: 不允许管理员降级自己的角色（防止误操作失去管理员权限）。
    """
    if body.user_id == user["user_id"] and body.role != "admin":
        raise HTTPException(status_code=400, detail="不能降低自己的管理员角色")
    auth_service.update_user_role(body.user_id, body.role)
    log_audit(user["user_id"], user["username"], "ROLE_CHANGE", "users",
              f"用户 {body.user_id} 角色调整为 {body.role}", None)
    return {"message": "角色已更新"}


@router.put("/users/status")
def update_status(body: StatusUpdateRequest,
                  user: dict = Depends(require_role_level(3))):
    """启用/禁用用户账号。"""
    if body.user_id == user["user_id"] and not body.enable:
        raise HTTPException(status_code=400, detail="不能禁用自己的账号")
    auth_service.toggle_user_status(body.user_id, body.enable)
    log_audit(user["user_id"], user["username"], "STATUS_CHANGE", "users",
              f"用户 {body.user_id} {'启用' if body.enable else '禁用'}", None)
    return {"message": "状态已更新"}


@router.put("/users/password")
def reset_password(body: PasswordResetRequest,
                   user: dict = Depends(require_role_level(3))):
    """管理员重置用户密码。"""
    auth_service.reset_user_password(body.user_id, body.new_password)
    log_audit(user["user_id"], user["username"], "PASSWORD_RESET", "users",
              f"重置用户 {body.user_id} 密码", None)
    return {"message": "密码已重置"}
