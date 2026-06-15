"""API 端点 - 使用新的 Skill 系统"""
import json
import time
import traceback
from typing import Optional
from fastapi import APIRouter, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from app.api.schemas import (
    AnalyzeRequest,
    ChatRequest,
    HealthResponse,
    ErrorResponse,
)
from app.skills.agent import crypto_agent
from app.core.config import get_settings
from app.core.exceptions import CryptoAnalystException
from app.utils.validators import validate_symbol, validate_question, validate_language
from app.utils.sse_protocol import (
    sse_start, sse_chat_delta, sse_signal_card, sse_suggestions,
    sse_done, sse_error, render, ERR_INTERNAL,
)

settings = get_settings()
router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查端点"""
    return HealthResponse(
        status="healthy",
        version=settings.app_version,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    )


@router.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """分析加密货币（流式）"""
    rid = request.request_id

    async def event_generator():
        try:
            yield render(sse_start(rid, request.conversation_id))

            async for chunk in crypto_agent.answer(
                request.message, mode="think",
                conversation_id=request.conversation_id
            ):
                if isinstance(chunk, dict):
                    chunk_type = chunk.get("type", "")
                    if chunk_type == "signal_card":
                        yield render(sse_signal_card(rid, chunk.get("data", chunk)))
                    elif chunk_type == "suggestions":
                        yield render(sse_suggestions(rid, chunk.get("suggestions", [])))
                    else:
                        yield render(sse_chat_delta(rid, json.dumps(chunk, ensure_ascii=False)))
                else:
                    yield render(sse_chat_delta(rid, chunk))

            yield render(sse_done(rid))

        except CryptoAnalystException as e:
            yield render(sse_error(rid, ERR_INTERNAL, e.detail))
        except Exception as e:
            traceback.print_exc()
            yield render(sse_error(rid, ERR_INTERNAL, f"分析失败: {e}"))

    return EventSourceResponse(event_generator())


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """对话式交互（流式）"""
    rid = request.request_id

    async def event_generator():
        try:
            yield render(sse_start(rid, request.conversation_id))

            async for chunk in crypto_agent.answer(
                request.message, mode="chat",
                conversation_id=request.conversation_id
            ):
                if isinstance(chunk, dict):
                    chunk_type = chunk.get("type", "")
                    if chunk_type == "signal_card":
                        yield render(sse_signal_card(rid, chunk.get("data", chunk)))
                    elif chunk_type == "suggestions":
                        yield render(sse_suggestions(rid, chunk.get("suggestions", [])))
                    else:
                        yield render(sse_chat_delta(rid, json.dumps(chunk, ensure_ascii=False)))
                else:
                    yield render(sse_chat_delta(rid, chunk))

            yield render(sse_done(rid))

        except CryptoAnalystException as e:
            yield render(sse_error(rid, ERR_INTERNAL, e.detail))
        except Exception as e:
            traceback.print_exc()
            yield render(sse_error(rid, ERR_INTERNAL, f"对话失败: {e}"))

    return EventSourceResponse(event_generator())


@router.get("/symbols")
async def get_supported_symbols():
    """获取支持的币种列表"""
    # 这里可以扩展从数据库或配置文件获取
    common_symbols = [
        "BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "AVAX", "DOT", "DOGE", "MATIC",
        "LTC", "LINK", "UNI", "ATOM", "ETC", "XLM", "FIL", "ICP", "ALGO", "VET"
    ]
    return {
        "symbols": common_symbols,
        "count": len(common_symbols),
        "note": "支持更多币种，但常见币种数据更完整"
    }


# 错误处理
@router.get("/error-test")
async def error_test():
    """错误测试端点（仅用于开发）"""
    if settings.debug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="这是一个测试错误"
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="生产环境禁用错误测试"
        )
