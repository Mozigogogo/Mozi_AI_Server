# 墨子（Mozi）— 交易信号卡 产品文档 v2.1

> 最后更新：2026-06-05 | 版本：v2.1（新增 Signal Chat 独立对话接口 + C级信号 + 中英双语）

---

## 目录

1. [系统架构总览](#1-系统架构总览)
2. [信号卡核心概念](#2-信号卡核心概念)
3. [Signal Chat 对话接口](#3-signal-chat-对话接口)
4. [REST API 接口](#4-rest-api-接口)
5. [SSE 事件格式](#5-sse-事件格式)
6. [后验验证与周期复盘](#6-后验验证与周期复盘)
7. [前端集成指南](#7-前端集成指南)
8. [会员等级差异](#8-会员等级差异)
9. [后端部署指南](#9-后端部署指南)
10. [数据表结构](#10-数据表结构)
11. [配置参数说明](#11-配置参数说明)
12. [迭代与扩展](#12-迭代与扩展)

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      墨子（Mozi）v2.0                        │
├─────────────────┬──────────────────┬────────────────────────┤
│  行情问答 Agent  │  大单侦测 Agent   │   信号卡系统（新增）    │
│  (原有，不变)    │  (原有，不变)     │                        │
│                 │                  │  ┌──────────────────┐  │
│  /chat/stream   │  /bigorder/v1/   │  │ 数学推导引擎      │  │
│  /analyze/stream│    chat          │  │ (Hurst/熵/MC/Kelly)│ │
│                 │    stream        │  └────────┬─────────┘  │
│                 │                  │  ┌────────▼─────────┐  │
│                 │                  │  │ 三源融合 + 自适应  │  │
│                 │                  │  │ (大单/量化/技术)   │  │
│                 │                  │  └────────┬─────────┘  │
│                 │                  │  ┌────────▼─────────┐  │
│                 │                  │  │ 结算引擎          │  │
│                 │                  │  │ (真实K线后验)      │  │
│                 │                  │  └────────┬─────────┘  │
│                 │                  │  ┌────────▼─────────┐  │
│                 │                  │  │ 周期复盘引擎       │  │
│                 │                  │  │ (每周自动迭代)     │  │
│                 │                  │  └──────────────────┘  │
└─────────────────┴──────────────────┴────────────────────────┘
        │                │                    │
        ▼                ▼                    ▼
    前端对话流       前端SSE推送        前端卡片组件 + 推送
```

### 三种使用模式

| 模式 | 触发方式 | 适用场景 | 会员要求 |
|------|---------|---------|---------|
| **对话生成** | 用户在聊天中问"BTC能买吗" | 用户主动查询 | Lite/Pro |
| **主动推送** | 后台扫描 → SSE 推送 | 实时监控 | Pro |
| **API 查询** | 调用 `/generate/{coin}` | 第三方集成 | Pro |

---

## 2. 信号卡核心概念

### 2.1 信号生成流程

```
用户请求 /generate/BTC 或 "BTC能买吗"
        │
        ▼
┌─ 第1层：数学推导（第一性原理）────────────────────┐
│  Hurst指数(0~1)     →  趋势持续性 vs 均值回归      │
│  Shannon熵          →  价格可预测性                │
│  蒙特卡洛模拟1000条  →  涨跌概率 + VaR              │
│  波动率锥            →  当前波动率历史分位           │
│  市场状态检测         →  trending/volatile/quiet    │
│  输出：regime + 评分修正(-100~+100)                 │
└──────────────────────────────────────────────────┘
        │
        ▼
┌─ 第2层：三源独立评分（自适应权重）────────────────┐
│                                                   │
│  ① 大单异动（权重动态）                            │
│     Redis/Scorer → 净流入 + 买卖比 → 方向+得分     │
│                                                   │
│  ② 量化六因子（权重动态）                           │
│     趋势/动量/量价/资金/波动/结构 → 综合评分         │
│                                                   │
│  ③ 技术分析（权重动态，7维升级）                    │
│     EMA(9/21/55) + ADX + RSI + MACD + BB           │
│     + Supertrend + OBV → 加权得分                  │
└──────────────────────────────────────────────────┘
        │
        ▼
┌─ 第3层：融合 + 数学修正 ─────────────────────────┐
│  加权方向分 = Σ(方向 × 得分 × 自适应权重)           │
│  数学修正    += Hurst确认 / MC概率 / 波动率过滤      │
│  数学否决    ← 推导 < -40 且置信度低 → 不生成        │
│                                                   │
│  至少2源方向一致 → 生成信号卡                        │
│  3源 + 数学确认 → S级                               │
│  2源 + 置信度≥50% → A级                             │
│  其他 → B级                                        │
└──────────────────────────────────────────────────┘
```

### 2.2 自适应权重机制

权重不是固定的，而是根据历史表现动态调整：

```
初始权重：大单异动=0.35 / 量化六因子=0.35 / 技术分析=0.30

市场状态感知（不同状态下的参数）：
┌───────────────┬─────────┬─────────┬──────────┬──────────────┐
│ 市场状态       │ 止损倍数 │ 止盈倍数 │ 最低置信度 │ 权重倾向      │
├───────────────┼─────────┼─────────┼──────────┼──────────────┤
│ trending_up   │ 1.5 ATR │ 3.0 ATR │ 40%      │ 偏技术面      │
│ trending_down │ 1.5 ATR │ 2.5 ATR │ 45%      │ 偏技术面      │
│ mean_reverting│ 2.0 ATR │ 2.0 ATR │ 55%      │ 偏大单异动    │
│ volatile      │ 2.5 ATR │ 2.0 ATR │ 60%      │ 偏大单异动    │
│ quiet         │ 1.2 ATR │ 3.5 ATR │ 35%      │ 偏技术面      │
└───────────────┴─────────┴─────────┴──────────┴──────────────┘
```

### 2.3 Kelly 仓位建议

基于 Kelly 公式的数学推导：
```
f* = (p × b - q) / b
其中 p=胜率, q=1-p, b=盈亏比

实际使用半 Kelly（f*/2）以降低方差
```

### 2.4 信号等级

| 等级 | 条件 | 含义 |
|------|------|------|
| S | 3源一致 + 置信度≥65% + 数学确认 | 多维共振，最高置信度 |
| A | 2源一致 + 置信度≥50% | 强信号 |
| B | 2源一致 + 置信度<50% | 中等信号 |
| C | 方向冲突或信号偏弱（Chat兜底） | ⚠️ 信号偏弱，仅供参考 |

---

## 3. API 接口文档

### 3.1 信号卡对话（SSE 流式）

```
POST /signals/v1/chat
```

**概述：** 独立的信号卡对话接口，与 `/bigorder/v1/chat` 并列。前端根据用户意图分流到不同端点。自动识别中英文，返回对应语言的信号卡和 LLM 解读。

**请求体：**

```json
{
  "message": "BTC可以买进吗",
  "coin": "BTC"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| message | string | 是 | 用户问题。支持中文/英文，自动检测语言 |
| coin | string | 否 | 前端上下文传入的关注币种（附加到系统提示） |

**完整 SSE 事件序列：**

```
1. event: thinking    → {"status": "Analyzing..."}              // LLM 正在选择工具
2. event: tool_call   → {"tool": "analyze_coin", "args": {...}} // 选择了哪个工具
3. event: signal_card → {完整信号卡数据}                          // 前端渲染卡片 ⭐
4. event: tool_result → {"tool": "analyze_coin"}                // 工具执行完成
5. event: content     → {"text": "..."}                         // LLM 文字解读（多次）
6. event: suggestions → {"type": "suggestions", "suggestions": [...]}  // 推荐追问
7. event: done        → {}                                       // 流结束
```

> 如果 LLM 未选择工具（直接回答），则只返回 `content` + `done`。

**signal_card 事件完整数据结构：**

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
      {
        "name": "bigorder_anomaly",
        "score": 55.4,
        "direction": "long",
        "detail": "Binance medium信号, 净流入+160,268, 买200,874/卖40,606"
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
        "detail": "EMA空头排列(9<21<55), ADX=33趋势强+DI空头, RSI=12严重超卖"
      }
    ]
  },
  "math": {
    "hurst": 0.50,
    "hurst_interp": "数据不足(需≥100)",
    "predictability": 0.3243,
    "kelly": 0.125,
    "mc_bull_prob": 0.275,
    "mc_bear_prob": 0.725,
    "mc_var95": -20.77,
    "vol_regime": "high",
    "vol_percentile": 82.1,
    "market_regime": "trending_down",
    "findings": [
      "MC上涨概率仅28%，方向存疑",
      "VaR(95%)=-20.8%，尾部风险大"
    ]
  },
  "display": "📊 BTC 交易信号卡 | 🟡A级 | 置信度 80%\n├─ 方向：做多\n...",
  "strategy": {
    "version": 1,
    "regime": "trending_down",
    "global_win_rate": 0.5
  }
}
```

**C 级信号卡示例（弱信号兜底）：**

```json
{
  "type": "signal_card",
  "tier": "pro",
  "card": {
    "coin": "BTC",
    "direction": "long",
    "grade": "C",
    "confidence": 30.0,
    ...
  },
  "display": "📊 BTC 交易信号卡 | 🔸C级 | 置信度 30%\n│  ⚠️ 信号偏弱，仅供参考\n├─ 方向：做多\n..."
}
```

> C 级卡的特征：grade="C"、confidence 较低、display 中有"⚠️ 信号偏弱"警告。C 级卡不参与结算统计。

**Function Calling 工具列表：**

| 工具名 | 说明 | 参数 | 超时 |
|--------|------|------|------|
| `analyze_coin` | 分析币种，生成信号卡 + 量化分析 | coin (必填) | 60s |
| `query_winrate` | 查询历史胜率和回测数据 | coin, days(默认30) | 10s |
| `query_strategy` | 查看策略性能报告（自适应权重、各因子胜率） | 无 | 5s |
| `query_scan_results` | 查看最近一次全市场扫描结果 | limit(默认10) | 10s |

**前端接入示例（JavaScript）：**

```javascript
async function signalChat(message, coin) {
  const response = await fetch('/signals/v1/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, coin })
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();  // 保留未完成的行

    let currentEvent = '';
    for (const line of lines) {
      if (line.startsWith('event: ')) {
        currentEvent = line.slice(7).trim();
      } else if (line.startsWith('data: ')) {
        const data = JSON.parse(line.slice(6));

        switch (currentEvent) {
          case 'signal_card':
            // ⭐ 渲染信号卡组件（用 card 结构化数据或 display 文本）
            renderSignalCard(data);
            break;
          case 'content':
            // 追加 LLM 文字解读（流式）
            appendText(data.text);
            break;
          case 'suggestions':
            // 渲染推荐追问按钮
            renderSuggestions(data.suggestions);
            break;
          case 'thinking':
            showLoading();
            break;
          case 'done':
            finishResponse();
            break;
          case 'error':
            showError(data.error);
            break;
        }
      }
    }
  }
}

// 调用示例
signalChat('BTC可以买进吗');
signalChat('How is ETH');
signalChat('最近有什么信号');
signalChat('策略表现如何');
```

**curl 测试：**

```bash
# 中文 — 分析币种
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "BTC可以买进吗"}'

# 英文 — 自动识别
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "How is ETH"}'

# 查看策略
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "策略表现如何"}'

# 查看扫描结果
curl -N -X POST http://localhost:8000/signals/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "最近有什么信号"}'
```

### 3.2 信号卡生成

```
GET /api/v1/signals/generate/{coin}
```

**参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| coin | path | 必填 | 币种符号（如 BTC） |
| kline_type | query | 2 | K线类型：1=小时 2=天 3=周 |

**响应：**

```json
{
  "status": "success",
  "signal": {
    "coin": "BTC",
    "direction": "long",
    "grade": "S",
    "current_price": 67500.00,
    "entry_low": 67200.00,
    "entry_high": 67800.00,
    "stop_loss": 65100.00,
    "take_profit": 70500.00,
    "risk_reward_ratio": 2.2,
    "confidence": 78.0,
    "position_pct": 8.0,
    "invalidation_price": 65100.00,
    "win_rate": 68.0,
    "sample_count": 45,
    "avg_profit_pct": 3.2,
    "sources": [
      {"name": "bigorder_anomaly", "score": 72, "direction": "long", "weight": 0.35},
      {"name": "quantitative", "score": 65, "direction": "long", "weight": 0.37},
      {"name": "technical", "score": 55, "direction": "long", "weight": 0.28}
    ],
    "math": {
      "hurst": 0.63,
      "hurst_interpretation": "H=0.63 趋势持续性强",
      "entropy_predictability": 0.42,
      "kelly_fraction": 0.125,
      "monte_carlo_bull_prob": 0.78,
      "monte_carlo_bear_prob": 0.22,
      "monte_carlo_var95": -5.2,
      "vol_regime": "normal",
      "vol_percentile": 35.0,
      "market_regime": "trending_up",
      "market_regime_confidence": 0.8,
      "math_score_adjustment": 25.0,
      "math_confidence": 0.7,
      "key_findings": [
        "H=0.63 趋势持续性强，支撑做多",
        "MC上涨概率78%，方向确认",
        "市场状态=trending_up，与信号方向一致"
      ]
    },
    "strategy": {
      "strategy_version": 3,
      "regime": "trending_up",
      "adaptive_weights": {"bigorder_anomaly": 0.35, "quantitative": 0.37, "technical": 0.28},
      "global_win_rate": 0.58,
      "evolution_count": 5
    },
    "status": "pending",
    "created_at": "2026-06-01 14:30:00"
  },
  "display": "📊 BTC 交易信号卡 | 🔴S级 | 置信度 78%\n├─ 方向：做多\n...",
  "backtest": {
    "win_rate": 68.0,
    "sample_count": 45,
    "avg_profit_pct": 3.2,
    "sharpe_ratio": 1.8,
    "sortino_ratio": 2.3,
    "max_drawdown_pct": -8.5,
    "statistical_significance": {
      "z_score": 2.45,
      "p_value": 0.02,
      "is_significant": true,
      "effect_size": 0.52
    }
  }
}
```

**无信号时：**

```json
{
  "coin": "BTC",
  "status": "no_signal",
  "message": "当前信号不足，未达到生成信号卡的条件（需要至少 2 个信号源方向一致）"
}
```

### 3.2 扫描热门币种

```
GET /api/v1/signals/scan?limit=10
```

返回所有热门币中符合条件的信号卡，按置信度降序。

### 3.3 详细回测

```
GET /api/v1/signals/backtest/{coin}?direction=long&walk_forward=true
```

### 3.4 策略管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/signals/strategy/performance` | GET | 策略性能报告 |
| `/api/v1/signals/strategy/evolve?coin=BTC` | POST | 手动触发策略演化 |
| `/api/v1/signals/strategy/record?source_name=quantitative&pnl_pct=3.5&direction_correct=true` | POST | 记录信号结算结果 |

### 3.5 复盘接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/v1/signals/review` | GET | 历史表现摘要 |
| `/api/v1/signals/review/trigger` | POST | 手动触发周复盘 |
| `/api/v1/signals/settle` | POST | 手动触发结算 |

---

## 4. SSE 事件格式

### 4.1 实时推送（`/api/v1/signals/stream`）

```
GET /api/v1/signals/stream?tier=pro&interval=60&min_grade=A
```

**参数：**

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| tier | query | lite | 会员等级：lite / pro |
| interval | query | 60 | 扫描间隔（秒），范围 30-300 |
| min_grade | query | A | 最低推送等级：S / A / B |

**事件类型：**

#### 连接确认

```
event: message
data: {"type": "connected", "tier": "pro", "interval": 60}
```

#### 信号卡推送（核心事件）

```
event: signal_card
data: {
  "type": "signal_card",
  "tier": "pro",
  "card": {
    "coin": "BTC",
    "direction": "long",
    "grade": "S",
    "confidence": 78,
    "current_price": 67500,
    "entry_zone": [67200, 67800],
    "stop_loss": 65100,
    "take_profit": 70500,
    "risk_reward": 2.2,
    "position_pct": 8,
    "invalidation": 65100,
    "sources": [
      {"name": "bigorder_anomaly", "score": 72, "direction": "long", "detail": "Binance强信号"},
      {"name": "quantitative", "score": 65, "direction": "long", "detail": "综合评分65"},
      {"name": "technical", "score": 55, "direction": "long", "detail": "EMA多头排列+ADX=32"}
    ]
  },
  "math": {
    "hurst": 0.63,
    "hurst_interp": "H=0.63 趋势持续性强",
    "predictability": 0.42,
    "kelly": 0.125,
    "mc_bull_prob": 0.78,
    "mc_bear_prob": 0.22,
    "mc_var95": -5.2,
    "vol_regime": "normal",
    "vol_percentile": 35,
    "market_regime": "trending_up",
    "findings": [
      "H=0.63 趋势持续性强，支撑做多",
      "MC上涨概率78%，方向确认"
    ]
  },
  "backtest": {
    "win_rate": 68,
    "sample_count": 45,
    "sharpe": 1.8
  },
  "strategy": {
    "version": 3,
    "regime": "trending_up",
    "global_win_rate": 0.58
  }
}
```

#### 心跳

```
event: heartbeat
data: {"ts": 1717200000}
```

### 4.2 对话中的信号卡事件

当用户在聊天中问"BTC能买吗"时，`/chat/stream` 或 `/analyze/stream` 会返回：

```
event: message
data: {"type": "signal_card", "data": { ... 同上结构 ... }}
```

前端根据 `type === "signal_card"` 渲染卡片组件，而非普通文本气泡。

**对话中的完整 SSE 流程：**

```
1. event: message  → {"data": "", "type": "start"}         // 开始
2. event: message  → {"type": "signal_card", "data": {...}} // 信号卡
3. event: message  → {"type": "suggestions", ...}           // 推荐问题
4. event: message  → {"data": "", "type": "complete"}       // 完成
```

**无信号时降级为文字：**

```
1. event: message  → {"data": "", "type": "start"}
2. event: message  → {"data": "当前 BTC 信号不足...", "type": "chunk"}
3. event: message  → {"type": "suggestions", ...}
4. event: message  → {"data": "", "type": "complete"}
```

---

## 5. 后验验证与周期复盘

### 5.1 完整闭环

```
时间线：
────────────────────────────────────────────────────────→

T+0        T+10min       T+1h~24h          T+7天
 │            │             │                │
 │ 生成信号卡  │ 结算扫描     │ 结算完成         │ 周期复盘
 │ 存库        │ (定时任务)   │ (真实K线判断)    │ (定时任务)
 │ status=    │ status=     │ status=         │
 │ pending     │ pending     │ hit_tp/         │ 汇总统计
 │             │             │ hit_sl/         │ 策略演化
 │             │             │ expired         │ 版本+1
```

### 5.2 结算逻辑

后台每 **10 分钟**执行一次 `_signal_settlement_task()`：

```
1. 查所有 pending 且生成超过 1 小时的信号卡
2. 对每张卡：
   a. 拉小时K线（created_at ~ +24h）
   b. 逐根K线判断：
      - 多头：K线high ≥ TP → hit_tp（止盈）
      - 多头：K线low ≤ SL → hit_sl（止损）
      - 空头：反向判断
   c. 24h 内都未触达 → expired
3. 更新 settled_price、pnl_pct、settled_at
4. 只记录结果，不调整策略
```

**结算示例：**

```
信号卡：BTC做多 | 生成价67,500 | TP=70,500 | SL=65,100

4h后K线：high=68,800, low=66,200 → 未触达，继续
8h后K线：high=69,500, low=67,100 → 未触达，继续
12h后K线：high=71,200, low=68,500 → high≥TP！

→ status = hit_tp
→ settled_price = 70,500
→ pnl_pct = (70500-67500)/67500 × 100 = +4.44%
```

### 5.3 周期复盘逻辑

每周日凌晨 3 点执行 `_weekly_review_task()`：

```
1. 查过去 7 天所有已结算的信号卡
2. 汇总统计：
   - 总体胜率 = hit_tp / (hit_tp + hit_sl)
   - 按等级分组胜率（S/A/B 各自表现）
   - 按方向分组胜率（做多/做空各自表现）
   - 按信号源分组胜率（大单/量化/技术各自表现）
   - 夏普比率
3. 批量喂给自适应引擎（batch=True，只记录不调权）
4. 调用 evolve() 统一调整权重
5. 策略版本 +1
```

**复盘报告示例：**

```json
{
  "period": "7d",
  "total_cards": 28,
  "hit_tp": 18,
  "hit_sl": 6,
  "expired": 4,
  "win_rate": 75.0,
  "avg_pnl_pct": 2.8,
  "sharpe_ratio": 1.9,
  "by_grade": {
    "S": {"total": 8, "win_rate": 87.5, "avg_pnl": 4.2},
    "A": {"total": 15, "win_rate": 71.4, "avg_pnl": 2.5},
    "B": {"total": 5, "win_rate": 50.0, "avg_pnl": 0.3}
  },
  "by_direction": {
    "long": {"total": 18, "win_rate": 78.6, "avg_pnl": 3.1},
    "short": {"total": 10, "win_rate": 66.7, "avg_pnl": 2.0}
  },
  "by_source": {
    "quantitative": {"total": 28, "win_rate": 82.1, "avg_pnl": 3.5},
    "bigorder_anomaly": {"total": 28, "win_rate": 71.4, "avg_pnl": 2.8},
    "technical": {"total": 28, "win_rate": 60.7, "avg_pnl": 1.5}
  },
  "strategy_evolution": {
    "version_before": 5,
    "version_after": 6,
    "actions": ["因子technical退化（近期胜率53% < 整体61%），权重 0.280 → 0.238"],
    "current_weights": {"bigorder_anomaly": 0.36, "quantitative": 0.40, "technical": 0.24}
  }
}
```

### 5.4 自适应权重更新规则

复盘时 `evolve()` 的调整逻辑：

```
对每个信号源：
  若 近期胜率 < 整体胜率 - 10%  → 退化检测 → 权重 × 0.85
  若 近期胜率 > 整体胜率 + 10%  → 优异检测 → 权重 × 1.10

归一化：所有权重重新归一化到总和 = 1.0
```

---

## 6. 前端集成指南

### 6.1 实时推送接入

```javascript
// Pro 用户建立 SSE 连接
const eventSource = new EventSource(
  '/api/v1/signals/stream?tier=pro&interval=60&min_grade=A'
);

// 信号卡事件
eventSource.addEventListener('signal_card', (event) => {
  const data = JSON.parse(event.data);
  renderSignalCard(data);
});

// 心跳（用于连接保活检测）
eventSource.addEventListener('heartbeat', (event) => {
  updateLastHeartbeat();
});

// 连接状态
eventSource.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'connected') {
    console.log('Signal card stream connected', data);
  }
});

// 错误重连
eventSource.onerror = () => {
  setTimeout(() => {
    // 自动重连逻辑
  }, 5000);
};
```

### 6.2 对话中信号卡渲染

```javascript
// 监听 chat/stream 的 SSE 事件
eventSource.addEventListener('message', (event) => {
  const data = JSON.parse(event.data);

  if (data.type === 'signal_card') {
    // 渲染信号卡组件（而非普通文本气泡）
    renderSignalCardComponent(data.data);
    return;
  }

  if (data.type === 'chunk') {
    // 普通文本流
    appendText(data.data);
    return;
  }

  if (data.type === 'suggestions') {
    // 推荐问题按钮
    renderSuggestions(data.suggestions);
    return;
  }
});
```

### 6.3 前端卡片组件数据结构

```typescript
// 信号卡组件 Props
interface SignalCardProps {
  // 基础信息
  coin: string;              // "BTC"
  direction: "long" | "short";
  grade: "S" | "A" | "B";
  confidence: number;        // 0-100

  // 价格
  current_price: number;
  entry_zone: [number, number]; // [67200, 67800]
  stop_loss: number;
  take_profit: number;
  risk_reward: number;
  position_pct: number;

  // 信号源
  sources: Array<{
    name: "bigorder_anomaly" | "quantitative" | "technical";
    score: number;           // 0-100
    direction: "long" | "short" | "neutral";
    detail: string;
  }>;

  // 数学推导（Pro 专属，Lite 只有部分字段）
  math?: {
    hurst?: number;
    mc_bull_prob?: number;
    mc_bear_prob?: number;
    mc_var95?: number;        // VaR 95%
    kelly?: number;            // Kelly仓位
    market_regime?: string;
    vol_regime?: string;
    findings?: string[];
  };

  // 回测数据
  backtest?: {
    win_rate: number;
    sample_count: number;
    sharpe?: number;
  };

  // 策略信息
  strategy?: {
    version: number;
    regime: string;
    global_win_rate: number;
  };
}
```

### 6.4 建议的卡片 UI 布局

```
┌──────────────────────────────────────────────────┐
│ 🔴 S级信号 │ BTC │ 做多 │ 置信度 78%             │
├──────────────────────────────────────────────────┤
│ $67,500                                          │
│ ──────────●────────────────────── $70,500 TP     │
│          进场区间                   $65,100 SL     │
│ 盈亏比 2.2:1 │ Kelly仓位 12.5%                    │
├──────────────────────────────────────────────────┤
│ 信号源                                           │
│ 大单异动(72) ████████░░ 偏多                      │
│ 量化六因子(65) ███████░░░ 偏多                     │
│ 技术分析(55) █████░░░░░ 偏多                      │
├──────────────────────────────────────────────────┤
│ 📐 数学推导                                      │
│ Hurst=0.63 │ MC看涨78% │ 波动率P35                │
│ · 趋势持续性强，支撑做多                           │
│ · MC上涨概率78%，方向确认                          │
├──────────────────────────────────────────────────┤
│ 📈 回测验证 │ 胜率68%(n=45) │ 夏普1.8             │
│ 策略v3 │ trending_up │ 全局胜率58%                │
└──────────────────────────────────────────────────┘
```

### 6.5 信号卡去重

SSE 推送已做服务端去重（同币种同方向同等级，5 分钟内不重复推送），前端无需额外处理。

---

## 7. 会员等级差异

### 7.1 SSE 推送

| 特性 | Lite | Pro |
|------|------|-----|
| 推送等级 | A 级及以上 | S/A/B 全部 |
| 扫描间隔 | 120 秒 | 60 秒 |
| 数学推导 | hurst + mc_bull_prob + regime 仅 3 字段 | 全部 11 字段 |
| 回测数据 | 无 | win_rate + sharpe |
| 策略信息 | 无 | 版本 + regime + 全局胜率 |
| Kelly 仓位 | 不含 | 包含 |
| 蒙特卡洛 VaR | 不含 | 包含 |

### 7.2 对话中生成

| 特性 | Lite | Pro |
|------|------|-----|
| 触发方式 | 问"BTC能买吗" | 同左 |
| 卡片等级 | A/B 级 | S/A/B 全部 |
| 数学推导 | 精简版 | 完整版 |
| 推荐问题 | 有 | 有 |

### 7.3 SSE 事件字段对比

```json
// Lite 用户收到的事件
{
  "type": "signal_card",
  "tier": "lite",
  "card": { /* 完整卡片数据 */ },
  "math": {
    "hurst": 0.63,
    "mc_bull_prob": 0.78,
    "market_regime": "trending_up"
  }
  // 无 backtest, 无 strategy
}

// Pro 用户收到的事件
{
  "type": "signal_card",
  "tier": "pro",
  "card": { /* 完整卡片数据 */ },
  "math": {
    "hurst": 0.63,
    "hurst_interp": "H=0.63 趋势持续性强",
    "predictability": 0.42,
    "kelly": 0.125,
    "mc_bull_prob": 0.78,
    "mc_bear_prob": 0.22,
    "mc_var95": -5.2,
    "vol_regime": "normal",
    "vol_percentile": 35,
    "market_regime": "trending_up",
    "findings": ["H=0.63 趋势持续性强", "MC看涨78%"]
  },
  "backtest": {"win_rate": 68, "sample_count": 45, "sharpe": 1.8},
  "strategy": {"version": 3, "regime": "trending_up", "global_win_rate": 0.58}
}
```

---

## 8. 后端部署指南

### 8.1 数据库迁移

在 MySQL 中执行建表语句：

```bash
mysql -u root -p exchange < sql/signal_card_history.sql
```

或直接在 MySQL 客户端执行 `sql/signal_card_history.sql`。

### 8.2 无需额外依赖

所有新增功能使用纯 Python 实现，无需安装新依赖：
- 数学推导：`math` + `random`（标准库）
- 策略持久化：JSON 文件（`app/signals/strategy_state.json`）
- 结算/复盘：使用现有的 `pymysql` + `data_service`

### 8.3 后台任务自动启动

信号卡后台任务随 FastAPI 应用自动启动（不依赖 Redis）：

| 任务 | 间隔 | 说明 |
|------|------|------|
| `_signal_settlement_task` | 10 分钟 | 结算 pending 信号卡 |
| `_weekly_review_task` | 每周日 03:00 | 周期复盘 + 策略演化 |

启动日志确认：

```
Signal Cards: 后台结算(10min) + 周期复盘(每周日) 已启动
Signal Cards: 下次复盘时间 2026-06-07 03:00（151.2h 后）
```

### 8.4 环境变量

无新增环境变量。使用现有的 MySQL 配置：

```
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=xxx
MYSQL_DATABASE=exchange
```

### 8.5 验证部署

```bash
# 1. 健康检查
curl http://localhost:8000/

# 2. 生成信号卡
curl http://localhost:8000/api/v1/signals/generate/BTC

# 3. 扫描热门币
curl http://localhost:8000/api/v1/signals/scan?limit=5

# 4. 手动结算
curl -X POST http://localhost:8000/api/v1/signals/settle

# 5. 策略性能
curl http://localhost:8000/api/v1/signals/strategy/performance

# 6. 手动复盘
curl -X POST http://localhost:8000/api/v1/signals/review/trigger

# 7. SSE 推送（浏览器打开）
# http://localhost:8000/api/v1/signals/stream?tier=pro&interval=60&min_grade=A
```

---

## 9. 数据表结构

### signal_card_history

```sql
CREATE TABLE signal_card_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    coin VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,       -- long/short
    grade VARCHAR(5) NOT NULL,            -- S/A/B
    entry_low DECIMAL(20,4),
    entry_high DECIMAL(20,4),
    stop_loss DECIMAL(20,4) NOT NULL,
    take_profit DECIMAL(20,4) NOT NULL,
    current_price DECIMAL(20,4) NOT NULL,
    invalidation_price DECIMAL(20,4),
    confidence DECIMAL(5,2),
    risk_reward_ratio DECIMAL(5,2),
    position_pct DECIMAL(5,2),
    sources_json TEXT,                    -- JSON: 信号源明细
    math_json TEXT,                       -- JSON: 数学推导摘要
    strategy_version INT DEFAULT 1,
    regime VARCHAR(20),
    adaptive_weights_json TEXT,           -- JSON: 权重快照
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    settled_price DECIMAL(20,4),
    settled_at TIMESTAMP NULL,
    pnl_pct DECIMAL(8,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_status (status),
    INDEX idx_coin_status (coin, status),
    INDEX idx_created_at (created_at),
    INDEX idx_settled_at (settled_at)
);
```

**状态流转：**

```
pending → hit_tp     (止盈触发)
        → hit_sl     (止损触发)
        → expired    (24h 未触达)
```

---

## 10. 配置参数说明

### 策略引擎参数（`app/signals/adaptive_strategy.py`）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `BAYESIAN_PRIOR_ALPHA` | 10.0 | 贝叶斯先验伪计数（越大变化越慢） |
| `BAYESIAN_PRIOR_BETA` | 10.0 | 贝叶斯先验伪计数 |
| `LEARNING_RATE` | 0.15 | 每次权重调整幅度 |
| `DECAY_HALFLIFE_DAYS` | 30 | 胜率衰减半衰期（天） |
| `MIN_OBSERVATIONS` | 5 | 最少观测数才更新权重 |

### 结算参数（`app/signals/settlement.py`）

| 参数 | 值 | 说明 |
|------|------|------|
| 结算间隔 | 10 分钟 | 后台任务扫描周期 |
| 最小等待 | 1 小时 | 生成后至少等1小时才结算 |
| 有效期 | 24 小时 | 超过24h未触达则 expired |

### 复盘参数（`app/signals/review.py`）

| 参数 | 值 | 说明 |
|------|------|------|
| 复盘周期 | 7 天 | 每周一次 |
| 复盘时间 | 周日 03:00 | 凌晨低峰期 |

---

## 11. 迭代与扩展

### 11.1 文件结构总览

```
app/signals/
├── __init__.py
├── models.py              # 数据模型（SignalCard/MathDerivationSummary/StrategyMeta）
├── math_engine.py         # 第一性原理数学推导引擎
├── adaptive_strategy.py   # 自适应策略引擎
├── fusion.py              # 三源融合引擎（大单/量化/技术 + 自适应权重）
├── chat.py                # Signal Chat 独立 SSE 端点（Function Calling）
│   ├── POST /signals/v1/chat  # 主端点
│   ├── _execute_tool()         # 同步工具执行
│   ├── _tool_analyze_coin()    # 分析币种 → 信号卡
│   ├── _tool_query_winrate()   # 历史胜率
│   ├── _tool_query_strategy()  # 策略报告
│   ├── _tool_query_scan_results() # 扫描结果
│   ├── _detect_language()      # 中英文检测
│   └── _get_suggestions()      # 推荐追问生成
├── alpha_scanner.py       # 全市场 Alpha 雷达扫描器
├── endpoints.py           # REST 端点
├── settlement.py          # 结算引擎（真实K线后验 + 扫描缓存）
├── backtest.py            # 回测引擎
└── review.py              # 周期复盘引擎
```

### 11.2 扩展方向

**新增信号源：**
1. 在 `fusion.py` 中新增 `_xxx_source(ohlcv, weight)` 函数
2. 在 `DEFAULT_WEIGHTS` 和 `REGIME_PRESETS` 中添加权重配置
3. 在 `fuse_signals()` 中调用并加入 sources 列表

**新增数学推导指标：**
1. 在 `math_engine.py` 中新增计算函数
2. 在 `MathDerivation` 中新增字段
3. 在 `run_math_derivation()` 中调用
4. 在 `models.py` 的 `MathDerivationSummary` 中新增字段

**调整复盘周期：**
- 修改 `main.py` 中 `_weekly_review_task()` 的计算逻辑
- 或改为可配置参数（通过 `settings.py`）

**调整结算间隔：**
- 修改 `main.py` 中 `_signal_settlement_task()` 的 `asyncio.sleep(600)`

### 11.3 注意事项

- `strategy_state.json` 是策略引擎的持久化文件，不要删除，否则权重回到默认值
- 结算任务依赖小时 K 线数据（`kline_type=1`），确保 API 可用
- 复盘会在凌晨 3 点触发，会调用 LLM 做市场状态检测，确保 API Key 有效
- 所有新增代码都不影响已有的行情问答（`/chat/stream`）和大单侦测（`/bigorder/v1/`）
