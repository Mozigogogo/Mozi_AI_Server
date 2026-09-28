# 深度思考流（thinking）前端接入契约

> 版本：v1（2026-09-27）| 后端已上线生产（askmozi.com，commit 64437a5）并实测验证
>
> 本文自包含，前端仓库无需后端代码上下文。协议全集见后端仓库 `docs/sse-protocol.md`（v1.2）。

## 1. 这是什么

深度思考模式（`/api/v1/analyze/stream`）下，模型（DeepSeek v4-flash）的推理过程会以 `data_type: "thinking"` 的 delta 帧实时流式下发，**全部位于正文 chat 帧之前**。体验对标 DeepSeek R1 / Kimi：35 秒的等待期用户能看到推理滚动，结束后收起为"已深度思考"。

- 只有 `/api/v1/analyze/stream` 出 thinking 帧；`/api/v1/chat/stream` 永远没有
- 旧前端忽略未知 `data_type` 即安全（无 Breaking Change）
- 服务端开关 `THINKING_STREAM_ENABLED` 可整体关闭（关闭 = 一条 thinking 帧都没有）

## 2. 帧格式

```
event: delta
data: {"event":"delta","data_type":"thinking","request_id":"req_abc","delta":"推理片段…"}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| event | string | 固定 `delta`（与 chat 同事件名，靠 data_type 分发） |
| data_type | string | `thinking` |
| request_id | string | 每帧透传 |
| delta | string | 推理文本增量，前端负责拼接 |

完整流（think 模式典型时序）：

```
event: start     → {"data_type":"meta","request_id":"...","conversation_id":"..."}
event: delta × N → {"data_type":"thinking","delta":"…"}     ← 思考流（N 可达数千）
event: delta × M → {"data_type":"chat","delta":"…"}         ← 正文
event: delta     → {"data_type":"signal_card","payload":{…}}   ← 如有（已有逻辑）
event: delta     → {"data_type":"suggestions","payload":[…]}   ← 如有（已有逻辑）
event: done      → {"data_type":"meta","request_id":"..."}
```

出错时任意时刻可到 `event: error`（`code` + `message`），错误码：5001 LLM超时 / 5002 工具超时 / 5003 内部错误 / 5004 服务不可用。

## 3. 可以依赖的时序保证

1. **thinking 帧全部先于第一条 chat 帧**，绝不全交错。生产实测：7364 条 thinking → 430 条 chat，零混入
2. 后端流式失败自动降级非流式时：先到**一整条大 thinking 帧**（完整推理一次性下发）再出正文——不要假设帧都很小
3. 模型未走推理时：0 条 thinking 帧，正文直接开始

## 4. 接入实现

在现有 SSE delta 分发里加一个 case，加两段状态逻辑：

```javascript
let thinkingBuf = '';
let answerStarted = false;
let flushTimer = null;
const THINKING_FLUSH_MS = 150;   // 关键：批量刷新，见第 5 节

function onDelta(frame) {
  switch (frame.data_type) {
    case 'thinking': {
      thinkingBuf += frame.delta;
      if (!flushTimer) {
        flushTimer = setTimeout(() => {
          flushTimer = null;
          renderThinking(thinkingBuf);        // 面板内容 + 自动滚底
        }, THINKING_FLUSH_MS);
      }
      break;
    }
    case 'chat': {
      if (!answerStarted) {
        answerStarted = true;
        clearTimeout(flushTimer);
        renderThinking(thinkingBuf);          // 最后一次 flush，别丢尾巴
        collapseThinkingPanel(`已深度思考（用时 ${elapsedSecs()}s）`);
      }
      appendAnswer(frame.delta);
      break;
    }
    // signal_card / suggestions / tool_debug：沿用现有逻辑，不动
  }
}
```

注意：端点是 POST，浏览器原生 `EventSource` 只支持 GET，继续用现有的 `fetch + ReadableStream` 或 `@microsoft/fetch-event-source` 方案，仅在上面的分发处加 case。

## 5. 性能——最大的坑

实测单次请求 thinking 可达 **7000+ 帧**（平均每帧 1-2 个字）。

- **绝不能每帧 setState / 重渲染**——会直接卡死页面
- 必须 buffer 累积 + 定时 flush（150-200ms 一次，或 rAF）；正文 chat 帧本身量级小（几百帧），沿用现有逻辑即可
- 自动滚底放在 flush 里做，不要每帧算 scrollTop

## 6. UI 形态（对标 DeepSeek R1 / Kimi）

| 阶段 | 展示 |
|------|------|
| thinking 到达中 | 灰色小字面板展开、自动滚底；标题"深度思考中…"可带计时器（脉冲动画） |
| 首条 chat 帧到达 | 面板收起为一行"已深度思考（用时 Ns）"，点击可重新展开回看 |
| 0 条 thinking | 不显示面板，直接正文 |
| error 帧 | 保留已收到的思考 + 正文，追加错误提示 |

## 7. 验收清单

- [ ] think 模式：思考面板滚动展示，正文出现时自动收起为"已深度思考（用时 Ns）"
- [ ] 首条 chat 帧到达时 thinking 尾部无丢失（最后一次 flush）
- [ ] 长思考（7000+ 帧）页面不卡顿（confirm flush 生效）
- [ ] chat 模式（/chat/stream）无面板出现
- [ ] thinking 缺失（开关关闭/模型未推理）时正常直接出正文
- [ ] error 帧到达时已渲染内容保留
- [ ] 收起后可点击重新展开完整思考内容
