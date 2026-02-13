from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class Language(str, Enum):
    """语言枚举"""
    ZH = "zh"
    EN = "en"


class AnalyzeRequest(BaseModel):
    """分析请求模型"""
    symbol: str = Field(
        ...,
        description="加密货币符号，如BTC、ETH",
        min_length=1,
        max_length=10,
        example="BTC"
    )
    question: str = Field(
        ...,
        description="分析问题",
        min_length=2,
        max_length=1000,
        example="请分析当前市场状况"
    )
    lang: Language = Field(
        default=Language.ZH,
        description="语言，zh（中文）或en（英文）",
        example="zh"
    )


class ChatRequest(BaseModel):
    """聊天请求模型"""
    message: str = Field(
        ...,
        description="用户消息",
        min_length=2,
        max_length=1000,
        example="BTC最近表现如何？"
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="会话ID，用于保持对话上下文",
        max_length=100,
        example="conv_123"
    )
    lang: Language = Field(
        default=Language.ZH,
        description="语言，zh（中文）或en（英文）",
        example="zh"
    )


class ToolInfo(BaseModel):
    """工具信息模型"""
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    args_schema: Optional[str] = Field(None, description="参数模式")


class AnalyzeResponse(BaseModel):
    """分析响应模型"""
    symbol: str = Field(..., description="加密货币符号")
    question: str = Field(..., description="分析问题")
    response: str = Field(..., description="分析响应")
    intermediate_steps: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="中间步骤"
    )
    lang: str = Field(..., description="语言")


class ChatResponse(BaseModel):
    """聊天响应模型"""
    message: str = Field(..., description="用户消息")
    response: str = Field(..., description="AI响应")
    conversation_id: Optional[str] = Field(None, description="会话ID")
    lang: str = Field(..., description="语言")


class HealthResponse(BaseModel):
    """健康检查响应模型"""
    status: str = Field(..., description="服务状态")
    version: str = Field(..., description="应用版本")
    timestamp: str = Field(..., description="时间戳")


class ToolsResponse(BaseModel):
    """工具列表响应模型"""
    tools: List[ToolInfo] = Field(..., description="工具列表")
    count: int = Field(..., description="工具数量")


class ErrorResponse(BaseModel):
    """错误响应模型"""
    error: str = Field(..., description="错误信息")
    detail: Optional[str] = Field(None, description="错误详情")
    code: int = Field(..., description="错误代码")


class StreamChunk(BaseModel):
    """流式响应块模型"""
    data: str = Field(..., description="数据块")
    type: str = Field(
        default="chunk",
        description="数据类型：chunk（数据块）、complete（完成）、error（错误）"
    )


# 请求示例
analyze_request_example = {
    "symbol": "BTC",
    "question": "请分析当前市场状况和技术面",
    "lang": "zh"
}

chat_request_example = {
    "message": "BTC最近表现如何？",
    "conversation_id": "conv_123",
    "lang": "zh"
}

# 响应示例
analyze_response_example = {
    "symbol": "BTC",
    "question": "请分析当前市场状况和技术面",
    "response": "基于技术分析，BTC目前处于...",
    "intermediate_steps": [
        {
            "tool": "get_market_data",
            "input": {"symbol": "BTC"},
            "output": {"summary": "已获取BTC的市场数据"}
        }
    ],
    "lang": "zh"
}

chat_response_example = {
    "message": "BTC最近表现如何？",
    "response": "根据分析，BTC近期表现...",
    "conversation_id": "conv_123",
    "lang": "zh"
}

health_response_example = {
    "status": "healthy",
    "version": "1.0.0",
    "timestamp": "2024-01-01T00:00:00Z"
}