# 指令路由接口文档 — 前后端对接指南

> 最后更新：2026-06-24 | 版本：v1.0
>
> 适用于：`POST /api/v1/route`

---

## 一、接口总览

前端有 6 个指令入口（`/price` `/ai` `/chat` `/bigorder` `/predict` `/alert`），各自对应不同的后端接口。用户输入的是自然语言，前端需要先调本路由端点拿到指令名，再去调对应接口。

```
用户输入 "BTC现在多少钱"
  │
  ▼
POST /api/v1/route             ← 本接口
  │
  ▼
返回 {command: "/price", coin_symbol: "BTC", ...}
  │
  ▼
前端按 command 分流
  ├─ /price    → 调价格接口（传 coin_symbol=BTC）
  ├─ /ai       → 调 POST /api/v1/analyze/stream（深度分析，SSE）
  ├─ /chat     → 调 POST /api/v1/chat/stream（闲聊，SSE）
  ├─ /bigorder → 调 /bigorder/v1/coin/BTC/signal（大单侦测）
  ├─ /predict  → 调预测玩法接口（待前端定义）
  └─ /alert    → 调报警添加接口（待前端定义）
```

**不做的**：
- 不流式 — 单次 JSON 响应，平均延迟 500-1500ms（DeepSeek 调用）
- 不替前端调下游接口 — 路由只负责"识别意图 + 返回指令"，下游接口由前端按需触发
- 不替代 LLM 的 `IntentAnalyzer` — 那是 `crypto_agent` 内部用的 9 分类，粒度不同，本接口是独立的 6 分类

---

## 二、接口定义

### 基本信息

```
POST /api/v1/route
Content-Type: application/json
响应格式：JSON（非 SSE）
超时建议：前端设 12s（后端 LLM 超时 10s + 网络余量）
```

### 请求体

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 用户问题，1-1000 字符。也接受字段名 `message`（Pydantic alias 兼容） |
| `conversation_id` | string | 否 | 会话 ID，预留上下文使用，当前版本未启用 |

**请求示例**：

```bash
curl -X POST 'http://127.0.0.1:8000/api/v1/route' \
  -H 'Content-Type: application/json' \
  -d '{"question": "BTC现在多少钱"}'
```

```json
{
  "question": "分析一下ETH后市怎么走"
}
```

### 响应体

| 字段 | 类型 | 必返回 | 说明 |
|------|------|--------|------|
| `command` | string \| null | 是 | 对应指令，如 `/price`。**LLM 失败时为 `null`**，前端读 `fallback_text` |
| `coin_symbol` | string \| null | 是 | 从问题中提取的币种符号（大写）。多币种问题可能返回 `"BTC,ETH"`，闲聊/无关问题时为空串或 null |
| `confidence` | float | 是 | 置信度 0-1，一般 ≥0.85。低于 0.7 时建议前端弹确认 |
| `reason` | string | 是 | 判定理由，调试用，可忽略 |
| `language` | string | 是 | `zh` 或 `en`，前端可据此切换 UI 语言 |
| `fallback_text` | string \| null | 是 | 仅当 `command=null` 时非空。**前端应直接渲染为一条助手消息**，内容是能力介绍 |

---

## 三、6 个指令的判定规则

LLM 按以下优先级判定（从上到下，匹配即停）：

| 优先级 | 触发关键词 | 返回指令 | 典型问题 |
|--------|-----------|----------|---------|
| 1 | 监控 / 报警 / 提醒 / 通知 / 跌破X提醒我 | `/alert` | "BTC跌破6万提醒我"、"把PEPE加入监控" |
| 2 | 猜 / 赌 / 预测 / 下注 / 看涨看跌 | `/predict` | "我猜SOL明天涨"、"赌一手BTC跌" |
| 3 | 大单 / 主力 / 异动 / 资金流向 | `/bigorder` | "BTC最近有大单异动吗"、"ETH主力资金流向" |
| 4 | 多少钱 / 价格 / 涨跌幅 / 市值（纯数据） | `/price` | "BTC现在多少钱"、"ETH涨了多少" |
| 5 | 涉及币种 + 要求分析/走势/建议 | `/ai` | "分析一下ETH后市"、"BNB怎么样"、"BTC能买吗" |
| 6 | 其他（闲聊/问候/无关） | `/chat` | "你好"、"今天天气怎么样" |

**关键约束**：
- 优先级 4（`/price`）只匹配"纯数据查询"。如果问"BTC现在能买吗"，虽然提到价格隐含意义，但属于分析建议，会被优先级 5 接住为 `/ai`
- 多币种问题（"BTC和ETH哪个好"）默认走 `/ai`，`coin_symbol` 返回 `"BTC,ETH"`
- 完全无关问题（天气、问候、闲聊）一律走 `/chat`

---

## 四、前端集成代码示例

### TypeScript 示例

```typescript
interface RouteResponse {
  command: string | null;
  coin_symbol: string | null;
  confidence: number;
  reason: string;
  language: 'zh' | 'en';
  fallback_text: string | null;
}

async function routeUserQuestion(question: string): Promise<RouteResponse> {
  const resp = await fetch('/api/v1/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
    signal: AbortSignal.timeout(12000),  // 12s 超时
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return await resp.json();
}

async function handleUserInput(input: string) {
  const route = await routeUserQuestion(input);

  // 情况 1：LLM 失败，渲染兜底文案为助手消息
  if (route.command === null) {
    appendAssistantMessage(route.fallback_text ?? '服务暂时不可用');
    return;
  }

  // 情况 2：置信度低，弹确认（可选）
  if (route.confidence < 0.7) {
    const ok = await confirm(`你是想${commandLabel(route.command)}吗？`);
    if (!ok) return;
  }

  // 情况 3：按 command 分流到下游接口
  switch (route.command) {
    case '/price':
      return fetchPrice(route.coin_symbol);              // GET /price 接口
    case '/ai':
      return streamAnalyze(input, route.coin_symbol);    // POST /api/v1/analyze/stream
    case '/chat':
      return streamChat(input);                          // POST /api/v1/chat/stream
    case '/bigorder':
      return fetchBigorderSignal(route.coin_symbol);     // GET /bigorder/v1/coin/{coin}/signal
    case '/predict':
      return openPredictUI(route.coin_symbol);           // 打开预测玩法面板
    case '/alert':
      return openAlertDialog(route.coin_symbol);         // 打开报警添加弹窗
  }
}

function commandLabel(cmd: string): string {
  return {
    '/price': '查价格',
    '/ai': '深度分析',
    '/chat': '闲聊',
    '/bigorder': '看大单',
    '/predict': '玩预测',
    '/alert': '设监控',
  }[cmd] ?? cmd;
}
```

### 处理多币种问题

```typescript
const coins = (route.coin_symbol ?? '').split(',').filter(Boolean);
if (coins.length > 1) {
  // 例如 "BTC,ETH" → 让用户选一个再调下游
  return askUserToPickOne(coins);
}
```

---

## 五、响应示例

### 成功 — 价格查询

```json
{
  "command": "/price",
  "coin_symbol": "BTC",
  "confidence": 0.95,
  "reason": "用户询问BTC当前价格",
  "language": "zh",
  "fallback_text": null
}
```

### 成功 — 英文输入

```json
{
  "command": "/price",
  "coin_symbol": "ETH",
  "confidence": 0.98,
  "reason": "User asks for the price of Ethereum",
  "language": "en",
  "fallback_text": null
}
```

### 成功 — 报警添加（小写币种自动大写）

请求：`{"question": "帮我把pepe加入监控"}`

```json
{
  "command": "/alert",
  "coin_symbol": "PEPE",
  "confidence": 0.98,
  "reason": "用户要求将pepe加入监控",
  "language": "zh",
  "fallback_text": null
}
```

### 成功 — 多币种（`/ai` 接住）

请求：`{"question": "btc和eth哪个更适合长期持有"}`

```json
{
  "command": "/ai",
  "coin_symbol": "BTC,ETH",
  "confidence": 0.9,
  "reason": "用户询问BTC和ETH哪个更适合长期持有，属于深度分析和投资建议",
  "language": "zh",
  "fallback_text": null
}
```

### 成功 — 闲聊（无币种）

```json
{
  "command": "/chat",
  "coin_symbol": "",
  "confidence": 0.99,
  "reason": "用户询问天气，与加密货币无关",
  "language": "zh",
  "fallback_text": null
}
```

> ⚠️ `coin_symbol` 可能是空串 `""` 或 `null`，前端判空要用 `if (!coin_symbol)` 而非 `if (coin_symbol === null)`。

### 失败 — LLM 超时或错误（兜底）

```json
{
  "command": null,
  "coin_symbol": null,
  "confidence": 0.0,
  "reason": "APITimeoutError: Request timed out",
  "language": "zh",
  "fallback_text": "我是加密货币分析助手，可以帮你：\n💰 /price — 查询价格、涨跌幅\n🤖 /ai — 深度分析（趋势/技术面/信号）\n💬 /chat — 闲聊\n📊 /bigorder — 大单侦测、主力资金\n🎯 /predict — 预测涨跌玩法\n🔔 /alert — 添加监控报警\n\n试试问我：\"BTC现在多少钱\" 或 \"分析一下ETH\""
}
```

**前端处理**：`command === null` 时，把 `fallback_text` 当作一条助手消息直接渲染到对话框，用户重新输入即可。

---

## 六、边界情况

| 情况 | 行为 |
|------|------|
| 问题为空串 | Pydantic 422（`min_length=1`） |
| 问题 >1000 字符 | Pydantic 422（`max_length=1000`） |
| LLM 超时（>10s） | 返回 `command=null` + `fallback_text` |
| LLM 返回未知指令 | 返回 `command=null` + `fallback_text`，后端日志记录 warning |
| LLM 返回非 JSON | 后端走截断补全解析；仍失败则返回 `command=null` + `fallback_text` |
| 多币种问题 | `command` 正常返回（通常 `/ai`），`coin_symbol` 为逗号分隔（"BTC,ETH"） |
| 无币种闲聊 | `command=/chat`，`coin_symbol` 为 `""` 或 `null` |
| 英文输入 | `language=en`，其他字段正常 |

---

## 七、性能与限制

- **延迟**：单次 500-1500ms（DeepSeek 调用），P95 < 2s
- **超时**：后端硬超时 10s，建议前端 12s
- **并发**：走共享 LLM 客户端连接池，无独立限流。若 QPS > 50 联系后端加 semaphore
- **不计入会话上下文**：当前版本 `conversation_id` 仅占位，未来可能用于"用户上次问ETH，这次问'技术面怎么样' → 自动补全 ETH"

---

## 八、变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0 | 2026-06-24 | 首版。6 指令分类 + 兜底文案 |
