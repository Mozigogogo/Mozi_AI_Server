"""意图分析器 - 纯 LLM 驱动"""
from typing import Dict, Any
import json
import re

from openai import AsyncOpenAI

from app.skills.base import IntentInfo
from app.core.config import get_settings

settings = get_settings()


class IntentAnalyzer:
    """意图分析器 - 使用 LLM 理解用户意图"""

    def __init__(self, openai_client: AsyncOpenAI = None):
        self.client = openai_client or AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_api_base
        )
        self.prompt_template = self._get_prompt_template()

    def _get_prompt_template(self) -> str:
        """获取 Prompt 模板"""
        return """你是一个加密货币分析助手的意图识别器。

请分析以下用户问题，提取关键信息，并以 JSON 格式输出。

用户问题：{question}

请输出以下 JSON 结构：
{{
  "language": "zh 或 en",                    // 用户使用的语言
  "intent_type": "问题意图类型",              // 见下方意图类型列表
  "coin_symbol": "币种符号（如 BTC、ETH）",   // 如果能识别到
  "required_apis": ["需要调用的 API 列表"],  // 根据问题需求决定
  "answer_requirements": ["回答需要包含的内容"],
  "confidence": 0.0                          // 置信度 [0-1]
}}

意图类型包括：
- query_price: 查询价格、市值、排名等基础信息
- query_trend: 查询趋势、走势、涨跌幅
- query_news: 查询新闻、热点事件
- query_derivatives: 查询持仓、资金费率、买卖比等衍生品数据
- analyze_technical: 技术面分析（趋势、支撑阻力、指标）
- analyze_sentiment: 情绪分析（市场情绪、多空结构）
- analyze_comprehensive: 综合分析（多维度全面分析）
- simple_chat: 简单对话（问候、感谢等）

可用的 API 列表（根据问题需求选择，不要调用不必要的 API）：
- get_header_data: 获取价格、市值、排名等基本信息
- get_kline_data: 获取 K 线数据（价格走势、最高最低）
- get_recent_news: 获取最新新闻
- get_buy_sell_ratio: 获取买卖比例
- get_open_interest: 获取持仓量
- get_trading_volume: 获取交易量
- get_funding_rate: 获取资金费率

注意事项：
1. 严格按照用户使用的语言回答（中文问题用 zh，英文问题用 en）
2. 只选择真正需要的 API，不要调用不必要的 API
3. 如果用户没有提到币种，coin_symbol 设为 null
4. 确保输出的 JSON 是有效的
5. 输出只包含 JSON，不要包含其他内容

请只输出 JSON："""

    async def analyze(self, question: str) -> IntentInfo:
        """
        分析用户意图

        Args:
            question: 用户问题

        Returns:
            IntentInfo: 意图信息
        """
        try:
            # 构建 prompt
            prompt = self.prompt_template.format(question=question)

            # 调用 LLM
            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                max_tokens=500,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # 提取响应
            response_text = response.choices[0].message.content.strip()

            # 解析 JSON
            intent_data = self._parse_json_response(response_text)

            # 构建 IntentInfo
            return IntentInfo(
                language=intent_data.get("language", "zh"),
                intent_type=intent_data.get("intent_type", "simple_chat"),
                coin_symbol=intent_data.get("coin_symbol"),
                required_apis=intent_data.get("required_apis", []),
                answer_requirements=intent_data.get("answer_requirements", []),
                raw_question=question,
                confidence=intent_data.get("confidence", 0.0)
            )

        except Exception as e:
            print(f"意图分析失败: {e}")
            # 返回默认意图
            return IntentInfo(
                language="zh" if any(ord(c) > 127 for c in question) else "en",
                intent_type="simple_chat",
                coin_symbol=None,
                required_apis=[],
                answer_requirements=[],
                raw_question=question,
                confidence=0.0
            )

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """解析 JSON 响应"""
        # 尝试提取 JSON（处理可能的前后文本）
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            response = json_match.group()

        try:
            return json.loads(response)
        except json.JSONDecodeError as e:
            print(f"JSON 解析失败: {e}, 原始响应: {response}")
            # 返回默认值
            return {
                "language": "zh",
                "intent_type": "simple_chat",
                "coin_symbol": None,
                "required_apis": [],
                "answer_requirements": [],
                "confidence": 0.0
            }
