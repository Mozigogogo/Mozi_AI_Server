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
    AnalyzeResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    ErrorResponse,
)
from app.skills.agent import crypto_agent
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


@router.post("/analyze", response_model=AnalyzeResponse)
async def analyze(request: AnalyzeRequest):
    """分析加密货币（非流式）"""
    print(f"DEBUG: Entering analyze endpoint with request: {request}")
    try:
        # 构建问题
        question = f"请分析{request.symbol}：{request.question}"
        if request.lang.value == "en":
            question = f"Analyze {request.symbol}: {request.question}"

        # 收集流式输出
        response_parts = []
        async for chunk in crypto_agent.answer(question, mode="think"):
            response_parts.append(chunk)

        response = "".join(response_parts)

        return AnalyzeResponse(
            symbol=request.symbol,
            question=request.question,
            response=response,
            lang=request.lang.value
        )

    except CryptoAnalystException as e:
        print(f"CryptoAnalystException: {e.detail}")
        raise HTTPException(
            status_code=e.status_code,
            detail=e.detail
        )
    except Exception as e:
        print(f"Internal Server Error: {str(e)}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"分析失败: {str(e)}"
        )


@router.post("/analyze/stream")
async def analyze_stream(request: AnalyzeRequest):
    """分析加密货币（流式）"""
    print(f"DEBUG: Entering analyze_stream endpoint with request: {request}")
    async def event_generator():
        try:
            # 立即发送开始信号
            yield {
                "event": "message",
                "data": json.dumps({"data": "", "type": "start"})
            }

            # 构建问题
            question = f"请分析{request.symbol}：{request.question}"
            if request.lang.value == "en":
                question = f"Analyze {request.symbol}: {request.question}"

            # 流式执行
            async for chunk in crypto_agent.answer(question, mode="think"):
                yield {
                    "event": "message",
                    "data": json.dumps({"data": chunk, "type": "chunk"})
                }

            print("DEBUG: Stream - Generator loop finished.")
            # 发送完成信号
            yield {
                "event": "message",
                "data": json.dumps({"data": "", "type": "complete"})
            }

        except CryptoAnalystException as e:
            print(f"DEBUG: Stream - CryptoAnalystException: {e.detail}")
            yield {
                "event": "message",
                "data": json.dumps({
                    "data": f"错误: {e.detail}",
                    "type": "error"
                })
            }
        except Exception as e:
            print(f"DEBUG: Stream - Internal Error: {str(e)}")
            traceback.print_exc()
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
        # 收集流式输出
        response_parts = []
        async for chunk in crypto_agent.answer(request.message, mode="chat"):
            response_parts.append(chunk)

        response = "".join(response_parts)

        return ChatResponse(
            message=request.message,
            response=response,
            conversation_id=request.conversation_id,
            lang=request.lang.value
        )

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
            # 立即发送开始信号
            yield {
                "event": "message",
                "data": json.dumps({"data": "", "type": "start"})
            }

            # 流式执行
            async for chunk in crypto_agent.answer(request.message, mode="chat"):
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
async def clear_memory(mode: Optional[str] = Query(None, description="清除指定模式的记忆: chat/analysis，不传则清除全部")):
    """清除对话记忆"""
    # 新的 Skill 系统没有记忆功能，返回成功即可
    return {"message": "Skill 系统不需要清除记忆"}


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
