# 会话隔离功能文档

> **文档版本**: 1.0.0
> **更新日期**: 2026-03-15
> **功能状态**: 已实现并测试通过

---

## 目录

- [1. 功能概述](#1-功能概述)
- [2. 技术实现](#2-技术实现)
- [3. API使用指南](#3-api使用指南)
- [4. 数据库设计](#4-数据库设计)
- [5. 故障排查](#5-故障排查)

---

## 1. 功能概述

### 1.1 背景与问题

**原有问题**：
- 所有用户共享同一个 `chat_history` 列表
- 多用户场景下对话历史互相混淆
- 用户A能看到用户B的对话内容
- 重启应用后所有对话历史丢失

**影响范围**：
- 聊天接口 `/api/v1/chat`
- 分析接口 `/api/v1/analyze`
- 所有依赖对话历史的场景

### 1.2 解决方案

**核心策略**: 内存缓存 + MySQL持久化

- **内存缓存**: LRU缓存机制，快速响应活跃会话
- **MySQL持久化**: 对话历史写入数据库，重启不丢失
- **自动清理**: 每会话保留50轮（100条消息）
- **优雅降级**: 数据库失败时降级为无状态模式

### 1.3 功能特性

| 特性 | 说明 |
|------|------|
| **会话隔离** | 每个conversation_id拥有独立对话历史 |
| **历史持久化** | 对话历史保存到MySQL，重启不丢失 |
| **自动清理** | 超过50轮自动清理旧记录 |
| **内存缓存** | LRU缓存100个活跃会话，提升响应速度 |
| **优雅降级** | 数据库失败时不中断服务 |
| **向后兼容** | 不传conversation_id时为无状态模式 |

---

## 2. 技术实现

### 2.1 项目结构

```
app/
├── services/
│   ├── session_service.py    # 会话管理服务（新增）
│   ├── llm_service.py       # LLM服务
│   └── data_service.py      # 数据服务
├── agents/
│   └── crypto_agent.py       # 智能体（修改：移除chat_history，使用session_service）
└── api/
    └── endpoints.py          # API接口（修改：/clear支持session_id）
```

### 2.2 核心组件

#### SessionService（会话管理服务）

**位置**: `app/services/session_service.py`

**职责**:
- 管理用户会话历史
- LRU缓存活跃会话（最多100个）
- MySQL持久化对话历史
- 自动清理过期的数据库记录

**关键方法**:

```python
class SessionService:
    def get_history(self, session_id: str, limit: int = 50) -> List[Dict]:
        """获取会话历史"""

    def add_message(self, session_id: str, role: str, content: str):
        """添加消息到会话"""

    def clear_session(self, session_id: str):
        """清除指定会话"""

    def clear_all(self):
        """清除所有缓存会话"""
```

#### LRUCache（LRU缓存实现）

**特性**:
- 自动淘汰最久未使用的会话
- 最大容量：100个会话
- O(1) 时间复杂度的get/put操作

### 2.3 代码变更

#### crypto_agent.py 修改

**移除内容**:
```python
# ❌ 移除全局chat_history
self.chat_history = []
```

**新增内容**:
```python
# ✅ 使用session_service
from app.services.session_service import session_service

# ✅ 获取会话历史
history = session_service.get_history(conversation_id, limit=50)

# ✅ 保存消息到会话
if conversation_id:
    session_service.add_message(conversation_id, "user", message)
    session_service.add_message(conversation_id, "assistant", response)
```

#### endpoints.py 修改

**变更**:
```python
# ✅ /clear 接口支持 session_id 参数
@router.post("/clear")
async def clear_memory(conversation_id: Optional[str] = None):
    if conversation_id:
        return {"message": f"会话 {conversation_id} 已清除"}
    return {"message": "所有缓存会话已清除"}
```

---

## 3. API使用指南

### 3.1 基本使用

#### 创建新会话并对话

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你好，我是用户A",
    "conversation_id": "user_a_session",
    "lang": "zh"
  }'
```

#### 在同一会话中继续对话

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "你还记得我刚才说了什么吗？",
    "conversation_id": "user_a_session",
    "lang": "zh"
  }'
```

**预期响应**: AI应该记住用户A的名字并正确回答

### 3.2 多用户隔离测试

#### 用户A的对话

```bash
# 用户A第一条消息
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "我叫张三", "conversation_id": "user_a"}'

# 用户A第二条消息
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "我的名字是什么？", "conversation_id": "user_a"}'

# 预期：AI回答"张三"
```

#### 用户B的对话

```bash
# 用户B第一条消息
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "我叫李四", "conversation_id": "user_b"}'

# 用户B第二条消息
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "我的名字是什么？", "conversation_id": "user_b"}'

# 预期：AI回答"李四"，不能看到张三的信息
```

### 3.3 清除会话

#### 清除指定会话

```bash
curl -X POST "http://localhost:8000/api/v1/clear?conversation_id=user_a"
```

**预期**:
- user_a的会话记忆被清除
- user_b的会话不受影响
- 数据库中的记录被删除

#### 清除所有缓存

```bash
curl -X POST "http://localhost:8000/api/v1/clear"
```

**预期**:
- 所有内存缓存的会话被清除
- 数据库记录保留（需手动清理）

### 3.4 流式接口使用

#### chat/stream

```bash
curl -s -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "你好", "conversation_id": "session_123"}' \
  --no-buffer
```

**响应格式**: SSE流式输出

```
event: message
data: {"data": "你好！...", "type": "chunk"}

event: message
data: {"data": "", "type": "complete"}
```

#### analyze/stream

```bash
curl -s -X POST http://localhost:8000/api/v1/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC", "question": "分析", "conversation_id": "session_123"}' \
  --no-buffer
```

---

## 4. 数据库设计

### 4.1 表结构

**表名**: `session_history`

**建表SQL**: 见 `sql/session_history.sql`

```sql
CREATE TABLE IF NOT EXISTS session_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT COMMENT '主键ID',
    session_id VARCHAR(100) NOT NULL COMMENT '会话ID，由外层后端生成',
    role VARCHAR(20) NOT NULL COMMENT '角色：user/assistant',
    content TEXT NOT NULL COMMENT '消息内容',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_session_id (session_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='会话历史表';
```

### 4.2 字段说明

| 字段 | 类型 | 约束 | 说明 |
|------|------|--------|------|
| id | BIGINT | PRIMARY KEY AUTO_INCREMENT | 主键ID |
| session_id | VARCHAR(100) | NOT NULL | 会话ID，由外层后端生成 |
| role | VARCHAR(20) | NOT NULL | 角色（user/assistant） |
| content | TEXT | NOT NULL | 消息内容 |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | 创建时间 |

### 4.3 索引

| 索引名 | 字段 | 类型 | 说明 |
|----------|------|------|------|
| PRIMARY | id | 主键 | 主键索引 |
| idx_session_id | session_id | 普通索引 | 加速按会话ID查询 |
| idx_created_at | created_at | 普通索引 | 加速按时间排序 |

### 4.4 数据清理

**自动清理逻辑**:

```python
def _cleanup_db(self, session_id: str, limit: int):
    """清理数据库中的旧记录"""
    # 1. 查询该会话有多少条消息
    cursor.execute("SELECT COUNT(*) FROM session_history WHERE session_id = %s", (session_id,))

    # 2. 如果超过限制，删除最旧的记录
    if total > limit * 2:
        cursor.execute(
            "DELETE FROM session_history WHERE session_id = %s ORDER BY created_at ASC LIMIT %s",
            (session_id, total - limit * 2)
        )
```

**清理规则**:
- 每个会话最多保留50轮对话（100条消息）
- 超过100条时，删除最旧的记录
- 保留最新的100条消息供后续查询

---

## 5. 故障排查

### 5.1 常见问题

#### 问题1: 会话历史未保存

**症状**:
- AI无法记住之前的对话内容
- 每次对话都是独立的

**排查步骤**:

1. 检查是否传递了 `conversation_id`:
```bash
# ❌ 错误：没有conversation_id
curl -X POST http://localhost:8000/api/v1/chat \
  -d '{"message": "你好"}'

# ✅ 正确：有conversation_id
curl -X POST http://localhost:8000/api/v1/chat \
  -d '{"message": "你好", "conversation_id": "user_123"}'
```

2. 检查数据库连接:
```python
from app.core.config import get_settings
import pymysql

settings = get_settings()
conn = pymysql.connect(
    host=settings.mysql_host,
    port=settings.mysql_port,
    user=settings.mysql_user,
    password=settings.mysql_password,
    database=settings.mysql_database
)
print("数据库连接成功")
```

3. 检查session_history表是否存在:
```sql
SHOW TABLES LIKE 'session_history';
```

#### 问题2: 会话历史混淆

**症状**:
- 用户A能看到用户B的对话
- 不同会话的历史互相干扰

**原因**:
- 没有使用唯一的 `conversation_id`
- 不同用户使用了相同的 `conversation_id`

**解决方案**:
```bash
# 确保每个用户使用不同的conversation_id
# 用户A使用
curl -X POST http://localhost:8000/api/v1/chat \
  -d '{"message": "...", "conversation_id": "user_a_unique_id"}'

# 用户B使用
curl -X POST http://localhost:8000/api/v1/chat \
  -d '{"message": "...", "conversation_id": "user_b_unique_id"}'
```

#### 问题3: 数据库写入失败

**症状**:
- 日志显示 "保存会话消息失败"
- 会话历史不持久化

**排查步骤**:

1. 检查数据库权限:
```sql
SHOW GRANTS FOR CURRENT_USER();
```

2. 检查磁盘空间:
```bash
df -h
```

3. 检查MySQL日志:
```bash
tail -f /var/log/mysql/error.log
```

### 5.2 日志查看

**相关日志位置**:

- 应用日志: `logs/app.log`（如果启用）
- MySQL日志: `/var/log/mysql/`（根据配置）
- Uvicorn日志: 标准输出（可通过Docker logs查看）

**查看方式**:

```bash
# Docker环境
docker logs crypto-analyst-assistant

# 直接运行环境
tail -f logs/app.log
```

### 5.3 性能监控

**关键指标**:

| 指标 | 说明 | 正常值 | 告警阈值 |
|------|------|----------|----------|
| 缓存命中率 | 内存缓存命中/总请求 | >80% | <60% |
| 数据库响应时间 | MySQL查询耗时 | <100ms | >500ms |
| 会话清理频率 | 自动清理触发次数 | 随对话量 | 突然增加 |
| 缓存大小 | LRU缓存中的会话数 | <100 | =100（频繁淘汰）|

**监控方式**:

```python
# 简单的缓存统计
from app.services.session_service import session_service

print(f"缓存会话数: {len(session_service._cache.cache)}")
print(f"缓存容量: {session_service._cache.capacity}")
```

---

## 附录

### A. 会话ID生成建议

**推荐格式**:

```python
# 方式1: UUID（推荐）
import uuid
conversation_id = str(uuid.uuid4())  # 如: "a1b2c3d4-5678-90ab-cdef12345678"

# 方式2: 用户ID + 时间戳
import time
conversation_id = f"user_{user_id}_{int(time.time())}"

# 方式3: 哈希值
import hashlib
conversation_id = hashlib.md5(f"{user_id}_{timestamp}".encode()).hexdigest()
```

### B. 向后兼容性

**无conversation_id的行为**:

```bash
# 不传conversation_id - 无状态模式
curl -X POST http://localhost:8000/api/v1/chat \
  -d '{"message": "你好"}'

# 预期: AI不记得任何历史，每次对话独立
```

**适用场景**:
- 一次性查询，不需要上下文
- 测试和调试
- 对话记忆功能不重要的场景

### C. 配置参数

| 参数 | 位置 | 默认值 | 说明 |
|------|------|----------|------|
| `cache_capacity` | SessionService | 100 | LRU缓存最大会话数 |
| `max_messages` | SessionService | 100 | 每会话最大消息数（50轮） |
| `history_limit` | get_history() | 50 | 获取历史时默认轮数 |

**修改方式**:

```python
# app/services/session_service.py

class SessionService:
    def __init__(self):
        self.settings = settings
        self._cache = LRUCache(capacity=200)  # 修改缓存容量
        self._max_messages = 200  # 修改最大消息数
```

---

**文档维护说明**:
- 重大功能变更时请更新本文档
- 发现新问题时请更新故障排查章节
- 测试通过后验证文档示例

**最后更新**: 2026-03-15
