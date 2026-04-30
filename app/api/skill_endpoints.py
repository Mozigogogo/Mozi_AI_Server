"""Skill 系统测试端点"""
import asyncio
from typing import Generator
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.skills.agent import crypto_agent
from app.skills.base import IntentInfo


class TestRequest(BaseModel):
    question: str
    mode: str = "chat"  # chat 或 think


class TestResponse(BaseModel):
    question: str
    mode: str
    intent: dict
    response: str


router = APIRouter(prefix="/test/skill", tags=["Skill System Test"])


@router.post("/answer/stream")
async def test_answer_stream(request: TestRequest):
    """测试完整问答流程（流式）"""

    async def event_generator():
        try:
            # 发送意图信息
            intent = await crypto_agent.test_intent_analysis(request.question)
            yield f"INTENT: {intent.model_dump_json()}\n\n"

            # 流式回答
            async for chunk in crypto_agent.answer(request.question, request.mode):
                yield chunk

        except Exception as e:
            yield f"\nERROR: {str(e)}\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/plain"
    )
