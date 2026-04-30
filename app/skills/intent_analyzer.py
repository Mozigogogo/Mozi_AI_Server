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
- query_price: 查询价格、市值、排名、涨跌幅、价格变化、24h价格变化等基础信息（包括当前价格、历史最高/最低价、换手率等）
- query_trend: 查询趋势、走势、K线形态、技术形态分析等（主要针对历史价格走势、图表形态，不包含具体数值变化）
- query_news: 查询新闻、热点事件
- query_derivatives: 查询衍生品数据（包括成交量、成交额、持仓量、多空比、资金费率、合约数据等）
  关键词：成交量、成交额、持仓量、多空比、买卖比、资金费率、合约、衍生品、交易所数据
- analyze_technical: 技术面分析（趋势、支撑阻力、指标）
- analyze_sentiment: 情绪分析（市场情绪、多空结构）
- analyze_comprehensive: 综合分析（多维度全面分析）
  关键词：综合分析、全面分析、整体分析、详细分析
- analyze_quantitative: 量化决策分析（六因子评分模型）
  关键词：量化分析、买入卖出、点位判断、决策建议、操作建议、胜率分析、量化评分
- simple_chat: 简单对话（问候、感谢等）

可用的 API 列表（根据问题需求选择，不要调用不必要的 API）：

基础数据 APIs (query_price):
- get_header_data: 获取价格、市值、排名等基本信息

趋势数据 APIs (query_trend):
- get_kline_data: 获取 K 线数据（价格走势、最高最低）

新闻 APIs (query_news):
- get_recent_news: 获取最新新闻

衍生品数据 APIs (query_derivatives - 当用户问到以下任何内容时使用):
- get_buy_sell_ratio: 获取买卖比例（多空比） - 关键词：多空比、买卖比
- get_open_interest: 获取持仓量 - 关键词：持仓量、持仓、OI
- get_trading_volume: 获取交易量/成交额 - 关键词：成交量、成交额、交易量
- get_funding_rate: 获取资金费率 - 关键词：资金费率、费率

注意事项：
1. 严格按照用户使用的语言回答（中文问题用 zh，英文问题用 en）
2. 只选择真正需要的 API，不要调用不必要的 API
3. 如果用户没有提到币种，coin_symbol 设为 null
4. 确保输出的 JSON 是有效的
5. 输出只包含 JSON，不要包含其他内容

意图判断规则（重要）：
- **涨跌幅/价格变化查询 → query_price**：当用户问及以下关键词时，必须设置为 query_price：
  * "涨跌幅"、"价格变化"、"24h价格变化"、"24h涨跌幅"、"涨了还是跌了"、"涨了/跌了多少"、"今天涨/跌"
  * "市值"、"排名"、"当前价格"、"价格是多少"、"现在的价格"等基础信息查询
  * 此类查询使用 get_header_data API（包含涨跌幅数据、价格变化百分比等）

- **趋势查询 → query_trend**：当用户问及以下关键词时，设置为 query_trend：
  * "趋势"、"走势"、"K线"、"技术形态"、"支撑阻力"、"趋势方向"、"上涨趋势"、"下跌趋势"、"震荡趋势"
  * 此类查询侧重图表形态分析，使用 get_kline_data API

- **衍生品查询 → query_derivatives**：当用户问及以下关键词时，设置为 query_derivatives：
  * 成交量、成交额、交易量
  * 多空比、买卖比、多头空头
  * 持仓量、持仓、OI
  * 资金费率、费率
  * 合约、衍生品、期货
  * 交易所数据
- 示例："BTC 的涨跌幅是多少？" → intent_type: "query_price", required_apis: ["get_header_data"]
- 示例："ETH 各交易所的成交量是多少？" → intent_type: "query_derivatives", required_apis: ["get_trading_volume"]
- 示例："BTC 的多空比是多少？" → intent_type: "query_derivatives", required_apis: ["get_buy_sell_ratio"]
- 示例："ETH 的资金费率怎么样？" → intent_type: "query_derivatives", required_apis: ["get_funding_rate"]
- 示例："ETH的量化分析" → intent_type: "analyze_quantitative", required_apis: ["get_header_data", "get_kline_data", "get_trading_volume"]
- 示例："BTC买入卖出点位判断" → intent_type: "analyze_quantitative", required_apis: ["get_header_data", "get_kline_data", "get_trading_volume"]

- **避免误判**：涨跌幅查询是关于具体数值变化，不是趋势分析
  * 如果问题包含具体数值变化（涨跌幅、价格变化百分比），应使用 query_price，不是 query_trend

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
                max_tokens=1000,  # 增加token限制，防止JSON被截断
                timeout=30.0,  # 添加超时设置
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
