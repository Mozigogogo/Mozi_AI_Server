-- 迁移：将价格字段从 DECIMAL(20,4) 改为 DECIMAL(24,12)
-- 解决 PEPE/BONK 等 meme 币价格精度丢失问题（4 位小数 → 0）
-- 可在 MySQL 控制台直接执行

ALTER TABLE signal_card_history
    MODIFY COLUMN entry_low DECIMAL(24,12) COMMENT '进场区间下沿',
    MODIFY COLUMN entry_high DECIMAL(24,12) COMMENT '进场区间上沿',
    MODIFY COLUMN stop_loss DECIMAL(24,12) NOT NULL COMMENT '止损价',
    MODIFY COLUMN take_profit DECIMAL(24,12) NOT NULL COMMENT '止盈价',
    MODIFY COLUMN current_price DECIMAL(24,12) NOT NULL COMMENT '生成时价格',
    MODIFY COLUMN invalidation_price DECIMAL(24,12) COMMENT '失效价格线',
    MODIFY COLUMN settled_price DECIMAL(24,12) COMMENT '结算价格';

-- 清理已知脏数据：BTC id=1 的异常价格 105500（真实 ~63k，数据源一次性故障）
DELETE FROM signal_card_history WHERE id = 1 AND coin = 'BTC' AND current_price > 100000;

-- 清理 price=0 的无效卡（PEPE/BONK 精度丢失导致）
DELETE FROM signal_card_history WHERE current_price = 0;
