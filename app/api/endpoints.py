import json
import time
from typing import Generator
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sse_starlette.sse import EventSourceResponse

from app.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ToolsResponse,
    ErrorResponse,
    StreamChunk
)
from app.agents.crypto_agent import crypto_agent
from app.core.config import get_settings
from app.core.exceptions import CryptoAnalystException
from app.utils.validators import validate_symbol, validate_question, validate_language

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


@router.get("/tools", response_model=ToolsResponse)
async def get_tools():
    """获取可用工具列表"""
    tools_info = crypto_agent.get_available_tools()
    return ToolsResponse(
        tools=tools_info,
        count=len(tools_info)
    )


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """分析加密货币（非流式）"""
    try:
        # 验证输入
        symbol = validate_symbol(request.symbol)
        question = validate_question(request.question)
        lang = validate_language(request.lang.value)

        # 执行分析
        result = crypto_agent.analyze(
            symbol=symbol,
            question=question,
            lang=lang
        )

        return AnalyzeResponse(**result)

    except CryptoAnalystException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"分析失败: {str(e)}"
        )


@router.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """分析加密货币（流式）"""
    async def event_generator():
        try:
            # 验证输入
            symbol = validate_symbol(request.symbol)
            question = validate_question(request.question)
            lang = validate_language(request.lang.value)

            # 执行流式分析
            for chunk in crypto_agent.analyze_stream(
                symbol=symbol,
                question=question,
                lang=lang
            ):
                yield {
                    "event": "message",
                    "data": json.dumps({"data": chunk, "type": "chunk"})
                }

            # 发送完成信号
            yield {
                "event": "message",
                "data": json.dumps({"data": "", "type": "complete"})
            }

        except CryptoAnalystException as e:
            yield {
                "event": "message",
                "data": json.dumps({
                    "data": f"错误: {e.detail}",
                    "type": "error"
                })
            }
        except Exception as e:
            yield {
                "event": "message",
                "data": json.dumps({
                    "data": f"分析失败: {str(e)}",
                    "type": "error"
                })
            }

    return EventSourceResponse(event_generator())


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """对话式交互（非流式）"""
    try:
        # 验证输入
        message = validate_question(request.message)
        lang = validate_language(request.lang.value)

        # 执行对话
        result = crypto_agent.chat(
            message=message,
            conversation_id=request.conversation_id,
            lang=lang
        )

        return ChatResponse(**result)

    except CryptoAnalystException as e:
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"对话失败: {str(e)}"
        )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """对话式交互（流式）"""
    async def event_generator():
        try:
            # 验证输入
            message = validate_question(request.message)
            lang = validate_language(request.lang.value)

            # 执行流式对话
            for chunk in crypto_agent.chat_stream(
                message=message,
                conversation_id=request.conversation_id,
                lang=lang
            ):
                yield {
                    "event": "message",
                    "data": json.dumps({"data": chunk, "type": "chunk"})
                }

            # 发送完成信号
            yield {
                "event": "message",
                "data": json.dumps({"data": "", "type": "complete"})
            }

        except CryptoAnalystException as e:
            yield {
                "event": "message",
                "data": json.dumps({
                    "data": f"错误: {e.detail}",
                    "type": "error"
                })
            }
        except Exception as e:
            yield {
                "event": "message",
                "data": json.dumps({
                    "data": f"对话失败: {str(e)}",
                    "type": "error"
                })
            }

    return EventSourceResponse(event_generator())


@router.post("/clear")
async def clear_memory():
    """清除对话记忆"""
    try:
        crypto_agent.clear_memory()
        return {"message": "对话记忆已清除"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"清除记忆失败: {str(e)}"
        )


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