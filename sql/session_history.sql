-- 会话历史表
-- 用于存储多用户的对话历史，支持会话隔离
CREATE TABLE IF NOT EXISTS session_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    session_id VARCHAR(100) NOT NULL COMMENT '会话ID，由外层后端生成',
    role VARCHAR(20) NOT NULL COMMENT '角色：user/assistant',
    content TEXT NOT NULL COMMENT '消息内容',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_session_id (session_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话历史表';
