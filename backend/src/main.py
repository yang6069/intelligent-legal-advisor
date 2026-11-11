# -*- coding: utf-8 -*-
"""
==================================================================
智法通（ZhiFaTong）| FastAPI 主应用入口
==================================================================
模块职责：
    1. 组装全部 API 路由（8 个业务模块，统一挂载 /api 前缀）；
    2. 应用生命周期管理（startup：账号种子/知识种子/索引构建/MCP 初始化）；
    3. 全局异常兜底与 CORS 中间件；
    4. 健康检查与系统信息接口（容器探针 / 运维监控）。

启动方式:
    本地: uvicorn src.main:app --host 0.0.0.0 --port 28000
    容器: uvicorn src.main:app --host 0.0.0.0 --port 28000（APP_ENV=docker）
==================================================================
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src import config, database
from src.api.routers import analytics, audit, auth, contract, documents, knowledge, mcp, meta, qa, subscription

# ==================================================================
# 日志初始化（统一格式，容器 stdout 采集）
# ==================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("zhifatong.main")


# ==================================================================
# 应用生命周期（startup / shutdown）
# ==================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。

    startup:
        1. 数据库连通性探测（失败不阻断启动：容器启动顺序解耦）；
        2. 初始化内置账号（幂等）；
        3. 导入法律知识种子数据（幂等）；
        4. 重建三路索引（向量+BM25+图谱，含向量化耗时操作放线程池）；
        5. 初始化 MCP 工具目录（8 个内置法律工具）。
    shutdown:
        释放 Neo4j 驱动等长连接资源。
    """
    logger.info("智法通启动中 [env=%s] [version=%s]", config.APP_ENV,
                config.APP_VERSION)

    # ---- 1. 数据库就绪检查 ----
    if database.ping():
        # ---- 2. 内置账号 ----
        try:
            from src.auth.service import seed_accounts
            seed_accounts()
        except Exception as e:
            logger.warning("内置账号初始化延迟: %s", e)

        # ---- 3. 种子知识 ----
        from src.rag.seed_data import seed_knowledge
        seed_result = seed_knowledge()
        logger.info("知识种子: %s", seed_result)

        # ---- 3.5 历史数据乱码修复（幂等：latin-1 误码的合同标题/类型）----
        try:
            from src.analytics.service import repair_mojibake_rows
            repair_mojibake_rows()
        except Exception as e:
            logger.warning("历史乱码修复跳过: %s", e)

        # ---- 4. 索引保障（含 BM25 重启恢复 / 首次全量构建）----
        import asyncio
        from src.rag.knowledge_service import knowledge_service
        stats = await asyncio.to_thread(knowledge_service.ensure_bootstrapped)
        if seed_result.get("imported", 0) > 0:
            stats = await asyncio.to_thread(knowledge_service.rebuild_all)
            logger.info("三路索引构建完成: %s", stats)
    else:
        logger.warning("数据库未就绪（将以无账号/无种子模式启动，"
                       "依赖首次健康检查重试）")

    # ---- 5. MCP 工具目录 ----
    try:
        from src.mcp.manager import mcp_manager
        mcp_manager.ensure_ready()
        logger.info("MCP 工具目录: %s", mcp_manager.stats())
    except Exception as e:
        logger.warning("MCP 初始化延迟: %s", e)

    # ---- 6. 知识库索引跨进程同步监听 ----
    # 多 worker 部署下，任一进程增删文档后通过 Redis 广播，
    # 其余进程收到事件即重建本进程 BM25 内存索引（保证检索一致）
    try:
        from src.rag.index_sync import start_kb_sync_listener
        start_kb_sync_listener()
    except Exception as e:
        logger.warning("索引同步监听启动失败（降级运行）: %s", e)

    logger.info("智法通启动完成 -> http://0.0.0.0:%d", config.BACKEND_PORT)
    yield

    # ---- shutdown: 释放图谱驱动与同步线程 ----
    try:
        from src.rag import graph_store
        graph_store.close()
    except Exception:
        pass
    try:
        from src.rag.index_sync import stop_kb_sync_listener
        stop_kb_sync_listener()
    except Exception:
        pass
    logger.info("智法通已停止")


# ==================================================================
# 应用实例与中间件
# ==================================================================
app = FastAPI(
    title="智法通 · 企业级智能法律顾问平台",
    description=(
        "GraphRAG 三路混合检索 / 多 Agent 对抗式合同审查 / "
        "MCP 协议工具生态 / Self-RAG 自评估"
    ),
    version=config.APP_VERSION,
    lifespan=lifespan,
)

# CORS：前端开发服务器（Vite 5173）与生产域名跨域放行
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],               # 生产环境应收敛为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 全局异常兜底：未捕获异常返回统一 500 结构（不泄漏堆栈细节）
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("未捕获异常 %s %s: %s", request.method, request.url.path,
                 exc, exc_info=True)
    return JSONResponse(status_code=500,
                        content={"detail": "服务器内部错误，请稍后重试"})


# ==================================================================
# 路由挂载（统一 /api 前缀）
# ==================================================================
app.include_router(auth.router, prefix="/api", tags=["认证与用户"])
app.include_router(qa.router, prefix="/api", tags=["智能问答"])
app.include_router(contract.router, prefix="/api", tags=["合同审查"])
app.include_router(documents.router, prefix="/api", tags=["文书起草"])
app.include_router(subscription.router, prefix="/api", tags=["用户套餐"])
app.include_router(knowledge.router, prefix="/api", tags=["知识库管理"])
app.include_router(analytics.router, prefix="/api", tags=["数据分析"])
app.include_router(mcp.router, prefix="/api", tags=["MCP 工具"])
app.include_router(audit.router, prefix="/api", tags=["审计日志"])
app.include_router(meta.router, prefix="/api", tags=["前端字典"])


# ==================================================================
# 健康检查与系统信息
# ==================================================================
@app.get("/health", tags=["运维"])
async def health():
    """
    健康检查（Docker healthcheck / Nginx upstream 探测）。

    检查项: MySQL / Redis / Ollama / Rerank / Neo4j 可用性。
    """
    checks = {"mysql": database.ping()}
    try:
        database.redis_get("health:probe")
        checks["redis"] = True
    except Exception:
        checks["redis"] = False

    import httpx
    async with httpx.AsyncClient(timeout=3) as client:
        for name, url in [("ollama", f"{config.OLLAMA_BASE_URL}/api/tags"),
                          ("rerank", f"{config.RERANK_BASE_URL}/health")]:
            try:
                resp = await client.get(url)
                checks[name] = resp.status_code < 500
            except Exception:
                checks[name] = False
    try:
        from src.rag import graph_store
        checks["neo4j"] = graph_store.ensure_graph()
    except Exception:
        checks["neo4j"] = False

    return {"status": "ok", "app": config.APP_NAME,
            "version": config.APP_VERSION, "env": config.APP_ENV,
            "services": checks}


@app.get("/", tags=["运维"])
async def root():
    """根路径：API 导航信息。"""
    return {
        "app": f"{config.APP_NAME} v{config.APP_VERSION}",
        "docs": "/docs",
        "health": "/health",
        "api_prefix": "/api",
    }


if __name__ == "__main__":
    # 本地直启入口（开发调试；生产使用 uvicorn 命令）
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=config.BACKEND_PORT,
                reload=False)
