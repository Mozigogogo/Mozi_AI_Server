-- 大单异动历史表
-- 存储四维评分达标的异动信号记录
CREATE TABLE IF NOT EXISTS anomaly_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    coin VARCHAR(20) NOT NULL COMMENT '币种：BTC/ETH/SOL...',
    exchange VARCHAR(30) NOT NULL COMMENT '交易所：Binance/OKX/Bybit/Bitget/Gate',
    total_score DECIMAL(6,1) NOT NULL COMMENT '综合得分 0~100',
    level VARCHAR(10) NOT NULL COMMENT '信号等级：strong/medium',
    net_flow_score DECIMAL(6,1) DEFAULT 0 COMMENT '资金流得分',
    density_score DECIMAL(6,1) DEFAULT 0 COMMENT '大单密度得分',
    ratio_score DECIMAL(6,1) DEFAULT 0 COMMENT '买卖比得分',
    price_score DECIMAL(6,1) DEFAULT 0 COMMENT '价格变化得分',
    buy_amount DECIMAL(20,2) DEFAULT 0 COMMENT '买入总额(USD)',
    sell_amount DECIMAL(20,2) DEFAULT 0 COMMENT '卖出总额(USD)',
    net_flow DECIMAL(20,2) DEFAULT 0 COMMENT '净流入(USD)',
    price_change_pct DECIMAL(10,4) DEFAULT 0 COMMENT '价格变化百分比',
    llm_analysis TEXT COMMENT 'LLM 智能解读',
    timestamp BIGINT NOT NULL COMMENT '事件时间戳(ms)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    INDEX idx_coin (coin),
    INDEX idx_level (level),
    INDEX idx_total_score (total_score),
    INDEX idx_created_at (created_at),
    INDEX idx_exchange_coin (exchange, coin)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='大单异动历史表';
