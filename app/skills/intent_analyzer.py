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
        return """分析用户问题，输出JSON。禁止输出其他内容。

用户问题：{question}

输出格式：
{{"language":"zh或en","intent_type":"类型","coin_symbol":"币种","required_apis":["API列表"],"answer_requirements":["要求"],"confidence":0.0}}

意图类型：query_price(价格/市值/涨跌幅) | query_trend(趋势/走势/K线) | query_news(新闻) | query_derivatives(成交量/持仓量/多空比/资金费率) | analyze_technical(技术面) | analyze_comprehensive(综合分析) | analyze_quantitative(量化分析) | simple_chat(闲聊)

API：get_header_data(价格) | get_kline_data(K线) | get_recent_news(新闻) | get_buy_sell_ratio(多空比) | get_open_interest(持仓量) | get_trading_volume(成交量) | get_funding_rate(资金费率)

规则：价格变化/涨跌幅→query_price，成交量/持仓/多空比/资金费率→query_derivatives，量化/买入卖出→analyze_quantitative。只选需要的API。只输出JSON："""

    async def analyze(self, question: str) -> IntentInfo:
        """分析用户意图（含重试）"""
        for attempt in range(2):  # 最多重试1次
            try:
                prompt = self.prompt_template.format(question=question)

                response = await self.client.chat.completions.create(
                    model=settings.deepseek_model,
                    max_tokens=500,
                    timeout=20.0,
                    messages=[{"role": "user", "content": prompt}]
                )

                response_text = response.choices[0].message.content.strip()
                intent_data = self._parse_json_response(response_text)

                # 检查是否解析成功（非 simple_chat 默认值）
                if intent_data.get("intent_type") != "simple_chat":
                    return IntentInfo(
                        language=intent_data.get("language", "zh"),
                        intent_type=intent_data["intent_type"],
                        coin_symbol=intent_data.get("coin_symbol"),
                        required_apis=intent_data.get("required_apis", []),
                        answer_requirements=intent_data.get("answer_requirements", []),
                        raw_question=question,
                        confidence=intent_data.get("confidence", 0.0)
                    )

                # 如果解析结果为 simple_chat 但用户问的不是闲聊，重试
                if attempt == 0:
                    print(f"  ⚠️ 意图识别可能不正确({intent_data})，重试...")
                    continue

                # 第二次还是 simple_chat，可能真的是闲聊
                return IntentInfo(
                    language=intent_data.get("language", "zh"),
                    intent_type="simple_chat",
                    raw_question=question,
                )

            except Exception as e:
                if attempt == 0:
                    print(f"  ⚠️ 意图识别异常({e})，重试...")
                    continue
                print(f"  ❌ 意图识别重试后仍失败: {e}")

        # 兜底
        return IntentInfo(
            language="zh" if any(ord(c) > 127 for c in question) else "en",
            intent_type="simple_chat",
            raw_question=question,
        )

    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """解析 JSON 响应"""
        # 尝试提取 JSON（处理可能的前后文本）
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            candidate = json_match.group()
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                # JSON 被截断，尝试补全
                pass

        # 尝试直接解析
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 截断 JSON 补全：逐步补全括号
        for suffix in [']}', ']}', '"}']:
            try:
                return json.loads(response + suffix)
            except json.JSONDecodeError:
                continue

        print(f"JSON 解析失败, 原始响应: {response[:200]}")
        # 返回默认值
        return {
            "language": "zh",
            "intent_type": "simple_chat",
            "coin_symbol": None,
            "required_apis": [],
            "answer_requirements": [],
            "confidence": 0.0
        }
