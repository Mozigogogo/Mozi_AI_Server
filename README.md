# Mozi - 加密市场智能分析平台

基于 Skill 架构的加密货币分析助手，集成行情分析和大单侦测两大 Agent，通过 SSE 流式输出提供实时智能分析。

## 系统架构

```
用户请求
  ├─ /api/v1/*          → Agent（行情分析）
  │   意图识别 → Skill路由 → 数据获取 → LLM回答
  │
  └─ /bigorder/v1/*     → BigOrder Agent（大单侦测）
      Function Calling → Tool执行 → LLM回答
      后台30s扫描 → 四维打分 → 异动信号
```

**技术栈**：FastAPI + DeepSeek LLM + MySQL + Redis + SSE

## 功能一览

### 行情分析 Agent

| 能力 | 示例问题 |
|------|---------|
| 实时行情 | BTC怎么样、ETH当前价格 |
| 技术分析 | SOL技术面怎么样 |
| 衍生品 | BTC多空比和资金费率 |
| 量化评分 | ETH量化评分 |
| 新闻资讯 | BTC最近有什么新闻 |
| 综合分析 | 全面分析一下ETH |
| 会话记忆 | 同一对话中沿用币种上下文 |

### 大单侦测 Agent（需 Redis）

| 能力 | 示例问题 |
|------|---------|
| 异动信号 | 市场有哪些异动 |
| 币种信号 | BTC有什么异动 |
| 资金流向 | ETH资金流怎么样 |
| 大单明细 | BTC最近的大单 |
| 交易所对比 | 对比SOL各交易所 |
| 历史查询 | 过去7天有哪些强信号 |
| SSE实时推送 | 新信号自动推送 |

## 项目结构

```
app/
├── main.py                    # FastAPI 入口 + 生命周期管理
├── core/
│   ├── config.py              # 配置加载
│   ├── llm_client.py          # 共享 LLM 客户端单例
│   ├── session.py             # 会话管理（用户隔离 + 币种记忆）
│   └── exceptions.py          # 异常定义
├── api/
│   ├── endpoints.py           # Agent SSE 端点
│   ├── schemas.py             # 请求/响应模型
│   └── skill_endpoints.py     # 测试端点
├── skills/                     # Agent Skill 体系
│   ├── agent.py               # 主编排器
│   ├── intent_analyzer.py     # LLM 意图识别
│   ├── skill_router.py        # Skill 路由
│   ├── response_generator.py  # LLM 回答生成
│   ├── query_skills/          # 查询类 Skill（价格/K线/新闻/衍生品）
│   └── analysis_skills/       # 分析类 Skill（技术面/量化/综合）
├── bigorder/                   # BigOrder Agent 子模块
│   ├── models.py              # 数据模型（TickData/SignalScore/AnomalySignal）
│   ├── deps.py                # 依赖管理（Redis 可选）
│   ├── consumer.py            # Redis ZSET 消费器
│   ├── scorer.py              # 四维打分引擎
│   ├── history.py             # 历史基线（滑动窗口）
│   ├── llm_analyzer.py        # 信号 LLM 解读
│   ├── endpoints.py           # REST + SSE 端点
│   └── chat.py                # Function Calling 对话
├── services/
│   ├── data_service.py        # 外部 API 调用 + 缓存
│   └── session_service.py     # MySQL 会话持久化
└── utils/
    └── validators.py          # 输入校验
config/
    └── settings.py            # 统一配置（所有 Agent 的参数）
```

## 快速开始

### 1. 环境准备

```bash
git clone <repository-url>
cd agent

python -m venv venv
source venv/bin/activate   # Linux/Mac
# venv\Scripts\activate    # Windows

pip install -r requirements.txt
```

### 2. 配置 .env

```bash
cp .env.example .env
```

**必填配置**：

```bash
# MySQL（Agent 用）
MYSQL_HOST=your_mysql_host
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=community

# DeepSeek API
DEEPSEEK_API_KEY=your_api_key
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

**可选配置（启用大单侦测 Agent）**：

```bash
# 开启 BigOrder Agent
REDIS_ENABLED=true
REDIS_HOST=your_redis_host
REDIS_PORT=6379
REDIS_PASSWORD=your_password

# BigOrder 独立 MySQL（存储异动历史）
BIGORDER_MYSQL_HOST=your_mysql_host
BIGORDER_MYSQL_PASSWORD=your_password
BIGORDER_MYSQL_DATABASE=exchange

# BigOrder 使用更快的模型
BIGORDER_DEEPSEEK_MODEL=deepseek-v4-flash

# 引擎参数
SCAN_INTERVAL=30
SCORE_THRESHOLD_STRONG=70
SCORE_THRESHOLD_MEDIUM=50
```

### 3. 启动服务

```bash
# 仅行情分析（无需 Redis）
REDIS_ENABLED=false python -m app.main

# 行情分析 + 大单侦测
REDIS_ENABLED=true python -m app.main
```

服务默认运行在 `http://localhost:8000`

### 4. 验证

```bash
# Agent 健康检查
curl http://localhost:8000/api/v1/health

# BigOrder 健康检查（需 REDIS_ENABLED=true）
curl http://localhost:8000/bigorder/v1/health
```

## API 接口

### 行情分析 Agent

#### 对话式分析（SSE 流式）

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC怎么样", "conversation_id": "user-001"}'
```

SSE 事件序列：`start → chunk(多次) → suggestions → complete`

#### 深度分析（SSE 流式）

```bash
curl -N -X POST http://localhost:8000/api/v1/analyze/stream \
  -H "Content-Type: application/json" \
  -d '{"symbol": "BTC", "question": "综合分析", "conversation_id": "user-001"}'
```

### 大单侦测 Agent（需 REDIS_ENABLED=true）

#### 对话式查询（SSE 流式）

```bash
curl -N -X POST http://localhost:8000/bigorder/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC有什么异动"}'
```

SSE 事件序列：`thinking → tool_call → tool_result → content(多次) → done`

#### REST 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /bigorder/v1/health | 健康检查 |
| GET | /bigorder/v1/anomalies | 异动信号列表 |
| GET | /bigorder/v1/coin/{coin}/signal | 币种异动详情 |
| GET | /bigorder/v1/coin/{coin}/flow | 资金流向统计 |
| GET | /bigorder/v1/coin/{coin}/orders | 大单明细 |
| GET | /bigorder/v1/coin/{coin}/compare | 多交易所对比 |
| GET | /bigorder/v1/history | 历史异动记录 |
| POST | /bigorder/v1/scan | 手动触发全量扫描 |
| GET | /bigorder/v1/stream | SSE 实时信号推送 |

## Docker 部署

### 构建并运行

```bash
docker build -t mozi-agent .
docker run -d \
  --name mozi-agent \
  -p 8000:8000 \
  --env-file .env \
  mozi-agent
```

### Docker Compose

```bash
docker-compose up -d
```

### 注意事项

- 生产环境设置 `DEBUG=False`
- BigOrder 后台扫描为单 worker 运行，`uvicorn --workers 1`
- `REDIS_ENABLED=false` 时 BigOrder 相关路由不会注册，Agent 功能不受影响

## 扩展新 Agent

项目支持模块化扩展，添加新 Agent 只需：

1. 在 `app/` 下创建新的子目录（如 `app/trading/`）
2. 实现独立的路由、依赖、模型
3. 在 `config/settings.py` 添加开关配置
4. 在 `app/main.py` 条件注册路由和生命周期

## 环境变量参考

| 变量 | 默认值 | 必填 | 说明 |
|------|--------|------|------|
| MYSQL_HOST | localhost | 是 | Agent MySQL 地址 |
| MYSQL_DATABASE | community | 是 | Agent 数据库名 |
| DEEPSEEK_API_KEY | | 是 | DeepSeek API Key |
| DEEPSEEK_MODEL | deepseek-v4-pro | 否 | Agent LLM 模型 |
| API_PORT | 8000 | 否 | 服务端口 |
| REDIS_ENABLED | false | 否 | 开启 BigOrder Agent |
| REDIS_HOST | localhost | 条件 | Redis 地址 |
| REDIS_PASSWORD | | 条件 | Redis 密码 |
| BIGORDER_MYSQL_DATABASE | exchange | 条件 | BigOrder 数据库 |
| BIGORDER_DEEPSEEK_MODEL | deepseek-v4-flash | 否 | BigOrder LLM 模型 |
| SCAN_INTERVAL | 30 | 否 | BigOrder 扫描间隔(秒) |
| SCORE_THRESHOLD_STRONG | 70 | 否 | 强信号阈值 |
| SCORE_THRESHOLD_MEDIUM | 50 | 否 | 中等信号阈值 |
