# Mozi - 加密市场智能分析平台

基于 Skill 架构的加密货币分析助手，集成**行情分析**、**大单侦测**、**信号卡**三大模块，通过 SSE 流式输出提供实时智能分析。

## 系统架构

```
前端意图分流
  ├─ 行情相关问题  → /api/v1/*           → 行情分析 Agent
  │   意图识别 → Skill路由 → 数据获取 → LLM回答 → 建议问题
  │
  ├─ 大单相关问题  → /bigorder/v1/*      → 大单侦测 Agent
  │   Function Calling → Tool执行 → LLM回答 → 建议问题
  │   后台30s扫描 → 四维打分 → 异动信号 → SSE推送
  │
  └─ 量化/信号问题 → /signals/v1/*       → 信号卡 Chat
      Function Calling → Tool执行 → 信号卡推送 → LLM解读 → 建议问题
      后台30min全市场扫描 → Alpha雷达 → 信号卡生成 → SSE推送
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
| 建议问题 | 每次回答后推荐3个相关问题 |

### 信号卡系统

| 能力 | 示例问题 |
|------|---------|
| 币种信号卡 | BTC怎么样、ETH可以买进吗、SOL怎么走 |
| 历史胜率 | BTC历史胜率怎么样 |
| 策略报告 | 策略表现如何 |
| 全市场扫描 | 最近有什么信号 |
| 中英双语 | How is BTC、Analyze ETH |
| C级弱信号兜底 | 任何币种都能返回信号卡（弱信号标C级） |
| 信号卡推送 | 结构化卡片数据 + LLM文字解读 |

## 项目结构

```
agent/
├── app/
│   ├── main.py                    # FastAPI 入口 + 生命周期管理
│   ├── core/
│   │   ├── config.py              # 配置加载
│   │   ├── llm_client.py          # 共享 AsyncOpenAI 客户端单例
│   │   ├── session.py             # 会话管理（用户隔离 + 币种记忆）
│   │   └── exceptions.py          # 异常定义
│   ├── api/
│   │   ├── endpoints.py           # Agent SSE 端点
│   │   ├── schemas.py             # 请求/响应模型
│   │   └── skill_endpoints.py     # 测试端点
│   ├── skills/                     # Agent Skill 体系
│   │   ├── agent.py               # 主编排器
│   │   ├── intent_analyzer.py     # LLM 意图识别
│   │   ├── skill_router.py        # Skill 路由
│   │   ├── response_generator.py  # LLM 回答生成 + 建议问题模板
│   │   ├── query_skills/          # 查询类 Skill（价格/K线/新闻/衍生品）
│   │   └── analysis_skills/       # 分析类 Skill（技术面/量化/综合）
│   ├── bigorder/                   # BigOrder Agent 子模块
│   │   ├── models.py              # 数据模型（TickData/SignalScore/AnomalySignal）
│   │   ├── deps.py                # 依赖管理（Redis 可选）
│   │   ├── consumer.py            # Redis ZSET 消费器
│   │   ├── scorer.py              # 四维打分引擎
│   │   ├── history.py             # 历史基线（滑动窗口，线程安全）
│   │   ├── llm_analyzer.py        # 信号 LLM 解读
│   │   ├── endpoints.py           # REST + SSE 端点
│   │   └── chat.py                # Function Calling 对话 + 建议问题
│   ├── services/
│   │   ├── data_service.py        # 外部 API 调用 + 缓存
│   │   └── session_service.py     # MySQL 会话持久化
│   ├── signals/                    # 信号卡系统
│   │   ├── models.py              # 数据模型（SignalCard/MathDerivation/StrategyMeta）
│   │   ├── math_engine.py         # 数学推导引擎（Hurst/熵/MC/Kelly/波动率锥）
│   │   ├── fusion.py              # 三源融合引擎（大单/量化/技术 + 自适应权重）
│   │   ├── adaptive_strategy.py   # 自适应策略引擎（权重演化 + 市场状态感知）
│   │   ├── chat.py                # 信号卡 Chat 独立 SSE 端点（Function Calling）
│   │   ├── endpoints.py           # REST 端点（generate/scan/stream/strategy）
│   │   ├── settlement.py          # 结算引擎（真实K线后验 + 扫描缓存）
│   │   ├── backtest.py            # 回测引擎
│   │   ├── review.py              # 周期复盘引擎
│   │   └── alpha_scanner.py       # 全市场 Alpha 雷达扫描器
│   └── utils/
│       └── validators.py          # 输入校验
├── config/
│   └── settings.py                # 统一配置（所有 Agent 的参数）
├── sql/
│   ├── session_history.sql        # 会话历史表（Agent 用）
│   └── anomaly_history.sql        # 异动历史表（BigOrder 用）
├── tests/
│   ├── test_live_bigorder.py      # BigOrder 实时接口测试
│   └── test_bigorder.py           # BigOrder 单元测试
├── .env.example                   # 环境变量模板（不含真实密钥）
├── requirements.txt               # Python 依赖
├── Dockerfile                     # Docker 镜像构建
├── docker-compose.yml             # Docker Compose 编排
└── start.sh                       # 启动脚本
```

---

## 部署指南

### 前置依赖

| 组件 | 版本要求 | 用途 | 必选 |
|------|---------|------|------|
| Python | >= 3.9 | 运行服务 | 是 |
| MySQL | >= 5.7 | 会话存储 + 异动历史 | 是 |
| Redis | >= 6.0 | 大单数据源 + 信号缓存 | BigOrder 需要 |
| DeepSeek API | - | LLM 推理 | 是 |

### 第一步：获取代码

```bash
git clone <repository-url>
cd agent
```

### 第二步：初始化数据库

在 MySQL 中依次执行建表脚本：

```bash
# 1. Agent 数据库（会话历史）
mysql -h <mysql_host> -u root -p<password> <community_database> < sql/session_history.sql

# 2. BigOrder 数据库（异动历史，启用大单侦测时需要）
mysql -h <bigorder_mysql_host> -u root -p<password> <exchange_database> < sql/anomaly_history.sql
```

如果两个 Agent 共用同一个 MySQL 实例，可在同一数据库下执行两张表：

```sql
-- 在 community 数据库下直接执行
source sql/session_history.sql;
source sql/anomaly_history.sql;
```

### 第三步：安装依赖

```bash
python -m venv venv
source venv/bin/activate    # Linux/Mac
# venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

### 第四步：配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入真实配置
vim .env
```

**必填配置（行情分析 Agent）**：

```bash
# MySQL
MYSQL_HOST=your_mysql_host
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DATABASE=community

# DeepSeek API
DEEPSEEK_API_KEY=sk-your-api-key
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-pro
```

**BigOrder 配置（启用大单侦测时需要）**：

```bash
# 开启 BigOrder
REDIS_ENABLED=true
REDIS_HOST=your_redis_host
REDIS_PORT=6379
REDIS_PASSWORD=your_redis_password

# BigOrder 独立 MySQL（不配置则 fallback 到主 MySQL）
BIGORDER_MYSQL_HOST=your_mysql_host
BIGORDER_MYSQL_PORT=3306
BIGORDER_MYSQL_USER=root
BIGORDER_MYSQL_PASSWORD=your_password
BIGORDER_MYSQL_DATABASE=exchange

# BigOrder LLM（使用更快更便宜的模型）
BIGORDER_DEEPSEEK_MODEL=deepseek-v4-flash
```

**注意**：`.env` 文件已被 `.gitignore` 排除，不会被提交到代码仓库。切勿将真实密钥提交到 git。

### 第五步：启动服务

#### 方式一：直接启动

```bash
# 仅行情分析（无需 Redis）
REDIS_ENABLED=false python -m app.main

# 行情分析 + 大单侦测
REDIS_ENABLED=true python -m app.main
```

#### 方式二：使用启动脚本

```bash
chmod +x start.sh
./start.sh
```

#### 方式三：Docker 部署（推荐生产环境）

```bash
# 构建镜像
docker build -t mozi-agent .

# 运行容器
docker run -d \
  --name mozi-agent \
  -p 8000:8000 \
  --env-file .env \
  mozi-agent

# 或使用 Docker Compose
docker-compose up -d
```

### 第六步：验证部署

```bash
# 1. 服务是否启动
curl http://localhost:8000/
# 期望：{"app":"Crypto Analyst Assistant","status":"running",...}

# 2. Agent 健康检查
curl http://localhost:8000/api/v1/health
# 期望：{"status":"healthy",...}

# 3. BigOrder 健康检查（需 REDIS_ENABLED=true）
curl http://localhost:8000/bigorder/v1/health
# 期望：{"status":"healthy","redis":"connected","watched_coins":[...]}

# 4. 测试对话
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC怎么样", "conversation_id": "test-001"}'
# 期望：SSE 流式输出，包含 start → chunk(多次) → suggestions → complete

# 5. 测试 BigOrder 对话（需 REDIS_ENABLED=true）
curl -N -X POST http://localhost:8000/bigorder/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC有什么异动"}'
# 期望：SSE 流式输出，包含 thinking → tool_call → tool_result → content(多次) → suggestions → done

# 6. 测试信号卡 Chat
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC怎么样"}'
# 期望：SSE 流式输出，包含 thinking → tool_call → signal_card → content(多次) → suggestions → done

# 7. 生成 BTC 信号卡
curl http://localhost:8000/api/v1/signals/generate/BTC
```

---

## 生产环境注意事项

### 1. 单 Worker 运行

BigOrder 后台扫描任务在 FastAPI lifespan 中启动，多个 worker 会导致重复扫描。**必须使用单 worker**：

```bash
# 正确
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# 如果需要多 worker 提升并发，建议将后台扫描拆为独立进程
```

### 2. 关闭调试模式

```bash
DEBUG=False
```

关闭后：
- 错误响应不再暴露内部堆栈
- OpenAPI 文档中的错误详情为 null

### 3. 并发性能

经测试（单 worker 模式）：

| 并发数 | 成功率 | 平均响应时间 | 说明 |
|--------|--------|-------------|------|
| 5 | 100% | 0.03s | 无压力 |
| 10 | 100% | 0.03s | 无压力 |
| 20 | 65% | ~51s | 瓶颈在 DeepSeek API 限流 |

如需支持更高并发，建议：
- 提升 DeepSeek API 并发配额
- 在前端加 Nginx 限流
- 考虑将 LLM 调用改为队列模式

### 4. CORS 安全

生产环境应限制允许的来源，修改 `app/main.py`：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://your-frontend-domain.com"],  # 替换为实际前端域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 5. 日志

当前使用 `print()` 输出日志。生产环境建议：
- 配置 uvicorn 的 `--log-level warning`
- 或替换为 Python `logging` 模块接入日志收集系统

### 6. 监控

建议监控以下指标：
- `/api/v1/health` 和 `/bigorder/v1/health` 端点可用性
- BigOrder 后台扫描是否正常运行（观察日志中是否有扫描异常）
- Redis 连接状态
- MySQL 连接数

---

## API 接口

### 行情分析 Agent

#### 对话式分析（SSE 流式）

```
POST /api/v1/chat/stream
```

请求体：
```json
{
  "message": "BTC怎么样",
  "conversation_id": "user-001",
  "lang": "zh"
}
```

SSE 事件序列：

| 事件 | type 字段 | 说明 |
|------|----------|------|
| 开始 | `start` | 流开始 |
| 内容 | `chunk` | 文本片段（多次） |
| 建议 | `suggestions` | 3个推荐问题 |
| 完成 | `complete` | 流结束 |
| 错误 | `error` | 异常信息 |

#### 深度分析（SSE 流式）

```
POST /api/v1/analyze/stream
```

请求体：
```json
{
  "symbol": "BTC",
  "question": "综合分析",
  "conversation_id": "user-001"
}
```

### 信号卡系统

#### 信号卡对话（SSE 流式）

```
POST /signals/v1/chat
```

请求体：
```json
{
  "message": "BTC可以买进吗",
  "coin": "BTC"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| message | string | 是 | 用户问题，自动识别中英文 |
| coin | string | 否 | 前端上下文传入的关注币种 |

**SSE 事件序列：**

```
1. event: thinking   → {"status": "Analyzing..."}
2. event: tool_call  → {"tool": "analyze_coin", "args": {"coin": "BTC"}}
3. event: signal_card → {完整信号卡数据（前端渲染卡片）}
4. event: tool_result → {"tool": "analyze_coin"}
5. event: content    → {"text": "..."}（LLM文字解读，流式多次）
6. event: suggestions → {"type": "suggestions", "suggestions": [...]}
7. event: done       → {}
```

**信号卡事件数据结构（`signal_card`）：**

```json
{
  "type": "signal_card",
  "tier": "pro",
  "card": {
    "coin": "BTC",
    "direction": "long",
    "grade": "A",
    "confidence": 80.0,
    "current_price": 67500.00,
    "entry_zone": [67200.00, 67800.00],
    "stop_loss": 65100.00,
    "take_profit": 70500.00,
    "risk_reward": 2.2,
    "position_pct": 5.6,
    "invalidation": 65100.00,
    "sources": [
      {"name": "bigorder_anomaly", "score": 55.4, "direction": "long", "detail": "Binance medium信号..."},
      {"name": "quantitative", "score": 11.0, "direction": "short", "detail": "综合评分-11..."},
      {"name": "technical", "score": 15.0, "direction": "short", "detail": "EMA空头排列..."}
    ]
  },
  "math": {
    "hurst": 0.50,
    "hurst_interp": "数据不足(需≥100)",
    "predictability": 0.32,
    "kelly": 0.125,
    "mc_bull_prob": 0.28,
    "mc_bear_prob": 0.72,
    "mc_var95": -20.8,
    "vol_regime": "high",
    "vol_percentile": 82.1,
    "market_regime": "trending_down",
    "findings": ["MC上涨概率仅28%，方向存疑", "VaR(95%)=-20.8%，尾部风险大"]
  },
  "display": "📊 BTC 交易信号卡 | 🟡A级 | 置信度 80%\n├─ 方向：做多\n...",
  "strategy": {
    "version": 1,
    "regime": "trending_down",
    "global_win_rate": 0.5
  }
}
```

**信号等级说明：**

| 等级 | 条件 | 标识 | 说明 |
|------|------|------|------|
| S | 3源一致 + 置信度≥65% + 数学确认 | 🔴 | 多维共振，最高置信度 |
| A | 2+源一致 + 置信度≥50% | 🟡 | 强信号 |
| B | 2+源一致 + 置信度≥35% | ⚪ | 中等信号 |
| C | 方向冲突或信号偏弱 | 🔸 | 低置信度，仅供参考 |

**可用工具（Function Calling 自动选择）：**

| 工具名 | 说明 | 参数 |
|--------|------|------|
| analyze_coin | 分析币种生成信号卡 | coin (必填) |
| query_winrate | 查询历史胜率 | coin, days(默认30) |
| query_strategy | 查看策略性能报告 | 无 |
| query_scan_results | 查看最近全市场扫描结果 | limit(默认10) |

**前端接入示例：**

```javascript
const response = await fetch('/signals/v1/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: 'BTC可以买进吗' })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const text = decoder.decode(value);
  const lines = text.split('\n');

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      currentEvent = line.slice(7).trim();
    } else if (line.startsWith('data: ')) {
      const data = JSON.parse(line.slice(6));

      switch (currentEvent) {
        case 'signal_card':
          renderSignalCard(data);        // 渲染信号卡组件
          break;
        case 'content':
          appendText(data.text);          // 追加LLM文字（流式）
          break;
        case 'suggestions':
          renderSuggestions(data.suggestions);  // 渲染推荐追问
          break;
        case 'done':
          finishResponse();
          break;
      }
    }
  }
}
```

**curl 测试：**

```bash
# 中文
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC可以买进吗"}'

# 英文
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How is ETH"}'

# 查看策略报告
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "策略表现如何"}'

# 查看扫描结果
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "最近有什么信号"}'
```

#### REST 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/signals/generate/{coin} | 生成指定币种信号卡 |
| GET | /api/v1/signals/scan?limit=10 | 扫描热门币种信号 |
| GET | /api/v1/signals/stream?tier=pro | SSE 实时信号推送 |
| GET | /api/v1/signals/strategy/performance | 策略性能报告 |
| POST | /api/v1/signals/strategy/evolve?coin=BTC | 手动触发策略演化 |
| POST | /api/v1/signals/settle | 手动触发结算 |
| GET | /api/v1/signals/review | 历史复盘摘要 |
| POST | /api/v1/signals/review/trigger | 手动触发复盘 |

### 大单侦测 Agent（需 REDIS_ENABLED=true）

#### 对话式查询（SSE 流式）

```
POST /bigorder/v1/chat
```

请求体：
```json
{
  "message": "BTC有什么异动",
  "coin": "BTC"
}
```

SSE 事件序列：

| 事件 | 说明 |
|------|------|
| `thinking` | 正在分析 |
| `tool_call` | 调用了哪个工具及参数 |
| `tool_result` | 工具返回的数据 |
| `content` | LLM 回答文本片段（多次） |
| `suggestions` | 3个推荐问题 |
| `done` | 流结束 |
| `error` | 异常信息 |

可用工具（Function Calling 自动选择）：

| 工具名 | 说明 | 必填参数 |
|--------|------|---------|
| query_anomalies | 异动信号列表 | 无 |
| query_coin_signal | 币种异动评分详情 | coin |
| query_order_flow | 资金流向统计 | coin |
| query_large_orders | 大单明细（按金额排序） | coin |
| query_history | 历史异动记录 | 无 |
| query_exchange_compare | 多交易所买卖分布对比 | coin |
| manual_scan | 手动触发全量扫描 | 无 |

#### REST 接口

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | /bigorder/v1/health | - | 健康检查（含 Redis 状态和监控币种列表） |
| GET | /bigorder/v1/anomalies | exchange?, min_score?, limit? | 异动信号列表 |
| GET | /bigorder/v1/coin/{coin}/signal | - | 币种异动详情（来自缓存） |
| GET | /bigorder/v1/coin/{coin}/flow | window?(分钟) | 资金流向统计 |
| GET | /bigorder/v1/coin/{coin}/orders | top?, exchange? | 大单明细 |
| GET | /bigorder/v1/coin/{coin}/compare | - | 多交易所对比 |
| GET | /bigorder/v1/history | coin?, days?, level?, limit? | 历史异动记录 |
| POST | /bigorder/v1/scan | coins?(数组) | 手动触发全量扫描 |
| GET | /bigorder/v1/stream | - | SSE 实时信号推送 |

REST 接口示例：

```bash
# 查询所有异动信号
curl http://localhost:8000/bigorder/v1/anomalies?min_score=60&limit=20

# 查询 BTC 异动详情
curl http://localhost:8000/bigorder/v1/coin/BTC/signal

# 查询 ETH 10分钟资金流
curl http://localhost:8000/bigorder/v1/coin/ETH/flow?window=10

# 查询 BTC 在 Binance 的 Top5 大单
curl http://localhost:8000/bigorder/v1/coin/BTC/orders?top=5&exchange=Binance

# 对比 SOL 各交易所
curl http://localhost:8000/bigorder/v1/coin/SOL/compare

# 查询过去3天的强信号
curl "http://localhost:8000/bigorder/v1/history?days=3&level=strong"

# 手动触发 BTC+ETH 扫描
curl -X POST http://localhost:8000/bigorder/v1/scan \
  -H "Content-Type: application/json" \
  -d '["BTC", "ETH"]'

# SSE 实时信号订阅
curl -N http://localhost:8000/bigorder/v1/stream
```

---

## 大单侦测引擎原理

### 四维评分模型

每个币种在每个交易所上独立计算四个维度的得分：

| 维度 | 计算方式 | 权重 | 触发条件 |
|------|---------|------|---------|
| **资金流** (Net Flow) | 买入额 - 卖出额，标准差偏离度 | 35% | 超过 mean + 2σ |
| **大单密度** (Density) | 时间窗口内成交笔数，标准差偏离度 | 30% | 超过 mean + 3σ |
| **买卖比** (Ratio) | 买量 / (买量 + 卖量)，偏离中性线 0.5 | 20% | >0.7 或 <0.3 |
| **价格变化** (Price) | (最新价 - 最早价) / 最早价 | 15% | 变化 >1.5% |

总分 = 各维度得分 × 权重之和（满分 100）

### 信号分级

| 等级 | 条件 | 标识 | 处理 |
|------|------|------|------|
| 强信号 | 总分 ≥ 70 | 红标 | LLM 自动解读 + MySQL 持久化 + SSE 推送 |
| 中等信号 | 50 ≤ 总分 < 70 | 黄标 | Redis 缓存 |
| 无信号 | 总分 < 50 | - | 不展示 |

### 数据流

```
交易所成交数据 → Redis ZSET（key: {Exchange}_big_deal_{BASE}_{side}）
                                ↓
        后台每30秒扫描 → Consumer 读取时间窗口数据
                                ↓
        Scorer 四维评分（与 HistoryTracker 基线对比）
                                ↓
        信号生成 → 强信号 → LLM 解读 → MySQL 存储
                               ↓
              Redis 缓存 ← SSE 推送 → 用户
```

### 历史基线

- 使用 Redis HASH 存储各币种各交易所各维度的均值/标准差
- 滑动窗口保留最近 288 个数据点（约 2.4 小时，按 30 秒间隔）
- 线程安全（`threading.Lock` 保护读写）
- 默认基线 std=1.0 避免除零错误

### Redis 数据结构

| Key 类型 | Key 格式 | 说明 |
|----------|---------|------|
| ZSET | `{Exchange}_big_deal_{BASE}_{side}` | 成交数据（score=时间戳） |
| ZSET | `signal:anomaly` | 全局异动信号（score=时间戳，保留最新1000条） |
| HASH | `signal:coin:{COIN}` | 各币种最新信号详情 |
| HASH | `stats:{COIN}:{WINDOW}` | 各币种资金流统计 |
| ZSET | `orders:large:{COIN}` | 各币种大单明细（score=金额，保留Top100） |
| HASH | `bigorder:history` | 历史基线（field={exchange}:{coin}:{dimension}） |

---

## 测试

```bash
# 运行 BigOrder 单元测试
python -m pytest tests/test_bigorder.py -v

# 运行实时接口测试（需先启动服务）
python tests/test_live_bigorder.py
```

测试覆盖：
- 数据模型序列化/反序列化
- 四维打分逻辑（边界条件、零值、极端值）
- 历史基线更新（首次/滑动窗口/线程安全）
- Redis Consumer 数据解析（正常/异常JSON/连接失败）
- 7 个 Function Calling 工具执行
- 建议问题生成（中英文、币种提取、模板填充）
- 安全性（SQL注入、密钥不硬编码）
- 超纲/边界问题处理
- 并发性能

---

## 扩展新 Agent

项目支持模块化扩展，添加新 Agent 只需：

1. 在 `app/` 下创建新的子目录（如 `app/trading/`）
2. 实现独立的 `models.py`、`deps.py`、`endpoints.py`、`chat.py`
3. 在 `config/settings.py` 添加开关配置（如 `trading_enabled`）
4. 在 `app/main.py` 条件注册路由和生命周期任务
5. 在 `sql/` 目录添加建表脚本

---

## 环境变量参考

### 行情分析 Agent

| 变量 | 默认值 | 必填 | 说明 |
|------|--------|------|------|
| MYSQL_HOST | localhost | 是 | Agent MySQL 地址 |
| MYSQL_PORT | 3306 | 否 | MySQL 端口 |
| MYSQL_USER | root | 是 | MySQL 用户名 |
| MYSQL_PASSWORD | | 是 | MySQL 密码 |
| MYSQL_DATABASE | community | 是 | Agent 数据库名 |
| DEEPSEEK_API_KEY | | 是 | DeepSeek API Key |
| DEEPSEEK_API_BASE | https://api.deepseek.com | 否 | API 地址 |
| DEEPSEEK_MODEL | deepseek-v4-pro | 否 | Agent LLM 模型 |
| API_HOST | 0.0.0.0 | 否 | 监听地址 |
| PORT | 8000 | 否 | 服务端口 |
| DEBUG | false | 否 | 调试模式 |

### 大单侦测 Agent

| 变量 | 默认值 | 必填 | 说明 |
|------|--------|------|------|
| REDIS_ENABLED | false | 否 | 开启 BigOrder Agent |
| REDIS_HOST | localhost | 条件 | Redis 地址 |
| REDIS_PORT | 6379 | 否 | Redis 端口 |
| REDIS_PASSWORD | | 条件 | Redis 密码 |
| BIGORDER_MYSQL_HOST | (fallback到MYSQL_HOST) | 否 | BigOrder MySQL 地址 |
| BIGORDER_MYSQL_PASSWORD | (fallback到MYSQL_PASSWORD) | 否 | BigOrder MySQL 密码 |
| BIGORDER_MYSQL_DATABASE | exchange | 否 | BigOrder 数据库名 |
| BIGORDER_DEEPSEEK_MODEL | deepseek-v4-flash | 否 | BigOrder LLM 模型 |
| SCAN_INTERVAL | 30 | 否 | 大单侦测后台扫描间隔(秒) |
| SIGNAL_SCAN_INTERVAL | 1800 | 否 | 信号卡全市场扫描间隔(秒)，默认30分钟 |
| HISTORY_WINDOW_COUNT | 288 | 否 | 历史基线窗口数 |
| SCORE_THRESHOLD_STRONG | 70 | 否 | 强信号阈值 |
| SCORE_THRESHOLD_MEDIUM | 50 | 否 | 中等信号阈值 |
| FLOW_WINDOW_SECONDS | 300 | 否 | 资金流时间窗口(秒) |
| PRICE_WINDOW_SECONDS | 900 | 否 | 价格变化时间窗口(秒) |

### 权重和阈值（一般不需要调整）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| WEIGHT_NET_FLOW | 0.35 | 资金流权重 |
| WEIGHT_DENSITY | 0.30 | 大单密度权重 |
| WEIGHT_RATIO | 0.20 | 买卖比权重 |
| WEIGHT_PRICE | 0.15 | 价格变化权重 |
| SIGMA_NET_FLOW | 2.0 | 资金流 sigma 阈值 |
| SIGMA_DENSITY | 3.0 | 密度 sigma 阈值 |
| RATIO_UPPER | 0.7 | 买卖比上限触发值 |
| RATIO_LOWER | 0.3 | 买卖比下限触发值 |
| PRICE_CHANGE_PCT | 0.015 | 价格变化百分比阈值(1.5%) |

---

## 常见问题

### Q: 启动报错 `ModuleNotFoundError: No module named 'xxx'`

安装依赖：`pip install -r requirements.txt`

### Q: BigOrder 路由返回 404

检查 `REDIS_ENABLED=true` 是否设置。BigOrder 路由仅在 Redis 启用时注册。

### Q: BigOrder 健康检查返回 `redis: disconnected`

检查 Redis 连接配置（host/port/password）是否正确，确认 Redis 服务正在运行。

### Q: 对话返回 `503 BigOrder 功能需要 Redis`

同上，需确认 Redis 连接正常。

### Q: 大单侦测没有数据

确认 Redis 中存在成交数据的 ZSET，key 格式为 `{Exchange}_big_deal_{BASE}_{side}`（如 `Binance_big_deal_BTC_buy`）。可执行 `redis-cli keys "*big_deal*"` 检查。

### Q: 历史查询返回空数据

确认 MySQL 中 `anomaly_history` 表已创建（执行 `sql/anomaly_history.sql`），且有强信号（总分 ≥ 70）被写入。

### Q: 如何只运行行情分析 Agent，不依赖 Redis？

设置 `REDIS_ENABLED=false` 即可。BigOrder 所有路由不会注册，Agent 功能完全不受影响。

### Q: 信号卡 Chat 接口返回的信号卡数据怎么用？

`signal_card` 事件包含 `card`（结构化数据）和 `display`（格式化文本）。前端用 `card` 渲染自定义卡片组件，`display` 可直接作为纯文本展示。C 级信号前端应显示弱信号警告。

### Q: 信号卡的语言如何控制？

自动检测用户消息语言。中文消息返回中文信号卡，英文消息返回英文信号卡。信号卡字段（方向、止损止盈、技术指标详情）全部跟随用户语言。
