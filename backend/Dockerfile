# ==================================================================
# 智法通后端镜像 | python:3.10-slim（本地已缓存镜像）
# ------------------------------------------------------------------
# 构建策略：
#   1. 依赖层单独缓存（requirements.txt 变更才重装）；
#   2. 源码层 + vendor 目录（BM25/Neo4j 本地包）；
#   3. 运行时数据卷 /app/data（Chroma 向量库 + 上传合同）。
# ==================================================================

FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/python:3.10-slim

# 环境标识：容器内运行（config.py 读取）
ENV APP_ENV=docker \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# ---- 依赖层（利用 Docker 缓存加速重复构建）----
COPY requirements.txt .
# 清华 PyPI 镜像源（国内网络加速）；chromadb 需要 gcc 编译原生扩展
RUN apt-get update && apt-get install -y --no-install-recommends gcc curl \
    && pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt \
    && apt-get purge -y gcc && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# ---- 源码层 ----
COPY src ./src
COPY vendor ./vendor

# 数据目录（挂载卷持久化 Chroma 索引与上传合同）
RUN mkdir -p /app/data/chroma /app/data/uploads

EXPOSE 8000

# 健康检查端点 /health（见 main.py）
HEALTHCHECK --interval=20s --timeout=10s --retries=10 --start-period=60s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# 启动命令：单 worker（SSE/asyncio.Queue 进度状态必须单进程共享；
#   LLM 调用是 async/await 不阻塞 event loop，单 worker 并发足够）
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
