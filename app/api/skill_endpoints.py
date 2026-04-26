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


@router.post("/intent", response_model=dict)
async def test_intent_analysis(request: TestRequest):
    """测试意图分析"""
    intent = await crypto_agent.test_intent_analysis(request.question)
    return intent.model_dump()


@router.post("/answer", response_model=TestResponse)
async def test_answer(request: TestRequest):
    """测试完整问答流程（非流式）"""
    # 收集流式输出
    response_parts = []
    async for chunk in crypto_agent.answer(request.question, request.mode):
        response_parts.append(chunk)

    # 意图分析
    intent = await crypto_agent.test_intent_analysis(request.question)

    return TestResponse(
        question=request.question,
        mode=request.mode,
        response="".join(response_parts)
    )


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


@router.post("/batch")
async def test_batch(questions: list[str]):
    """批量测试多个问题"""
    results = []
    for question in questions:
        intent = await crypto_agent.test_intent_analysis(question)
        response_parts = []
        async for chunk in crypto_agent.answer(question, "chat"):
            response_parts.append(chunk)

        results.append({
            "question": question,
            "intent": intent.model_dump(),
            "response": "".join(response_parts)
        })

    return {
        "total": len(results),
        "results": results
    }
