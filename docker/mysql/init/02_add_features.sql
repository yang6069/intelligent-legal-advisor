-- ============================================================
-- 智法通 V6.1 新增特性：文书起草 + 用户套餐配额
-- 执行时机：全新部署自动生效；已有数据库需手动执行
-- 字符集：utf8mb4
-- ============================================================

USE zhifatong;

-- ------------------------------------------------------------
-- 1. 文书草稿表（draft_documents）
--    支持全部角色，独立于合同审查流程
--    status: draft（草稿）/ ai_generated（AI 已生成）/ finalized（已定稿）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS draft_documents (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '文书ID',
    user_id       BIGINT       NOT NULL             COMMENT '所属用户',
    title         VARCHAR(255) NOT NULL DEFAULT '未命名文书' COMMENT '文书标题',
    doc_type      VARCHAR(32)  NOT NULL DEFAULT '起诉状' COMMENT '文书类型',
    template_id   VARCHAR(64)  DEFAULT ''           COMMENT '使用的模板标识',
    content       LONGTEXT     DEFAULT NULL         COMMENT '文书正文（富文本/Markdown）',
    metadata      JSON         DEFAULT NULL         COMMENT '起草元数据（当事人、金额、日期等结构化字段）',
    ai_prompt     TEXT         DEFAULT NULL         COMMENT '用户起草时的 AI 提示词',
    ai_params     JSON         DEFAULT NULL         COMMENT 'AI 生成参数',
    status        VARCHAR(16)  NOT NULL DEFAULT 'draft' COMMENT '状态',
    word_count    INT          NOT NULL DEFAULT 0   COMMENT '字数统计',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_type (doc_type),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='文书起草草稿表';

-- ------------------------------------------------------------
-- 2. 用户套餐配额表（sys_user_quota）
--    普通用户每天免费 50 次问答；购买套餐获得更多次数
--    admin / legal_admin 不受限制（role != 'user' 时跳过检查）
--
--    套餐类型：
--      free     - 免费版：每日重置 50 次
--      basic    - 基础套餐（39元）：500 次问答，30 天有效
--      pro      - 专业套餐（59元）：1000 次问答，30 天有效
--      elite    - 精英套餐（99元）：2000 次问答，30 天有效
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sys_user_quota (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '配额记录ID',
    user_id         BIGINT       NOT NULL UNIQUE    COMMENT '用户ID（与 sys_users 一对一）',
    plan_type       VARCHAR(16)  NOT NULL DEFAULT 'free' COMMENT '套餐类型',
    daily_free_used INT          NOT NULL DEFAULT 0 COMMENT '当日免费额度已用',
    daily_free_date DATE                              COMMENT '免费额度对应的自然日（用于日重置）',
    total_balance   INT          NOT NULL DEFAULT 0 COMMENT '付费套餐剩余总次数（跨日累积）',
    total_purchased INT          NOT NULL DEFAULT 0 COMMENT '历史累计购买次数',
    plan_expire_at  DATETIME     NULL               COMMENT '当前付费套餐到期时间（NULL=永久免费）',
    last_purchase_at DATETIME    NULL               COMMENT '最近一次购买时间',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_plan (plan_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户问答配额表';

-- ------------------------------------------------------------
-- 3. 套餐购买记录表（plan_orders）
--    模拟订单系统；真实接入支付时可扩展
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS plan_orders (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '订单ID',
    user_id       BIGINT       NOT NULL             COMMENT '购买用户',
    plan_type     VARCHAR(16)  NOT NULL             COMMENT '购买套餐',
    price         DECIMAL(10,2) NOT NULL            COMMENT '支付金额',
    qa_quota      INT          NOT NULL             COMMENT '获得问答次数',
    duration_days INT          NOT NULL DEFAULT 30  COMMENT '有效天数',
    order_no      VARCHAR(64)  NOT NULL UNIQUE      COMMENT '订单号',
    status        VARCHAR(16)  NOT NULL DEFAULT 'paid' COMMENT '状态：paid/refunded',
    paid_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_paid (paid_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='套餐购买记录表';

-- 为已有用户自动创建配额记录（首次迁移时）
INSERT IGNORE INTO sys_user_quota (user_id, plan_type, daily_free_date)
SELECT id, 'free', CURDATE() FROM sys_users;
