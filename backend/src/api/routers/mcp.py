# -*- coding: utf-8 -*-
"""
==================================================================
智法通 | MCP 工具管理路由（admin 专属）
==================================================================
路由清单:
    GET  /api/mcp/tools                工具发现（通配符过滤）
    POST /api/mcp/tools/register       工具注册（热插拔）
    PUT  /api/mcp/tools/status         工具启用/停用
    POST /api/mcp/tools/call           工具调用（统一入口）
    GET  /api/mcp/health               服务器健康检查
    GET  /api/mcp/stats                工具目录统计

协议语义（需求文档 4.4.2）:
    注册/发现/调用/健康检查，全部异步接口。
==================================================================
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.deps import require_role_level
from src.audit.service import log_audit
from src.mcp.manager import mcp_manager

logger = logging.getLogger("zhifatong.api.mcp")

# MCP 管理为 admin 专属（三级 RBAC 最高级）
mcp_guard = require_role_level(3)
router = APIRouter()


class ToolRegisterRequest(BaseModel):
    """工具注册请求（热插拔接入）。"""
    tool_name: str = Field(..., min_length=2, max_length=128,
                           pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    server_name: str = Field(..., min_length=2, max_length=128)
    description: str = Field("", max_length=512)
    # 字段名避开 BaseModel.schema 保留属性（Pydantic v2 警告）
    input_schema: dict = Field(default_factory=dict,
                               description="输入参数 JSON Schema")


class ToolCallRequest(BaseModel):
    """工具调用请求。"""
    tool_name: str = Field(..., min_length=2, max_length=128)
    arguments: dict = Field(default_factory=dict)


class ToolStatusRequest(BaseModel):
    """工具启用/停用请求。"""
    tool_name: str = Field(..., min_length=2, max_length=128)
    active: bool


@router.get("/mcp/tools")
def discover_tools(pattern: str = "*", user: dict = Depends(mcp_guard)):
    """
    工具发现（支持通配符，如 search_* / calculate_*）。

    返回: [{name, server, description, schema, status}, ...]
    """
    return {"tools": mcp_manager.discover_tools(pattern)}


@router.post("/mcp/tools/register")
def register_tool(body: ToolRegisterRequest, user: dict = Depends(mcp_guard)):
    """
    注册 MCP 工具（热插拔）。

    说明: 当前实现为进程内 MCP 兼容层；schema 会持久化到 mcp_tools
    表，重启后目录记录自动恢复。
    """
    result = mcp_manager.register_tool(
        body.tool_name, body.server_name, body.description, body.input_schema)
    if result.get("status") != "ok":
        raise HTTPException(status_code=400, detail=result.get("message"))
    log_audit(user["user_id"], user["username"], "REGISTER", "mcp",
              f"注册工具 {body.tool_name} (server={body.server_name})", None)
    return result


@router.put("/mcp/tools/status")
def set_tool_status(body: ToolStatusRequest, user: dict = Depends(mcp_guard)):
    """启用/停用工具（下线不删除目录记录，可随时恢复）。"""
    result = mcp_manager.set_tool_status(body.tool_name, body.active)
    if result.get("status") != "ok":
        raise HTTPException(status_code=404, detail=result.get("message"))
    log_audit(user["user_id"], user["username"], "TOOL_STATUS", "mcp",
              f"工具 {body.tool_name} -> {'启用' if body.active else '停用'}", None)
    return result


@router.post("/mcp/tools/call")
async def call_tool(body: ToolCallRequest, user: dict = Depends(mcp_guard)):
    """
    调用 MCP 工具（需求文档 4.4.2 call_tool 协议）。

    流程: 注册校验 -> JSON Schema 必填参数校验 -> 执行 -> 统计落库。
    返回: {"status": "ok", "result": ..., "latency_ms": ...}
    """
    result = await mcp_manager.call_tool(body.tool_name, body.arguments)
    if result.get("status") != "ok":
        # 工具存在性错误 -> 404；执行/参数错误 -> 400
        code = 404 if "未注册" in result.get("message", "") else 400
        raise HTTPException(status_code=code, detail=result.get("message"))
    log_audit(user["user_id"], user["username"], "TOOL_CALL", "mcp",
              f"调用工具 {body.tool_name}", None)
    return result


@router.get("/mcp/health")
async def health_check(user: dict = Depends(mcp_guard)):
    """所有 MCP 工具服务器健康检查。"""
    return {"servers": await mcp_manager.health_check()}


@router.get("/mcp/stats")
def mcp_stats(user: dict = Depends(mcp_guard)):
    """工具目录统计概览（服务器数/工具数/活跃率/累计调用）。"""
    return mcp_manager.stats()
