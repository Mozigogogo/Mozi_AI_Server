"""统一 SSE 协议 — 所有 Chat/Stream 端点共用

帧结构:
  event     | data_type   | 用途
  ----------|-------------|------
  start     | meta        | 流开始，携带 conversation_id
  delta     | chat        | 文字片段，delta 字段携带
  delta     | signal_card | 信号卡，payload 字段携带
  delta     | suggestions | 推荐追问，payload 字段携带
  delta     | tool_debug  | 工具中间状态 (thinking/tool_call/tool_result)
  done      | meta        | 流结束
  error     | meta        | 出错，code + message

每帧都透传 request_id。
"""
import json
from typing import Optional, Any

# 错误码
ERR_LLM_TIMEOUT = 5001
ERR_TOOL_TIMEOUT = 5002
ERR_INTERNAL = 5003
ERR_SERVICE_UNAVAILABLE = 5004


def sse_start(request_id: str, conversation_id: Optional[str] = None) -> dict:
    frame = {"event": "start", "data_type": "meta", "request_id": request_id}
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return frame


def sse_chat_delta(request_id: str, delta: str) -> dict:
    return {"event": "delta", "data_type": "chat", "request_id": request_id, "delta": delta}


def sse_signal_card(request_id: str, payload: Any) -> dict:
    return {"event": "delta", "data_type": "signal_card", "request_id": request_id, "payload": payload}


def sse_suggestions(request_id: str, payload: list) -> dict:
    return {"event": "delta", "data_type": "suggestions", "request_id": request_id, "payload": payload}


def sse_tool_debug(request_id: str, stage: str, payload: Any) -> dict:
    """stage: thinking / tool_call / tool_result"""
    return {"event": "delta", "data_type": "tool_debug", "request_id": request_id, "stage": stage, "payload": payload}


def sse_done(request_id: str) -> dict:
    return {"event": "done", "data_type": "meta", "request_id": request_id}


def sse_error(request_id: str, code: int, message: str) -> dict:
    return {"event": "error", "data_type": "meta", "request_id": request_id, "code": code, "message": message}


def render(frame: dict) -> dict:
    """将协议帧转为 EventSourceResponse 兼容的 {"event": ..., "data": json.dumps(...)}"""
    return {
        "event": frame["event"],
        "data": json.dumps(frame, ensure_ascii=False, default=str),
    }
