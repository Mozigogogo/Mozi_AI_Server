"""共享 AsyncOpenAI 客户端单例"""
from openai import AsyncOpenAI
from app.core.config import get_settings

_client: AsyncOpenAI = None


def get_llm_client() -> AsyncOpenAI:
    """获取共享的 AsyncOpenAI 客户端（首次调用时创建）"""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_api_base,
            max_retries=1,
        )
    return _client
