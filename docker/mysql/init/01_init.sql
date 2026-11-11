-- ============================================================
-- 智法通（ZhiFaTong）—— 数据库初始化脚本
-- 执行时机：MySQL 容器首次启动时自动执行（docker-entrypoint-initdb.d）
-- 字符集：utf8mb4（完整支持中文与表情符号）
-- ============================================================

CREATE DATABASE IF NOT EXISTS zhifatong
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE zhifatong;

-- ------------------------------------------------------------
-- 1. 系统用户表（sys_users）
--    角色：admin=超级管理员 | legal_admin=法务负责人 | user=普通用户
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_users (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '用户ID',
    username      VARCHAR(32)  NOT NULL UNIQUE      COMMENT '登录用户名',
    password_hash VARCHAR(255) NOT NULL             COMMENT 'PBKDF2-SHA256 密码哈希',
    real_name     VARCHAR(64)  DEFAULT ''           COMMENT '真实姓名',
    role          VARCHAR(32)  NOT NULL DEFAULT 'user' COMMENT '角色标识',
    email         VARCHAR(128) DEFAULT ''           COMMENT '电子邮箱',
    phone         VARCHAR(32)  DEFAULT ''           COMMENT '联系电话',
    status        TINYINT      NOT NULL DEFAULT 1   COMMENT '状态：1=启用 0=禁用',
    last_login_at DATETIME     NULL                 COMMENT '最近登录时间',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_role (role)
) ENGINE=InnoDB COMMENT='系统用户表';

-- ------------------------------------------------------------
-- 2. 问答会话表（qa_sessions）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qa_sessions (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '会话ID',
    user_id    BIGINT       NOT NULL             COMMENT '所属用户',
    title      VARCHAR(128) NOT NULL DEFAULT '新会话' COMMENT '会话标题（取首问截断）',
    msg_count  INT          NOT NULL DEFAULT 0   COMMENT '消息数量',
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id)
) ENGINE=InnoDB COMMENT='法律问答会话表';

-- ------------------------------------------------------------
-- 3. 问答消息表（qa_messages）
--    保存完整 RAG 链路产物：回答正文 / 法条引用 / 置信度 / 检索子查询
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS qa_messages (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '消息ID',
    session_id   BIGINT       NOT NULL              COMMENT '所属会话',
    user_id      BIGINT       NOT NULL              COMMENT '提问用户',
    role         VARCHAR(16)  NOT NULL              COMMENT '角色：user=提问 | assistant=回答',
    content      MEDIUMTEXT                         COMMENT '消息正文（Markdown）',
    citations    JSON                              COMMENT '法条引用列表（JSON数组）',
    sub_queries  JSON                              COMMENT '子查询分解结果',
    confidence   FLOAT                            COMMENT '回答置信度 0-1',
    latency_ms   INT                               COMMENT '端到端耗时（毫秒）',
    created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_user_created (user_id, created_at)
) ENGINE=InnoDB COMMENT='法律问答消息表';

-- ------------------------------------------------------------
-- 4. 合同审查表（contracts）
--    status 流转：pending -> processing -> completed / failed
--    report 字段保存多 Agent 对抗式审查完整结果（含推理链）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contracts (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '合同ID',
    user_id        BIGINT       NOT NULL              COMMENT '上传用户',
    title          VARCHAR(200) NOT NULL              COMMENT '合同标题',
    file_name      VARCHAR(255) NOT NULL              COMMENT '原始文件名',
    file_path      VARCHAR(512) NOT NULL              COMMENT '存储路径',
    contract_type  VARCHAR(32)  NOT NULL DEFAULT '其他' COMMENT '合同类型',
    status         VARCHAR(16)  NOT NULL DEFAULT 'pending' COMMENT '审查状态',
    risk_score     FLOAT                             COMMENT '综合风险评分 0-100',
    risk_level     VARCHAR(8)                        COMMENT '风险等级：高/中/低',
    clause_count   INT                               COMMENT '条款总数',
    risk_count     INT                               COMMENT '风险条款数',
    report         JSON                              COMMENT '审查报告（三方意见+辩论+推理链）',
    duration_ms    INT                               COMMENT '审查耗时（毫秒）',
    error_msg      VARCHAR(512)                      COMMENT '失败原因',
    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_status (status)
) ENGINE=InnoDB COMMENT='合同审查记录表';

-- ------------------------------------------------------------
-- 5. 知识库文档表（kb_documents）
--    doc_type：law=法律法规 | case=裁判案例 | interpretation=司法解释 | template=合同模板
--    元数据（法名/条款号/时效性）供检索过滤与引用校验
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS kb_documents (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '文档ID',
    doc_type      VARCHAR(32)  NOT NULL              COMMENT '文档类型',
    title         VARCHAR(255) NOT NULL              COMMENT '文档标题',
    law_name      VARCHAR(128) DEFAULT ''            COMMENT '所属法律名称',
    article_no    VARCHAR(64)  DEFAULT ''            COMMENT '条款编号（法条类）',
    content       MEDIUMTEXT   NOT NULL              COMMENT '全文内容',
    effectiveness VARCHAR(16)  NOT NULL DEFAULT '有效' COMMENT '时效性：有效/已废止/部分修订',
    publish_date  DATE                              COMMENT '发布/生效日期',
    source        VARCHAR(255) DEFAULT '内置知识库'  COMMENT '数据来源',
    char_count    INT          NOT NULL DEFAULT 0    COMMENT '字符数',
    indexed       TINYINT      NOT NULL DEFAULT 0    COMMENT '是否已建立向量索引',
    created_by    BIGINT                            COMMENT '创建人（系统导入为NULL）',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_type (doc_type),
    INDEX idx_law (law_name),
    UNIQUE KEY uq_doc (doc_type, title, article_no)
) ENGINE=InnoDB COMMENT='法律知识库文档表';

-- ------------------------------------------------------------
-- 6. 审计日志表（audit_logs）
--    全操作留痕，保留 >= 180 天（合规要求）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id         BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '日志ID',
    user_id    BIGINT                              COMMENT '操作用户ID',
    username   VARCHAR(64)  DEFAULT 'anonymous'    COMMENT '操作用户名',
    action     VARCHAR(64)  NOT NULL               COMMENT '动作：LOGIN/LOGOUT/QUERY/UPLOAD...',
    module     VARCHAR(32)  NOT NULL               COMMENT '模块：auth/qa/contract/kb/... ',
    detail     VARCHAR(1024)                       COMMENT '操作详情',
    ip         VARCHAR(64)  DEFAULT 'unknown'      COMMENT '客户端IP',
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_action_time (action, created_at)
) ENGINE=InnoDB COMMENT='安全审计日志表';

-- ------------------------------------------------------------
-- 7. MCP 工具注册表（mcp_tools）
--    记录通过 MCP 协议注册的工具元数据，支持热插拔
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mcp_tools (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '工具ID',
    tool_name   VARCHAR(128) NOT NULL UNIQUE       COMMENT '工具唯一名称',
    server_name VARCHAR(128) NOT NULL              COMMENT '所属工具服务器',
    description VARCHAR(512) DEFAULT ''            COMMENT '工具功能描述',
    schema_json JSON                               COMMENT '输入参数 JSON Schema',
    status      VARCHAR(16)  NOT NULL DEFAULT 'active' COMMENT '状态：active/inactive',
    call_count  INT          NOT NULL DEFAULT 0    COMMENT '累计调用次数',
    last_call_at DATETIME                          COMMENT '最近调用时间',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_server (server_name)
) ENGINE=InnoDB COMMENT='MCP 工具注册表';

-- ------------------------------------------------------------
-- 8. 系统配置表（sys_settings）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_settings (
    setting_key   VARCHAR(64) PRIMARY KEY COMMENT '配置键',
    setting_value VARCHAR(512)           COMMENT '配置值',
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB COMMENT='系统配置表';
