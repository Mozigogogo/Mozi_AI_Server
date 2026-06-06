# 信号卡接口文档 — 前后端对接指南

> 最后更新：2026-06-06 | 版本：v1.0

---

## 一、接口总览

前端根据用户意图分流到不同端点：

```
用户输入
  ├─ "BTC怎么样" / "ETH可以买吗"  → POST /signals/v1/chat      （信号卡对话）
  ├─ "最近有什么信号"              → POST /signals/v1/chat      （同一端点，LLM自动选tool）
  ├─ 用户点击"扫描信号"按钮        → GET  /api/v1/signals/scan  （全市场扫描）
  └─ 用户查看某个币种历史胜率       → GET  /api/v1/signals/winrate/{coin}（需新开发）
```

---

## 二、接口 1：信号卡对话

### 基本信息

```
POST /signals/v1/chat
Content-Type: application/json
响应格式：SSE (Server-Sent Events) 流式
```

### 入参

```json
{
  "message": "BTC可以买进吗",
  "coin": "BTC"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| message | string | 是 | 用户原始问题。后端自动检测中英文，用对应语言回复 |
| coin | string | 否 | 前端当前页面关注的币种（如从币种详情页发起对话时传入） |

### 出参 — SSE 事件流

前端逐行解析 `event:` 和 `data:` 字段。完整事件序列：

```
event: thinking
data: {"status": "Analyzing..."}

event: tool_call
data: {"tool": "analyze_coin", "args": {"coin": "BTC"}}

event: signal_card
data: { ... 信号卡完整数据，见下方 ... }

event: tool_result
data: {"tool": "analyze_coin"}

event: content
data: {"text": "根据信号卡数据..."}

event: content
data: {"text": "BTC当前..."}

...（content 多次，流式输出）

event: suggestions
data: {"type": "suggestions", "suggestions": ["BTC历史胜率怎么样", "最近有什么信号", "策略表现如何"]}

event: done
data: {}
```

> 如果 LLM 未调用工具（直接回答），则只有 `content` → `done`，没有 `signal_card`。

### signal_card 事件数据结构

这是前端渲染**信号卡组件**的核心数据：

```json
{
  "type": "signal_card",
  "tier": "pro",
  "card": {
    "coin": "BTC",
    "direction": "short",
    "grade": "A",
    "confidence": 84.0,
    "current_price": 62539.52,
    "entry_zone": [62225.0, 62854.0],
    "stop_loss": 64055.0,
    "take_profit": 59983.0,
    "risk_reward": 1.7,
    "position_pct": 5.6,
    "invalidation": 64055.0,
    "sources": [
      {
        "name": "bigorder_anomaly",
        "score": 55.4,
        "direction": "short",
        "detail": "Binance medium信号, 净流入-924,259, 买28,192/卖952,451"
      },
      {
        "name": "quantitative",
        "score": 11.0,
        "direction": "short",
        "detail": "综合评分-11, 强度弱, 置信度50.0%"
      },
      {
        "name": "technical",
        "score": 15.0,
        "direction": "short",
        "detail": "EMA空头排列(9<21<55), ADX=31趋势强+DI空头, RSI=14严重超卖, MACD死叉+零轴下方"
      }
    ]
  },
  "math": {
    "hurst": 0.50,
    "hurst_interp": "数据不足(需≥100)",
    "predictability": 0.23,
    "kelly": 0.125,
    "mc_bull_prob": 0.19,
    "mc_bear_prob": 0.81,
    "mc_var95": -24.26,
    "vol_regime": "normal",
    "vol_percentile": 41.0,
    "market_regime": "trending_down",
    "findings": [
      "MC上涨概率仅19%，方向存疑",
      "VaR(95%)=-24.3%，尾部风险大"
    ]
  },
  "display": "📊 BTC 交易信号卡 | 🟡A级 | 置信度 84%\n├─ 方向：做空\n├─ 当前价格：$62,539\n...",
  "strategy": {
    "version": 1,
    "regime": "trending_down",
    "global_win_rate": 0.5
  }
}
```

### card 字段说明

| 字段 | 类型 | 说明 | 前端用途 |
|------|------|------|---------|
| coin | string | 币种 | 标题显示 |
| direction | string | "long"=做多 / "short"=做空 | 方向标识（颜色/图标） |
| grade | string | "S"/"A"/"B"/"C" | 信号等级（不同样式） |
| confidence | float | 置信度 0-100 | 进度条/数字展示 |
| current_price | float | 当前价格 | 价格展示 |
| entry_zone | [float, float] | 进场区间 [low, high] | 价格区间展示 |
| stop_loss | float | 止损价 | 止损线展示 |
| take_profit | float | 止盈价 | 止盈线展示 |
| risk_reward | float | 盈亏比 | 关键指标展示 |
| position_pct | float | 建议仓位占总资金% | 仓位建议展示 |
| invalidation | float | 失效价 | 失效条件展示 |
| sources | array | 信号源列表 | 展开详情展示 |

### sources 子项说明

| 字段 | 说明 |
|------|------|
| name | 信号源："bigorder_anomaly"=大单异动 / "quantitative"=量化六因子 / "technical"=技术分析 |
| score | 该源评分 (0-100) |
| direction | 该源判断的方向 "long"/"short"/"neutral" |
| detail | 该源的详细分析文字 |

### math 字段说明（Pro 用户完整版）

| 字段 | 类型 | 说明 |
|------|------|------|
| hurst | float? | Hurst 指数，>0.6 趋势持续，<0.4 均值回归 |
| mc_bull_prob | float? | MC 上涨概率 |
| mc_bear_prob | float? | MC 下跌概率 |
| mc_var95 | float? | 95% VaR（风险值，负数） |
| kelly | float | Kelly 仓位比例 |
| market_regime | string | 市场状态：trending_up/trending_down/volatile/quiet/mean_reverting |
| vol_regime | string | 波动率状态：normal/high/low/extreme |
| vol_percentile | float? | 波动率历史分位 |
| findings | string[] | 关键发现（最多5条） |

### 信号等级对照

| grade | emoji | 含义 | 前端处理建议 |
|-------|-------|------|------------|
| S | 🔴 | 三源共振 + 数学确认 | 高亮强调，可主动推送通知 |
| A | 🟡 | 强信号 | 正常展示 |
| B | ⚪ | 中等信号 | 正常展示，略弱 |
| C | 🔸 | 信号偏弱，仅供参考 | 显示⚠️警告，降低视觉权重 |

### display 字段

`display` 是后端生成的格式化文本，可直接作为 fallback 纯文本展示。前端也可以只用 `card` 结构化数据自行渲染卡片 UI。

### 前端接入示例

```javascript
async function signalChat(message) {
  const resp = await fetch('/signals/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message })
  });

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const lines = buffer.split('\n');
    buffer = lines.pop();

    let eventType = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) eventType = line.slice(7).trim();
      else if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));
        switch (eventType) {
          case 'signal_card': renderCard(data); break;
          case 'content': appendText(data.text); break;
          case 'suggestions': renderSuggestions(data.suggestions); break;
          case 'done': onFinish(); break;
          case 'error': showError(data.error); break;
        }
      }
    }
  }
}
```

### curl 测试

```bash
# 中文
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC可以买进吗"}'

# 英文
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How is ETH"}'
```

---

## 三、接口 2：全市场信号扫描

### 基本信息

```
GET /api/v1/signals/scan
响应格式：JSON
```

### 入参（Query）

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| limit | int | 10 | 返回信号数量上限，最大 50 |
| refresh | bool | false | 是否强制刷新（忽略缓存重新扫描） |

### 出参

```json
{
  "source": "cache",
  "total_coins_scanned": 95,
  "count": 10,
  "signals": [
    {
      "coin": "BTC",
      "direction": "short",
      "grade": "A",
      "confidence": 80.0,
      "current_price": 62539.52,
      "entry_low": 62225.0,
      "entry_high": 62854.0,
      "stop_loss": 64055.0,
      "take_profit": 59983.0,
      "risk_reward_ratio": 1.7,
      "position_pct": 5.6,
      "invalidation_price": 64055.0,
      "confidence": 80.0,
      "sources": [...],
      "math": {...},
      "strategy": {...},
      "created_at": "2026-06-06 10:30:00"
    }
  ],
  "display": [
    "📊 BTC 交易信号卡 | 🟡A级 | 置信度 80%\n├─ ...",
    "📊 ETH 交易信号卡 | 🟡A级 | 置信度 84%\n├─ ..."
  ],
  "scan_time": 45.2,
  "cached_at": "2026-06-06 10:30:00"
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| source | 数据来源："cache"=缓存新鲜 / "cache_stale"=缓存过期后台刷新中 / "fresh"=现场扫描 |
| total_coins_scanned | 本次扫描覆盖了多少个币种 |
| count | 实际返回的信号数量 |
| signals | 信号卡数组，按置信度降序。每个元素是 SignalCard 的完整 model_dump() |
| display | 对应的格式化文本数组，和 signals 一一对应 |
| scan_time | 扫描耗时（秒） |
| cached_at | 缓存生成时间 |

### 前端使用场景

1. **用户点击"扫描信号"按钮** → 调用此接口
2. 首次调用 `refresh=false`（走缓存，秒返回）
3. 如果 `source` 是 `cache_stale`，前端可提示"数据刷新中"
4. 用 `signals` 数组渲染信号卡列表
5. 用 `display` 数组做纯文本 fallback

### curl 测试

```bash
# 走缓存
curl http://localhost:8000/api/v1/signals/scan?limit=20

# 强制刷新（慢，约1分钟）
curl http://localhost:8000/api/v1/signals/scan?limit=20&refresh=true
```

### 后台扫描机制

后端每 **30 分钟**自动执行一次全市场扫描（`_market_scan_task`），结果存入 MySQL + 本地文件。前端调用 scan 接口时优先读缓存，不触发实时扫描。

---

## 四、接口 3：历史胜率查询（待开发）

### 需求背景

用户想查看某个币种的历史信号胜率，包含：
- 总体胜率、交易次数
- 按等级（S/A/B）分组的胜率
- 按方向（做多/做空）分组的胜率
- 平均盈亏、盈亏分布

### 建议 API 设计

```
GET /api/v1/signals/winrate/{coin}
```

### 入参（Path + Query）

| 参数 | 类型 | 位置 | 必填 | 默认 | 说明 |
|------|------|------|------|------|------|
| coin | string | path | 是 | - | 币种，如 BTC |
| days | int | query | 否 | 30 | 回看天数 |
| grade | string | query | 否 | - | 过滤等级：S/A/B |

### 建议出参

```json
{
  "coin": "BTC",
  "days": 30,
  "summary": {
    "total": 45,
    "win_rate": 68.9,
    "avg_profit_pct": 3.2,
    "avg_loss_pct": -2.1,
    "profit_factor": 1.52,
    "best_trade_pct": 8.5,
    "worst_trade_pct": -4.2
  },
  "by_grade": {
    "S": {"total": 8, "win_rate": 87.5, "avg_pnl": 4.2},
    "A": {"total": 25, "win_rate": 72.0, "avg_pnl": 2.5},
    "B": {"total": 12, "win_rate": 50.0, "avg_pnl": 0.3}
  },
  "by_direction": {
    "long": {"total": 28, "win_rate": 75.0, "avg_pnl": 3.1},
    "short": {"total": 17, "win_rate": 58.8, "avg_pnl": 1.8}
  },
  "recent_settlements": [
    {
      "id": 123,
      "direction": "long",
      "grade": "A",
      "entry_price": 62500,
      "settled_price": 64800,
      "pnl_pct": 3.68,
      "status": "hit_tp",
      "created_at": "2026-06-04 10:00:00",
      "settled_at": "2026-06-04 18:00:00"
    }
  ],
  "generated_at": "2026-06-06 14:30:00"
}
```

### 字段说明

| 字段 | 说明 |
|------|------|
| summary.total | 该币种近 N 天已结算的信号卡总数 |
| summary.win_rate | 总胜率（hit_tp 算赢 + expired 中正 pnl 算赢）|
| summary.avg_profit_pct | 盈利交易的平均盈利% |
| summary.avg_loss_pct | 亏损交易的平均亏损% |
| summary.profit_factor | 盈利因子 = 总盈利 / 总亏损 |
| by_grade | 按等级分组，每组的总数/胜率/平均盈亏 |
| by_direction | 按方向分组 |
| recent_settlements | 最近 10 条结算记录，前端渲染列表 |

### 后端开发指南

**数据来源**：`signal_card_history` 表，查询条件：

```sql
WHERE coin = 'BTC'
  AND status IN ('hit_tp', 'hit_sl', 'expired')
  AND created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY)
```

**核心逻辑**（参考已有 `settlement.py` 中的 `get_accumulated_winrate`）：

```python
@router.get("/winrate/{coin}")
async def get_coin_winrate(
    coin: str,
    days: int = Query(30, ge=7, le=365),
    grade: Optional[str] = Query(None),
):
    coin = coin.upper()

    conn = get_conn()
    cursor = conn.cursor(DictCursor)

    # 1. 总体统计
    cursor.execute("""
        SELECT status, pnl_pct, grade, direction
        FROM signal_card_history
        WHERE coin = %s
          AND status IN ('hit_tp', 'hit_sl', 'expired')
          AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
    """, (coin, days))
    rows = cursor.fetchall()

    # 2. 计算 summary / by_grade / by_direction
    # 3. 查最近 10 条结算明细
    # 4. 返回
```

**已有的基础设施**：
- `settlement.py` → `get_accumulated_winrate(coin, days)` — 已实现总体胜率查询
- `settlement.py` → `_get_conn()` — MySQL 连接
- `adaptive_strategy.py` → `get_coin_winrate(coin)` — 本地 JSON 胜率

新接口只需扩展 `get_accumulated_winrate`，增加 by_grade / by_direction / recent_settlements。

### 前端使用场景

1. 用户在币种详情页查看"历史信号表现"
2. 前端渲染：胜率饼图 + 等级柱状图 + 最近交易列表
3. 通过 `/signals/v1/chat` 问"BTC胜率怎么样"也会触发 `query_winrate` tool，但返回的是简化数据；这个独立接口返回完整数据

### curl 测试（开发后）

```bash
# BTC 近30天胜率
curl http://localhost:8000/api/v1/signals/winrate/BTC

# ETH 近7天
curl http://localhost:8000/api/v1/signals/winrate/ETH?days=7

# 只看 A 级
curl http://localhost:8000/api/v1/signals/winrate/BTC?grade=A
```

---

## 五、数据依赖

### 必需的 MySQL 表

**signal_card_history**（信号卡生成 + 结算）：

```sql
CREATE TABLE IF NOT EXISTS signal_card_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    coin VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,        -- long / short
    grade VARCHAR(5) NOT NULL,             -- S / A / B / C
    entry_low DECIMAL(20,8),
    entry_high DECIMAL(20,8),
    stop_loss DECIMAL(20,8) NOT NULL,
    take_profit DECIMAL(20,8) NOT NULL,
    current_price DECIMAL(20,8) NOT NULL,
    invalidation_price DECIMAL(20,8),
    confidence DECIMAL(5,2),
    risk_reward_ratio DECIMAL(5,2),
    position_pct DECIMAL(5,2),
    sources_json MEDIUMTEXT,               -- JSON: 信号源明细
    math_json MEDIUMTEXT,                  -- JSON: 数学推导
    strategy_version INT DEFAULT 1,
    regime VARCHAR(20),
    adaptive_weights_json TEXT,            -- JSON: 权重快照
    status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/hit_tp/hit_sl/expired
    settled_price DECIMAL(20,8),
    settled_at TIMESTAMP NULL,
    pnl_pct DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_coin_status (coin, status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**scan_cache**（全市场扫描缓存）：

```sql
CREATE TABLE IF NOT EXISTS scan_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    total_coins INT NOT NULL DEFAULT 0,
    signal_count INT NOT NULL DEFAULT 0,
    scan_time FLOAT NOT NULL DEFAULT 0,
    results_json MEDIUMTEXT,
    displays_json MEDIUMTEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 后台自动任务

| 任务 | 间隔 | 说明 | 前端是否感知 |
|------|------|------|------------|
| 全市场扫描 | 30 分钟 | 扫描 ~95 个币种，结果存 MySQL + 本地文件 | 前端调 scan 接口读到缓存 |
| 信号卡结算 | 10 分钟 | 用真实 K 线验证 pending 卡的 TP/SL | 前端查 winrate 接口读到结算结果 |
| 周期复盘 | 每周日 03:00 | 汇总一周表现，演化策略权重 | 间接影响后续生成的信号卡质量 |

---

## 六、环境变量

信号卡系统无新增必填环境变量，使用现有 MySQL 配置。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| SIGNAL_SCAN_INTERVAL | 1800 | 全市场扫描间隔（秒），默认 30 分钟 |
| SCAN_INTERVAL | 30 | 大单侦测扫描间隔（秒），与信号卡无关 |

---

## 七、常见问题

**Q: 信号卡 Chat 和主问答 Agent 的区别？**

主问答 `/api/v1/chat/stream` 是通用对话，走 Skill 路由。信号卡 Chat `/signals/v1/chat` 是独立的量化分析专用端点，Function Calling 直接调信号卡引擎，不走 Skill 体系。前端根据用户意图分流。

**Q: scan 接口很慢怎么办？**

首次调用或 `refresh=true` 需要现场扫描 ~95 个币种，约 1 分钟。正常走缓存是秒返回。建议前端默认 `refresh=false`，提供"刷新"按钮时才传 `refresh=true`。

**Q: 信号卡的语言如何控制？**

后端自动检测 `message` 中的中文字符比例。中文消息返回中文信号卡，英文返回英文。信号卡标签、技术指标详情、数学推导 findings 全部跟随。前端无需传语言参数。

**Q: C 级信号卡怎么处理？**

grade="C" 表示信号弱，display 中包含"⚠️ 信号偏弱，仅供参考"。建议前端：
- 降低视觉权重（灰色/虚线边框）
- 显示警告标签
- 不主动推送通知
