# SSE 协议规范 — 后端统一流式协议

> 最后更新：2026-06-15 | 版本：v1.1
>
> 适用于：`/api/v1/chat/stream`、`/api/v1/analyze/stream`、`/bigorder/v1/chat`、`/signals/v1/chat`
>
> **v1.1 变更**：所有 chat 端点不再接受 `coin`/`symbol` 入参。币种一律由 LLM 从用户消息抽取，或通过 `conversation_id` 历史记忆。前端继续传 `coin`/`symbol` 会被 Pydantic 静默忽略，不会 422。

---

## 一、设计目标

四个 Chat/Stream 端点共用一套 SSE 帧协议，保证：

1. **每帧透传 `request_id`** — 客户端能在任何帧上关联请求
2. **event + data_type 双字段路由** — 前端可按 event 事件名或 data_type 类型分发
3. **统一错误帧** — 出错也走 SSE，带 code + message，前端不需要监听 HTTP 状态
4. **结构化事件分离** — 文字流（chat）、信号卡（signal_card）、推荐追问（suggestions）、调试（tool_debug）互不干扰

---

## 二、帧结构

每帧是一个 JSON 对象，通过 SSE 标准的 `event:` / `data:` 两个字段传输：

```
event: <event_name>
data: <json_string>
```

`data` 字段的 JSON 始终包含以下公共字段：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| event | string | 是 | 事件名：`start` / `delta` / `done` / `error` |
| data_type | string | 是 | 数据类型：`meta` / `chat` / `signal_card` / `suggestions` / `tool_debug` |
| request_id | string | 是 | 客户端传入，全程透传 |

业务字段（`delta` / `payload` / `code` 等）按 data_type 不同附加在公共字段后。

---

## 三、event 与 data_type 枚举

| event | data_type | 用途 | 附加字段 |
|------|-----------|------|---------|
| `start` | `meta` | 流开始，携带 `conversation_id`（如有） | `conversation_id?: string` |
| `delta` | `chat` | 文字片段（LLM token 流） | `delta: string` |
| `delta` | `signal_card` | 推送信号卡（完整 payload） | `payload: object` |
| `delta` | `suggestions` | 推送推荐追问列表 | `payload: string[]` |
| `delta` | `tool_debug` | 工具调用过程状态 | `stage: "thinking"\|"tool_call"\|"tool_result"`, `payload: object` |
| `done` | `meta` | 流正常结束 | 无 |
| `error` | `meta` | 出错 | `code: int`, `message: string` |

---

## 四、各事件帧示例

### 4.1 start（流开始）

```
event: start
data: {"event":"start","data_type":"meta","request_id":"req_abc","conversation_id":"conv_123"}
```

> `conversation_id` 缺省时该字段不出现。

### 4.2 delta / chat（文字增量）

```
event: delta
data: {"event":"delta","data_type":"chat","request_id":"req_abc","delta":"BTC 当前"}
```

> 前端应将连续的 `delta` 拼接为完整文本。

### 4.3 delta / signal_card（信号卡）

```
event: delta
data: {"event":"delta","data_type":"signal_card","request_id":"req_abc","payload":{...信号卡完整对象...}}
```

`payload` 结构由信号卡业务决定，详见 `docs/signal-card-api.md`。

### 4.4 delta / suggestions（推荐追问）

```
event: delta
data: {"event":"delta","data_type":"suggestions","request_id":"req_abc","payload":["BTC 历史胜率","最新信号","策略表现"]}
```

### 4.5 delta / tool_debug（工具调用过程）

仅 `/bigorder/v1/chat` 和 `/signals/v1/chat` 会产生这类帧（Function Calling 端点）。

```
event: delta
data: {"event":"delta","data_type":"tool_debug","request_id":"req_abc","stage":"thinking","payload":{"status":"Analyzing..."}}

event: delta
data: {"event":"delta","data_type":"tool_debug","request_id":"req_abc","stage":"tool_call","payload":{"tool":"analyze_coin","args":{"coin":"BTC"}}}

event: delta
data: {"event":"delta","data_type":"tool_debug","request_id":"req_abc","stage":"tool_result","payload":{"tool":"analyze_coin"}}
```

### 4.6 done（流结束）

```
event: done
data: {"event":"done","data_type":"meta","request_id":"req_abc"}
```

### 4.7 error（出错）

```
event: error
data: {"event":"error","data_type":"meta","request_id":"req_abc","code":5001,"message":"LLM timeout"}
```

---

## 五、完整事件流样例

### 5.1 普通对话端点（`/api/v1/chat/stream`、`/api/v1/analyze/stream`）

```
event: start       → data: {... meta, request_id, conversation_id}
event: delta       → data: {... chat, delta: "根据链上数据..."}
event: delta       → data: {... chat, delta: "BTC 当前..."}
event: delta       → data: {... signal_card, payload: {...}}      ← 如果 agent 生成信号卡
event: delta       → data: {... suggestions, payload: [...]}}     ← 如果有推荐问题
event: done        → data: {... meta}
```

### 5.2 Function Calling 端点（`/bigorder/v1/chat`、`/signals/v1/chat`）

```
event: start       → meta
event: delta       → tool_debug (stage=thinking)
event: delta       → tool_debug (stage=tool_call)
event: delta       → signal_card                                 ← 仅 analyze_coin 工具会产生
event: delta       → tool_debug (stage=tool_result)
event: delta       → chat, delta: "根据数据..."
event: delta       → chat, delta: "...连续输出"
event: delta       → suggestions
event: done        → meta
```

### 5.3 LLM 未调用工具（直接回答）

```
event: start       → meta
event: delta       → chat, delta: "..."
event: done        → meta
```

### 5.4 出错（流中途 LLM 超时）

```
event: start       → meta
event: delta       → tool_debug (stage=thinking)
event: error       → meta, code=5001, message="LLM timeout"
```

---

## 六、错误码

| 错误码 | 含义 | 触发场景 |
|-------|------|---------|
| 5001 | `ERR_LLM_TIMEOUT` | LLM 第一次路由调用超时（30s） |
| 5002 | `ERR_TOOL_TIMEOUT` | 工具执行超时 |
| 5003 | `ERR_INTERNAL` | 业务异常 / 未捕获错误 |
| 5004 | `ERR_SERVICE_UNAVAILABLE` | 依赖服务不可用（如 Redis 不可连） |

定义位置：`app/utils/sse_protocol.py`

---

## 七、四个端点的入参 Schema

### 7.1 `POST /api/v1/chat/stream`（通用对话）

文件：`app/api/schemas.py → ChatRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request_id | string (≤100) | 是 | 客户端生成，全程透传 |
| user_id | string (≤100) | 是 | 用户 ID |
| message | string (2-1000) | 是 | 用户消息 |
| conversation_id | string (≤100) | 否 | 会话 ID |
| lang | `"zh"` / `"en"` | 否，默认 zh | 语言 |

### 7.2 `POST /api/v1/analyze/stream`（深度分析）

文件：`app/api/schemas.py → AnalyzeRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request_id | string (≤100) | 是 | 同上 |
| user_id | string (≤100) | 是 | 用户 ID |
| message | string (2-1000) | 是 | 用户消息；币种由 LLM 从中抽取。旧字段名 `question` 仍兼容（alias） |
| conversation_id | string (≤100) | 否 | 会话 ID；未在 message 中明示币种时回退到会话记忆 |
| lang | `"zh"` / `"en"` | 否，默认 zh | 语言 |

### 7.3 `POST /bigorder/v1/chat`（大单侦测）

文件：`app/bigorder/models.py → ChatRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request_id | string | 是 | 同上 |
| user_id | string | 是 | 用户 ID |
| message | string | 是 | 用户问题；币种由 LLM 从中抽取 |
| conversation_id | string | 否 | 会话 ID |

### 7.4 `POST /signals/v1/chat`（信号卡对话）

文件：`app/signals/chat.py → SignalChatRequest`

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| request_id | string (≤100) | 是 | 同上 |
| user_id | string (≤100) | 是 | 用户 ID |
| message | string | 是 | 用户问题；币种由 LLM 从中抽取 |
| conversation_id | string (≤100) | 否 | 会话 ID |

---

## 八、实现位置

| 模块 | 文件 | 职责 |
|------|------|------|
| 协议核心 | `app/utils/sse_protocol.py` | 帧构建函数 + `render()` + 错误码常量 |
| 通用 Chat | `app/api/endpoints.py → chat_stream` | `/api/v1/chat/stream` |
| 通用 Analyze | `app/api/endpoints.py → analyze_stream` | `/api/v1/analyze/stream` |
| BigOrder | `app/bigorder/chat.py → chat` | `/bigorder/v1/chat` |
| Signals | `app/signals/chat.py → chat` | `/signals/v1/chat` |

所有端点统一使用 `sse_start / sse_chat_delta / sse_signal_card / sse_suggestions / sse_tool_debug / sse_done / sse_error` 七个构建函数，再经 `render()` 转为 `EventSourceResponse` 兼容格式。

---

## 九、前端解析示例（JS）

```javascript
const es = new EventSource('/api/v1/chat/stream', { withCredentials: true });

es.addEventListener('start', e => {
  const frame = JSON.parse(e.data);
  console.log('stream start', frame.request_id, frame.conversation_id);
});

es.addEventListener('delta', e => {
  const frame = JSON.parse(e.data);
  switch (frame.data_type) {
    case 'chat':         appendText(frame.delta); break;
    case 'signal_card':  renderCard(frame.payload); break;
    case 'suggestions':  renderSuggestions(frame.payload); break;
    case 'tool_debug':   showDebug(frame.stage, frame.payload); break;
  }
});

es.addEventListener('done', e => es.close());

es.addEventListener('error', e => {
  if (e.data) {
    const frame = JSON.parse(e.data);
    showError(frame.code, frame.message);
  }
});
```

> 注意：浏览器原生 `EventSource` 只支持 GET。生产中前端通常用 `fetch + ReadableStream` 或 `@microsoft/fetch-event-source` 发起 POST 请求，事件名分发逻辑与上面一致。

---

## 十、迁移与兼容性

本次协议统一对旧版有 **Breaking Change**，前端必须同步升级：

| 维度 | 旧协议 | 新协议 |
|------|--------|-------|
| 事件名 | `content` / `thinking` / `tool_call` / `signal_card` / `suggestions` | 统一收敛到 `start` / `delta` / `done` / `error` |
| 数据分类 | event name 即类型 | event + data_type 双字段 |
| request_id 透传 | 不保证 | 每帧都带 |
| 错误处理 | HTTP 状态码 | SSE error 帧 + code |
| 必填字段 | 无 | `request_id` + `user_id` 必传 |

旧前端不升级会出现的现象：
1. 收不到内容（事件名不匹配）
2. 422 入参校验失败（缺 `request_id` / `user_id`）
3. 错误静默丢失（不监听 error event）

### v1.1 兼容性（2026-06-15）

| 维度 | v1.0 | v1.1 |
|------|------|------|
| `coin`/`symbol` 入参 | 可选，作为 LLM 兜底 | **移除**，统一由 LLM 抽取 |
| 前端继续传 `coin`/`symbol` | 正常工作 | 字段被静默忽略，不 422 |
| 前端不传 `coin`/`symbol` | 部分场景下 LLM 抽取，部分场景走兜底 | 全部走 LLM 抽取，缺币种时回退到 `conversation_id` 历史 |

升级路径：前端可以**先**清理掉 `coin`/`symbol` 入参（不需要等后端），后端 v1.1 上线后旧前端也继续工作。

---

## 十一、新增端点时的 Checklist

新接入 SSE 端点必须做到：

- [ ] 引入 `from app.utils.sse_protocol import ...`
- [ ] 第一帧 `yield render(sse_start(rid, conversation_id))`
- [ ] 最后一帧 `yield render(sse_done(rid))` 或 `sse_error`
- [ ] 所有中间帧携带 `request_id`
- [ ] 出错路径一律走 `sse_error`，不抛 HTTP 异常中断流
- [ ] Pydantic 请求模型声明 `request_id` 和 `user_id` 为必填
- [ ] 文档（本文件）补充该端点入参 Schema
