"""回答生成器 - 语言跟随用户"""
from typing import Dict, Any

from anthropic import AsyncAnthropic

from app.skills.base import SkillResult, IntentInfo
from app.core.config import get_settings

settings = get_settings()


class ResponseGenerator:
    """回答生成器 - 使用用户语言生成回答"""

    def __init__(self, anthropic_client: AsyncAnthropic = None):
        self.client = anthropic_client or AsyncAnthropic(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_api_base
        )
        self.templates = self._get_prompt_templates()

    def _get_prompt_templates(self) -> Dict[str, Dict[str, str]]:
        """获取不同语言的 Prompt 模板"""
        return {
            "zh": {
                "chat": """你是一个加密货币分析助手。请用简洁的中文回答用户的问题。

用户问题：{question}
数据时间：{timestamp}
获取的数据：
{data}

回答要求：
{answer_requirements}

请直接回答，不超过 150 字。""",

                "think": """你是一个专业的加密货币分析师。请用中文进行深度分析。

用户问题：{question}
数据时间：{timestamp}
获取的数据：
{data}

分析要求：
{answer_requirements}

请提供结构化的深度分析（500-800字）。"""
            },
            "en": {
                "chat": """You are a cryptocurrency analysis assistant. Please answer the user's question concisely in English.

Question: {question}
Data timestamp: {timestamp}
Retrieved data:
{data}

Answer requirements:
{answer_requirements}

Answer directly, within 150 words.""",

                "think": """You are a professional cryptocurrency analyst. Please provide deep analysis in English.

Question: {question}
Data timestamp: {timestamp}
Retrieved data:
{data}

Analysis requirements:
{answer_requirements}

Provide structured deep analysis (500-800 words)."""
            }
        }

    async def generate_response(
        self,
        skill_result: SkillResult,
        intent: IntentInfo,
        mode: str = "chat"
    ) -> str:
        """
        生成回答（使用用户语言）

        Args:
            skill_result: Skill 执行结果
            intent: 意图信息
            mode: 模式（chat/think）

        Returns:
            str: 生成的回答
        """
        try:
            # 获取对应的语言模板
            template = self.templates.get(intent.language, self.templates["zh"])[mode]

            # 格式化数据
            formatted_data = self._format_data(skill_result.data)

            # 格式化回答要求
            answer_requirements = "\n".join(
                f"- {req}" for req in (intent.answer_requirements or ["准确回答用户问题"])
            )

            # 构建 Prompt
            prompt = template.format(
                question=intent.raw_question,
                timestamp=skill_result.timestamp,
                data=formatted_data,
                answer_requirements=answer_requirements
            )

            # 调用 LLM 生成回答
            max_tokens = 800 if mode == "think" else 300

            message = await self.client.messages.create(
                model=settings.deepseek_model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response = message.content[0].text.strip()
            return response

        except Exception as e:
            print(f"回答生成失败: {e}")
            # 返回错误消息
            if intent.language == "zh":
                return f"抱歉，生成回答时出错：{str(e)}"
            else:
                return f"Sorry, error generating response: {str(e)}"

    def _format_data(self, data: Any) -> str:
        """格式化数据为可读文本"""
        if isinstance(data, dict):
            items = []
            for key, value in data.items():
                if isinstance(value, (int, float)):
                    items.append(f"{key}: {value}")
                else:
                    value_str = str(value)
                    # 限制长度
                    if len(value_str) > 200:
                        value_str = value_str[:200] + "..."
                    items.append(f"{key}: {value_str}")
            return "\n".join(items)
        elif isinstance(data, list):
            return f"[{len(data)} items]"
        else:
            value_str = str(data)
            if len(value_str) > 300:
                value_str = value_str[:300] + "..."
            return value_str

    def get_greeting(self, language: str = "zh") -> str:
        """获取问候语"""
        greetings = {
            "zh": "你好！我是加密货币分析助手，请问有什么可以帮您？",
            "en": "Hello! I'm a cryptocurrency analysis assistant. How can I help you?"
        }
        return greetings.get(language, greetings["zh"])

    def get_no_symbol_message(self, language: str = "zh") -> str:
        """获取无币种提示"""
        messages = {
            "zh": "请指定要查询的币种，例如：BTC、ETH、SOL 等",
            "en": "Please specify a cryptocurrency, e.g., BTC, ETH, SOL, etc."
        }
        return messages.get(language, messages["zh"])
