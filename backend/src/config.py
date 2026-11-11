# -*- coding: utf-8 -*-
"""
==================================================================
智法通（ZhiFaTong）—— 企业级智能法律顾问平台 | 全局配置中心
==================================================================
模块职责：
    1. 集中管理所有可外部化配置（通过 .env / 环境变量注入）；
    2. 角色权限体系定义（三级 RBAC）与角色匹配的菜单树；
    3. 向各业务模块导出常量（分块参数、检索参数、风险权重等）。

设计原则：
    - 配置外部化：模型名称、服务地址、粗排/精排数量均通过 .env 调整，
      支持本地开发 / Docker 容器两种环境无缝切换（APP_ENV 区分）；
    - 安全默认值：所有配置均给出安全缺省值，缺失时系统仍可启动。
==================================================================
"""
import os
import sys
from pathlib import Path

# ------------------------------------------------------------------
# 0. 项目路径与本地依赖（vendor 目录，含 neo4j 驱动 / rank_bm25）
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent          # backend/
VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.exists() and str(VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_DIR))                    # 优先级低于系统包

DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"                          # 合同上传目录
CHROMA_DIR = DATA_DIR / "chroma"                           # ChromaDB 持久化目录
for _d in (DATA_DIR, UPLOAD_DIR, CHROMA_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(key: str, default: str) -> str:
    """读取环境变量（字符串），缺失时返回默认值。"""
    return os.environ.get(key, default)


def _env_int(key: str, default: int) -> int:
    """读取环境变量（整数），带异常保护。"""
    try:
        return int(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------
# 1. 应用环境
# ------------------------------------------------------------------
APP_ENV = _env("APP_ENV", "local")            # local=本地开发 | docker=容器部署
APP_NAME = "智法通"
APP_VERSION = "6.0.0"
BACKEND_PORT = _env_int("BACKEND_PORT", 28000)  # 后端监听端口

# ------------------------------------------------------------------
# 2. MySQL 数据库连接（本地: 13306 宿主机映射 / 容器: 3306 容器网络）
# ------------------------------------------------------------------
MYSQL_HOST = _env("MYSQL_HOST", "127.0.0.1" if APP_ENV == "local" else "zhifatong-mysql")
MYSQL_PORT = _env_int("MYSQL_PORT", 13306 if APP_ENV == "local" else 3306)
MYSQL_USER = _env("MYSQL_USER", "root")
MYSQL_PASSWORD = _env("MYSQL_PASSWORD", "zhifatong_root_2026")
MYSQL_DATABASE = _env("MYSQL_DATABASE", "zhifatong")
MYSQL_POOL_SIZE = _env_int("MYSQL_POOL_SIZE", 8)          # 连接池大小
MYSQL_POOL_RECYCLE = 3600                                  # 连接回收周期（秒），低于 MySQL wait_timeout

# ------------------------------------------------------------------
# 3. Redis 连接（缓存 / 会话 / 登录防爆破计数）
# ------------------------------------------------------------------
REDIS_HOST = _env("REDIS_HOST", "127.0.0.1" if APP_ENV == "local" else "zhifatong-redis")
REDIS_PORT = _env_int("REDIS_PORT", 26379 if APP_ENV == "local" else 6379)
REDIS_PASSWORD = _env("REDIS_PASSWORD", "")               # 本地开发无密码
REDIS_DB = _env_int("REDIS_DB", 0)

# ------------------------------------------------------------------
# 4. Neo4j 知识图谱（GraphRAG 核心组件，不可用时自动降级）
# ------------------------------------------------------------------
NEO4J_URI = _env("NEO4J_URI", "bolt://127.0.0.1:7687" if APP_ENV == "local" else "bolt://zhifatong-neo4j:7687")
NEO4J_USER = _env("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _env("NEO4J_PASSWORD", "Zhifa@Neo4j2025")
NEO4J_ENABLED = _env("NEO4J_ENABLED", "true").lower() == "true"

# ------------------------------------------------------------------
# 5. AI 服务（Ollama LLM + Embedding，my-rerank 精排）
# ------------------------------------------------------------------
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
LLM_MODEL = _env("LLM_MODEL", "qwen2.5:3b")                            # 生成模型（已升级 3B，质量更优）
EMBEDDING_MODEL = _env("EMBEDDING_MODEL", "quentinz/bge-large-zh-v1.5:latest")  # 1024 维中文嵌入
EMBEDDING_DIM = _env_int("EMBEDDING_DIM", 1024)
RERANK_BASE_URL = _env("RERANK_BASE_URL", "http://127.0.0.1:9999")     # Cross-Encoder 精排服务

# LLM 推理参数
LLM_TEMPERATURE = 0.1          # 法律场景低温度，保证严谨稳定
LLM_MAX_TOKENS = 2048          # 单次生成上限
LLM_TIMEOUT = 120              # 生成超时（秒）
LLM_STREAM_TIMEOUT = 180       # 流式生成超时（秒）

# ------------------------------------------------------------------
# 6. 两阶段检索架构参数（可调优）
# ------------------------------------------------------------------
RECALL_TOP_K = _env_int("RECALL_TOP_K", 20)       # 第一阶段粗排候选数（ANN+BM25 各自召回）
RERANK_TOP_N = _env_int("RERANK_TOP_N", 5)        # 第二阶段精排输出条数
RRF_K = _env_int("RRF_K", 60)                     # RRF 融合平滑参数
SIMILARITY_THRESHOLD = 0.30                       # 向量召回相似度下限（余弦）
GRAPH_SEARCH_ENABLED = True                       # 是否启用图谱关联检索（第三路）

# ------------------------------------------------------------------
# 7. 文档分块参数（法律感知分块）
# ------------------------------------------------------------------
CHUNK_SIZE = 512                                 # 目标分块字符数
CHUNK_OVERLAP = 64                               # 重叠字符数，保证上下文连贯

# ------------------------------------------------------------------
# 8. JWT 认证参数
# ------------------------------------------------------------------
JWT_SECRET = _env("JWT_SECRET", "zhifatong-jwt-secret-2026-do-not-leak")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = _env_int("JWT_EXPIRE_HOURS", 12)
PBKDF2_ITERATIONS = 120000                       # 密码哈希迭代次数

# 登录防爆破：5 次失败锁定 10 分钟（Redis 计数）
LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCK_SECONDS = 600

# ------------------------------------------------------------------
# 9. 角色权限体系（三级 RBAC）
# ------------------------------------------------------------------
# 角色等级：数值越大权限越高
ROLE_LEVELS = {
    "user": 1,          # 普通用户：智能问答 + 合同审查（本人）
    "legal_admin": 2,   # 法务负责人：+ 仪表盘 / 知识库 / 数据分析
    "admin": 3,         # 超级管理员：+ 用户管理 / 审计日志 / MCP 工具
}

ROLE_DISPLAY = {
    "admin": "超级管理员",
    "legal_admin": "法务负责人",
    "user": "普通用户",
}

# 各角色允许访问的 API 模块（用于后端接口级鉴权）
# 多用户数据隔离模型：
#   - admin       : 全平台视角（可见/操作全部角色的合同审查记录，
#                   仪表盘与分析展示全平台汇总），并拥有系统管理功能；
#   - legal_admin : 法务负责人，业务数据仅本人 + 知识库管理；
#   - user        : 普通用户，业务数据仅本人；除知识库管理外，
#                   功能权限与法务完全一致（仪表盘/数据分析可见，
#                   仅展示本人产生的数据）。
ROLE_MODULES = {
    "user": {"qa", "contract", "documents", "subscription", "analytics", "profile"},
    "legal_admin": {"qa", "contract", "documents", "subscription", "knowledge", "analytics", "profile"},
    "admin": {"qa", "contract", "documents", "subscription", "knowledge", "analytics", "mcp", "users", "audit", "profile"},
}

# ------------------------------------------------------------------
# 10. 角色匹配的前端菜单树
#     icon: 前端 Element Plus 图标名；path: 前端路由
# ------------------------------------------------------------------
MENU_DEFINITIONS = [
    # 仪表盘/数据分析对全部角色开放（管理员=全平台汇总，法务/用户=本人数据）
    {"code": "dashboard", "title": "仪表盘", "icon": "Odometer", "path": "/dashboard",
     "min_level": 1, "module": "analytics"},
    {"code": "chat", "title": "智能问答", "icon": "ChatDotRound", "path": "/chat",
     "min_level": 1, "module": "qa"},
    {"code": "contract", "title": "合同审查", "icon": "DocumentChecked", "path": "/contract",
     "min_level": 1, "module": "contract"},
    {"code": "drafting", "title": "文书起草", "icon": "EditPen", "path": "/drafting",
     "min_level": 1, "module": "documents"},
    # 我的套餐：仅普通用户可见（管理员/法务问答无限制，无需购买入口）
    {"code": "subscription", "title": "我的套餐", "icon": "Wallet", "path": "/subscription",
     "min_level": 1, "module": "subscription", "only_roles": ("user",)},
    # 知识库管理：仅法务及以上（普通用户禁止访问）
    {"code": "knowledge", "title": "知识库管理", "icon": "Collection", "path": "/knowledge",
     "min_level": 2, "module": "knowledge"},
    {"code": "analytics", "title": "数据分析", "icon": "TrendCharts", "path": "/analytics",
     "min_level": 1, "module": "analytics"},
    {"code": "mcp", "title": "MCP 工具", "icon": "SetUp", "path": "/mcp",
     "min_level": 3, "module": "mcp"},
    {"code": "users", "title": "用户管理", "icon": "UserFilled", "path": "/users",
     "min_level": 3, "module": "users"},
    {"code": "audit", "title": "审计日志", "icon": "Memo", "path": "/audit",
     "min_level": 3, "module": "audit"},
    {"code": "profile", "title": "个人中心", "icon": "Avatar", "path": "/profile",
     "min_level": 1, "module": "profile"},
]


def build_menu_tree(role: str) -> list[dict]:
    """
    根据角色构建前端菜单树。

    参数:
        role: 角色标识（admin / legal_admin / user）

    返回:
        菜单列表（仅包含该角色有权访问的菜单项）

    逻辑:
        1. 按角色等级过滤 min_level（普通用户仅见核心业务菜单）；
        2. 再过滤 only_roles（限定菜单仅对指定角色开放，如"我的套餐"
           仅普通 user 可见）。
    """
    level = ROLE_LEVELS.get(role, 1)
    return [
        m for m in MENU_DEFINITIONS
        if level >= m["min_level"]
        and ("only_roles" not in m or role in m["only_roles"])
    ]


# ------------------------------------------------------------------
# 11. 合同审查风险权重（多 Agent 评分模型）
# ------------------------------------------------------------------
SEVERITY_WEIGHT = {"高": 10, "中": 5, "低": 2}    # 风险严重度权重
COMPLETENESS_WEIGHT = 30                          # 完整性缺失占比上限
UNRESOLVED_WEIGHT = 8                             # 每项未共识问题加权
MISSED_WEIGHT = 12                                # 复核发现主审遗漏加权

# ------------------------------------------------------------------
# 11.1 套餐配额相关常量
# ------------------------------------------------------------------
FREE_DAILY_QA_LIMIT = 20                    # 普通用户每日免费问答次数（2026-09-24 从 50 下调到 20）
FREE_DAILY_CONTRACT_LIMIT = 10              # 普通用户每日免费合同审查次数（同日新增）
# admin / legal_admin 不受限（subscription.service 中跳过检查）

# ------------------------------------------------------------------
# 11.2 文书起草相关
# ------------------------------------------------------------------
DOC_MAX_TOKENS = 2048                       # AI 起草单次生成上限

# ------------------------------------------------------------------
# 12. 业务字典中心（前端选项/标签唯一数据源，经 /api/meta/dictionaries 下发）
#     设计原则：业务枚举只在此定义一次，前端严禁硬编码业务数据
# ------------------------------------------------------------------
# 知识库文档类型（value 与 kb_documents.doc_type 一致；tag 为前端标签色）
DOC_TYPES = [
    {"value": "law", "label": "法律法规", "tag": "primary"},
    {"value": "case", "label": "裁判案例", "tag": "success"},
    {"value": "interpretation", "label": "司法解释", "tag": "warning"},
    {"value": "template", "label": "合同模板", "tag": "info"},
]

# 用户角色选项（与 ROLE_LEVELS/ROLE_DISPLAY 一致；tag 为前端徽标色）
ROLE_OPTIONS = [
    {"value": "user", "label": "普通用户", "tag": "info"},
    {"value": "legal_admin", "label": "法务负责人", "tag": "warning"},
    {"value": "admin", "label": "系统管理员", "tag": "danger"},
]

# 审计日志模块枚举（与 audit service 记录的 module 值一致）
AUDIT_MODULES = [
    {"value": "auth", "label": "认证登录"},
    {"value": "qa", "label": "智能问答"},
    {"value": "contract", "label": "合同审查"},
    {"value": "knowledge", "label": "知识库"},
    {"value": "mcp", "label": "MCP 工具"},
    {"value": "user", "label": "用户管理"},
]

# 审计行为枚举（下拉过滤项）
AUDIT_ACTIONS = [
    "LOGIN", "LOGOUT", "QUERY", "QUERY_STREAM", "UPLOAD", "REVIEW_TEXT",
    "REGISTER", "TOOL_CALL", "TOOL_STATUS", "CREATE", "UPDATE", "DELETE", "EXPORT",
]

# 合同审查工作流阶段（进度动画文案，与工作流节点对应）
CONTRACT_STAGES = ["条款拆分", "三视角推理", "主审风险识别",
                   "复核遗漏检查", "反方辩论", "仲裁评分", "生成报告"]

# 数据分析时间范围选项（天）
ANALYTICS_DAYS = [7, 30, 90]

# 用户聚类特征中文标签（key 与聚类特征字段一致）
CLUSTER_FEATURE_LABELS = {
    "query_count": "咨询次数", "contract_count": "合同审查",
    "avg_confidence": "平均置信度", "active_days": "活跃天数",
    "role_level": "角色等级",
}

# 智能问答快捷提问兜底（知识库为空/无历史咨询时使用；
# 有数据时优先返回近期真实高频提问）
DEFAULT_QUICK_QUESTIONS = [
    "劳动合同违约金的法律上限是多少？",
    "借款合同没有约定利息，可以主张利息吗？",
    "公司注册资本认缴期限最长时间是多久？",
]

# 内置演示账号（登录页提示文案数据源；与 seed_accounts 保持一致）
DEMO_ACCOUNTS = [
    {"username": "admin", "password": "admin123", "role": "管理员"},
    {"username": "lawyer", "password": "lawyer123", "role": "法务负责人"},
    {"username": "user", "password": "user123456", "role": "普通用户"},
]
