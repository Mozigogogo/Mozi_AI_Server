from typing import Generator, List, Dict, Any
from openai import OpenAI
from app.core.config import get_settings
from app.core.exceptions import LLMException

settings = get_settings()


class LLMService:
    """LLM服务类"""

    def __init__(self):
        self.client = OpenAI(
            base_url=settings.deepseek_api_base,
            api_key=settings.deepseek_api_key
        )
        self.model = settings.deepseek_model
        self.temperature = settings.llm_temperature
        self.max_tokens = settings.llm_max_tokens

    def system_prompt(self, lang: str = "zh") -> str:
        """获取系统提示词"""
        if lang == "en":
            return (
                "You are a professional, cautious, compliance-aware crypto analyst. "
                "You do NOT provide investment advice or price targets."
            )
        return (
            "你是一位专业、谨慎、遵守合规要求的加密货币分析师，"
            "不提供投资建议或确定性结论。"
        )

    def call_llm_stream(
        self,
        prompt: str,
        lang: str = "zh",
        system_prompt_override: str = None
    ) -> Generator[str, None, None]:
        """调用LLM流式接口"""
        try:
            system_content = system_prompt_override or self.system_prompt(lang)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True
            )

            for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            raise LLMException(f"LLM调用失败: {str(e)}")

    def call_llm(
        self,
        prompt: str,
        lang: str = "zh",
        system_prompt_override: str = None
    ) -> str:
        """调用LLM非流式接口"""
        try:
            system_content = system_prompt_override or self.system_prompt(lang)

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": prompt}
                ],
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=False
            )

            return response.choices[0].message.content
        except Exception as e:
            raise LLMException(f"LLM调用失败: {str(e)}")


# 全局LLM服务实例
llm_service = LLMService()