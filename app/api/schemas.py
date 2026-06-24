from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum


class Language(str, Enum):
    """语言枚举"""
    ZH = "zh"
    EN = "en"


class AnalyzeRequest(BaseModel):
    """分析请求模型"""
    model_config = ConfigDict(populate_by_name=True)

    request_id: str = Field(..., description="客户端生成，SSE 全程透传", max_length=100)
    user_id: str = Field(..., description="用户 ID", max_length=100)
    message: str = Field(
        ...,
        alias="question",
        description="用户消息（旧字段名 question 仍兼容）",
        min_length=2,
        max_length=1000,
        example="请分析当前市场状况",
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="会话ID，用于保持上下文记忆",
        max_length=100,
        example="conv_123"
    )
    lang: Language = Field(
        default=Language.ZH,
        description="语言，zh（中文）或en（英文）",
        example="zh"
    )


class ChatRequest(BaseModel):
    """聊天请求模型"""
    request_id: str = Field(..., description="客户端生成，SSE 全程透传", max_length=100)
    user_id: str = Field(..., description="用户 ID", max_length=100)
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


class RouteRequest(BaseModel):
    """指令路由请求"""
    model_config = ConfigDict(populate_by_name=True)
    message: str = Field(
        ...,
        alias="question",
        description="用户问题",
        min_length=1,
        max_length=1000,
        example="BTC现在多少钱",
    )
    conversation_id: Optional[str] = Field(
        default=None, description="会话ID（可选，预留上下文使用）", max_length=100
    )


class RouteResponse(BaseModel):
    """指令路由响应"""
    command: Optional[str] = Field(
        None, description="对应指令，如 /price。LLM 失败时为 null，前端读 fallback_text"
    )
    coin_symbol: Optional[str] = Field(None, description="从问题中提取的币种符号（大写）")
    confidence: float = Field(0.0, description="置信度 0-1")
    reason: str = Field("", description="判定理由（调试用）")
    language: str = Field("zh", description="zh 或 en")
    fallback_text: Optional[str] = Field(
        None, description="LLM 失败时返回的能力介绍文案，前端直接渲染为消息"
    )


class ToolInfo(BaseModel):
    """工具信息模型"""
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    args_schema: Optional[str] = Field(None, description="参数模式")


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
    "question": "请分析当前市场状况和技术面",
    "lang": "zh",
}

chat_request_example = {
    "message": "BTC最近表现如何？",
    "conversation_id": "conv_123",
    "lang": "zh"
}

health_response_example = {
    "status": "healthy",
    "version": "1.0.0",
    "timestamp": "2024-01-01T00:00:00Z"
}
