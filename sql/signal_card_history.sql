-- 信号卡历史表
-- 存储所有生成的交易信号卡及其后验结算结果
CREATE TABLE IF NOT EXISTS signal_card_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    coin VARCHAR(20) NOT NULL COMMENT '币种：BTC/ETH/SOL...',
    direction VARCHAR(10) NOT NULL COMMENT '方向：long/short',
    grade VARCHAR(5) NOT NULL COMMENT '等级：S/A/B',

    -- 价格区间（12 位小数，支持 meme 币如 PEPE/BONK）
    entry_low DECIMAL(24,12) COMMENT '进场区间下沿',
    entry_high DECIMAL(24,12) COMMENT '进场区间上沿',
    stop_loss DECIMAL(24,12) NOT NULL COMMENT '止损价',
    take_profit DECIMAL(24,12) NOT NULL COMMENT '止盈价',
    current_price DECIMAL(24,12) NOT NULL COMMENT '生成时价格',
    invalidation_price DECIMAL(24,12) COMMENT '失效价格线',

    -- 指标
    confidence DECIMAL(5,2) COMMENT '置信度 0-100',
    risk_reward_ratio DECIMAL(5,2) COMMENT '盈亏比',
    position_pct DECIMAL(5,2) COMMENT '建议仓位%',

    -- 结构化数据（JSON）
    sources_json TEXT COMMENT '信号源明细',
    math_json TEXT COMMENT '数学推导摘要',

    -- 策略快照
    strategy_version INT DEFAULT 1 COMMENT '生成时的策略版本',
    regime VARCHAR(20) COMMENT '市场状态',
    adaptive_weights_json TEXT COMMENT '生成时的自适应权重快照',

    -- 结算
    status VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT 'pending/active/hit_tp/hit_sl/expired',
    settled_price DECIMAL(24,12) COMMENT '结算价格',
    settled_at TIMESTAMP NULL COMMENT '结算时间',
    pnl_pct DECIMAL(8,4) COMMENT '实际盈亏%',

    -- 时间
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '生成时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    INDEX idx_status (status),
    INDEX idx_coin_status (coin, status),
    INDEX idx_created_at (created_at),
    INDEX idx_settled_at (settled_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号卡历史表';
