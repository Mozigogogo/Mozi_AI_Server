from typing import Dict, Any, Generator
import statistics
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolAndLangInput, SymbolAndQuestionInput
from app.services.data_service import get_kline_data, get_header_data, get_news_from_mysql, get_all_derivatives_data
from app.services.llm_service import llm_service
from app.utils.formatters import (
    format_kline_data,
    format_header_data,
    format_news_data,
    format_derivatives_data,
    create_analysis_prompt
)
from app.utils.validators import validate_symbol, validate_language, validate_question


class TechnicalAnalysisInput(SymbolAndQuestionInput):
    """技术分析输入模型"""
    lang: str = Field(
        default="zh",
        description="语言，zh（中文）或en（英文），默认zh"
    )


class TechnicalAnalysisTool(CryptoAnalystTool):
    """技术分析工具 - 基于K线数据进行技术分析"""

    name: str = "technical_analysis"
    description: str = "基于加密货币的K线数据进行技术分析，包括趋势、支撑阻力、技术指标等。当用户询问技术面、价格走势、技术指标时使用此工具。"
    args_schema: type = TechnicalAnalysisInput

    def execute(self, *args, **kwargs) -> str:
        """执行技术分析"""
        # 处理参数：可能通过位置参数或关键字参数传递
        if args:
            # 位置参数：symbol, question, lang
            if len(args) >= 3:
                symbol, question, lang = args[0], args[1], args[2]
            elif len(args) == 2:
                symbol, question, lang = args[0], args[1], "zh"
            elif len(args) == 1:
                symbol, question, lang = args[0], "当前价格趋势如何？", "zh"
            else:
                raise ValueError("TechnicalAnalysisTool需要至少一个参数：symbol")
        elif kwargs:
            # 关键字参数
            symbol = kwargs.get("symbol", kwargs.get("__arg1", "BTC"))
            question = kwargs.get("question", "当前价格趋势如何？")
            lang = kwargs.get("lang", "zh")
        else:
            symbol = "BTC"
            question = "当前价格趋势如何？"
            lang = "zh"

        symbol = validate_symbol(symbol)
        question = validate_question(question)
        lang = validate_language(lang)

        # 获取数据
        kline_data = get_kline_data(symbol)
        header_data = get_header_data(symbol)

        # 创建提示词
        prompt = create_analysis_prompt(symbol, question, kline_data, header_data)

        # 调用LLM
        response = llm_service.call_llm(prompt, lang)

        # 清理响应中的非ASCII字符（表情符号等），但保留中文字符
        # 移除常见的表情符号和特殊符号
        import re
        # 移除控制字符和某些表情符号范围
        cleaned_response = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u2000-\u2fff\ufe00-\ufe0f]', '', response)
        # 替换特定的常见表情符号
        cleaned_response = cleaned_response.replace('\u26a0', '警告:')  # ⚠
        cleaned_response = cleaned_response.replace('\u2757', '!!')     # ❗
        cleaned_response = cleaned_response.replace('\u2755', '??')     # ❕
        cleaned_response = cleaned_response.replace('\u203c', '!!')     # ‼
        cleaned_response = cleaned_response.replace('\u2049', '!?')     #

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}技术分析报告：\n\n问题：{question}\n\n分析结果：\n{cleaned_response}\n\n分析完成，基于30天K线数据和基本信息。"


class NewsAnalysisInput(SymbolAndLangInput):
    """新闻分析输入模型"""
    pass


class NewsAnalysisTool(CryptoAnalystTool):
    """新闻分析工具 - 分析加密货币相关新闻"""

    name: str = "news_analysis"
    description: str = "分析加密货币相关的新闻数据，解读市场情绪和事件影响。当用户询问新闻解读、事件分析、市场情绪时使用此工具。"
    args_schema: type = NewsAnalysisInput

    def execute(self, symbol: str, lang: str = "zh") -> str:
        """执行新闻分析"""
        symbol = validate_symbol(symbol)
        lang = validate_language(lang)

        # 获取新闻数据
        news = get_news_from_mysql(symbol, limit=20)
        if not news:
            # 返回格式化字符串（LangChain工具期望返回字符串）
            return f"{symbol}新闻分析：\n\n未找到{symbol}的相关新闻数据。\n\n分析完成。"

        formatted_news = format_news_data(news)

        # 创建新闻分析提示词
        if lang == "en":
            prompt = f"""
Analyze recent news related to {symbol}.

News:
{formatted_news}

Output:
[Summary]
[Potential Impact]
[Risk Notice]
"""
        else:
            prompt = f"""
请分析以下与 {symbol} 相关的近期新闻：

{formatted_news}

输出：
【新闻总结】
【潜在影响】
【风险提示】
"""

        # 调用LLM
        response = llm_service.call_llm(prompt, lang)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}新闻分析报告：\n\n基于{len(news)}条相关新闻\n\n分析结果：\n{response}\n\n分析完成。"


class DerivativesAnalysisInput(SymbolAndLangInput):
    """衍生品分析输入模型"""
    pass


class DerivativesAnalysisTool(CryptoAnalystTool):
    """衍生品分析工具 - 分析加密货币的衍生品市场数据"""

    name: str = "derivatives_analysis"
    description: str = "分析加密货币的衍生品市场数据，包括多空结构、资金流向、市场情绪等。当用户询问衍生品市场、多空分析、资金面时使用此工具。"
    args_schema: type = DerivativesAnalysisInput

    def execute(self, symbol: str, lang: str = "zh") -> str:
        """执行衍生品分析"""
        symbol = validate_symbol(symbol)
        lang = validate_language(lang)

        # 获取衍生品数据
        derivatives_data = get_all_derivatives_data(symbol)
        formatted_data = format_derivatives_data(derivatives_data)

        # 创建衍生品分析提示词
        if lang == "en":
            prompt = f"""
Analyze the derivatives market data for {symbol}:

{formatted_data}

Please analyze:
1. Changes in active buying and selling power
2. Open interest changes representing long-short game
3. Whether trading volume shows significant changes
4. Market sentiment reflected by funding rates

Output:
[Long-Short Structure]
[Capital Behavior]
[Short-term Sentiment Judgment]
"""
        else:
            prompt = f"""
以下是 {symbol} 的衍生品与资金面数据（多交易所）：

{formatted_data}

请分析：
1. 主动买卖力量变化
2. 持仓量变化代表的多空博弈
3. 成交额是否放量
4. 资金费率反映的市场情绪

输出：
【多空结构】
【资金行为】
【短期情绪判断】
"""

        # 调用LLM
        response = llm_service.call_llm(prompt, lang)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}衍生品分析报告：\n\n基于多交易所数据\n\n分析结果：\n{response}\n\n已完成{symbol}的衍生品市场分析，包括买卖比例、持仓量、交易量和资金费率。"


class QuantitativeAnalysisInput(SymbolAndLangInput):
    """量化分析输入模型"""
    pass


class QuantitativeAnalysisTool(CryptoAnalystTool):
    """量化分析工具 - 基于六因子模型进行量化分析"""

    name: str = "quantitative_analysis"
    description: str = "基于六因子量化评分模型进行加密货币的量化分析，提供概率判断。当用户询问量化分析、概率判断、风险评估时使用此工具。"
    args_schema: type = QuantitativeAnalysisInput

    def execute(self, symbol: str, lang: str = "zh") -> str:
        """执行量化分析"""
        symbol = validate_symbol(symbol)
        lang = validate_language(lang)

        # 获取所有必要数据（分别处理，避免单个数据源失败影响整个分析）
        kline_data = None
        header_data = None
        news = []
        derivatives_data = None

        try:
            kline_data = get_kline_data(symbol)
        except Exception as e:
            print(f"获取K线数据失败：{e}")

        try:
            header_data = get_header_data(symbol)
        except Exception as e:
            print(f"获取基本信息失败：{e}")

        try:
            news = get_news_from_mysql(symbol, limit=20)
        except Exception as e:
            print(f"获取新闻数据失败：{e}")

        try:
            derivatives_data = get_all_derivatives_data(symbol)
        except Exception as e:
            print(f"获取衍生品数据失败：{e}")

        # 检查是否至少有价格数据，否则无法进行分析
        if not kline_data and not header_data:
            return f"{symbol}量化分析报告：\n\n数据获取失败：无法获取K线数据或基本信息。\n\n无法进行量化分析。"

        # 提取K线数据中的价格序列（收盘价）
        close_prices = []
        open_prices = []
        high_prices = []
        low_prices = []

        if kline_data:
            values = kline_data.get("values", [])
            if values:
                # 每个元素格式：[开盘价, 收盘价, 最低价, 最高价]
                for v in values:
                    if len(v) >= 4:
                        try:
                            open_prices.append(float(v[0]))
                            close_prices.append(float(v[1]))
                            low_prices.append(float(v[2]))
                            high_prices.append(float(v[3]))
                        except (ValueError, TypeError):
                            continue

        # 计算技术指标（如果有足够数据）
        indicators = {
            "ma_7": None,
            "ma_30": None,
            "price_change_1d": None,
            "price_change_7d": None,
            "price_change_30d": None,
            "rsi": None,
            "volatility": None,
            "volume": 0
        }

        if close_prices:
            if len(close_prices) >= 30:
                # 计算移动平均线
                if len(close_prices) >= 7:
                    indicators["ma_7"] = sum(close_prices[-7:]) / 7
                if len(close_prices) >= 30:
                    indicators["ma_30"] = sum(close_prices[-30:]) / 30

                # 计算趋势方向
                if len(close_prices) >= 2:
                    indicators["price_change_1d"] = (close_prices[-1] - close_prices[-2]) / close_prices[-2] * 100 if close_prices[-2] != 0 else 0
                if len(close_prices) >= 8:
                    indicators["price_change_7d"] = (close_prices[-1] - close_prices[-8]) / close_prices[-8] * 100 if close_prices[-8] != 0 else 0
                if len(close_prices) >= 31:
                    indicators["price_change_30d"] = (close_prices[-1] - close_prices[-31]) / close_prices[-31] * 100 if close_prices[-31] != 0 else 0

                # 计算RSI（简化版本）
                if len(close_prices) >= 14:
                    gains = []
                    losses = []
                    for i in range(1, min(15, len(close_prices))):
                        change = close_prices[-i] - close_prices[-i-1]
                        if change > 0:
                            gains.append(change)
                            losses.append(0)
                        else:
                            gains.append(0)
                            losses.append(abs(change))

                    avg_gain = sum(gains) / len(gains) if gains else 0
                    avg_loss = sum(losses) / len(losses) if losses else 0

                    if avg_loss > 0:
                        rs = avg_gain / avg_loss
                        indicators["rsi"] = 100 - (100 / (1 + rs))
                    else:
                        indicators["rsi"] = 100

                # 计算波动率（标准差）
                if len(close_prices) >= 20:
                    try:
                        indicators["volatility"] = statistics.stdev(close_prices[-20:]) / statistics.mean(close_prices[-20:]) * 100 if statistics.mean(close_prices[-20:]) != 0 else 0
                    except:
                        indicators["volatility"] = 0

        # 获取成交量数据
        if header_data:
            volume_str = header_data.get("volume", "0")
            try:
                indicators["volume"] = float(volume_str.replace(",", ""))
            except:
                indicators["volume"] = 0

        # 提取衍生品数据
        derivatives_info = {}
        if derivatives_data:
            but_sell_ratio = derivatives_data.get("but_sell_ratio", {})
            open_interest = derivatives_data.get("open_interest", {})
            funding_rate = derivatives_data.get("funding_rate", {})

            # 格式化买卖比例数据
            if but_sell_ratio:
                for exchange, data in but_sell_ratio.items():
                    if data and isinstance(data, dict) and data.get("code") == 0:
                        actual_data = data.get("data", {})
                        if isinstance(actual_data, dict):
                            short_data = actual_data.get("shortData", [])
                            long_data = actual_data.get("longData", [])
                            if short_data and long_data:
                                derivatives_info[f"{exchange}_short_ratio"] = short_data[-1] if short_data else 0
                                derivatives_info[f"{exchange}_long_ratio"] = long_data[-1] if long_data else 0

            # 格式化资金费率
            if funding_rate:
                derivatives_info["funding_rate"] = funding_rate

        # 量化分析提示词（基于原F3提示词，但包含实际数据）
        data_context = f"""
【{symbol} 数据概览】

1. 价格数据："""

        if header_data:
            data_context += f"""
   - 当前价格：{header_data.get('currentPrice', 'N/A')}
   - 24小时涨跌幅：{header_data.get('priceChangePercentage_24h', 'N/A')}
   - 24小时价格范围：{header_data.get('low_24h', 'N/A')} - {header_data.get('high_24h', 'N/A')}
   - 市值：{header_data.get('marketCap', 'N/A')}，排名：{header_data.get('marketCapRank', 'N/A')}"""
        else:
            data_context += "\n   - 价格数据缺失"

        data_context += f"""

2. 技术指标："""

        if close_prices:
            data_context += f"""
   - 7日均线：{indicators.get('ma_7', 'N/A')}
   - 30日均线：{indicators.get('ma_30', 'N/A')}
   - RSI：{indicators.get('rsi', 'N/A')}
   - 1日涨跌幅：{indicators.get('price_change_1d', 'N/A')}%
   - 7日涨跌幅：{indicators.get('price_change_7d', 'N/A')}%
   - 30日涨跌幅：{indicators.get('price_change_30d', 'N/A')}%
   - 波动率：{indicators.get('volatility', 'N/A')}%
   - 成交量：{indicators.get('volume', 'N/A')}"""
        else:
            data_context += "\n   - 技术指标数据缺失"

        data_context += f"""

3. 衍生品数据："""

        if derivatives_info:
            ratio_info = ', '.join([f'{k}: {v:.3f}' for k, v in derivatives_info.items() if 'ratio' in k])
            data_context += f"""
   - 买卖比例（最新）：{ratio_info if ratio_info else '数据缺失'}
   - 资金费率：{derivatives_info.get('funding_rate', '数据缺失')}"""
        else:
            data_context += "\n   - 衍生品数据缺失"

        data_context += f"""

4. 新闻数据：
   - 相关新闻数量：{len(news)}条"""

        # 根据数据可用性调整提示词
        missing_data_note = ""
        if not kline_data:
            missing_data_note += "⚠️ 缺少K线数据，趋势、动量和波动率因子评估受限\n"
        elif not close_prices:
            missing_data_note += "⚠️ K线数据格式异常，技术指标计算受限\n"

        if not header_data:
            missing_data_note += "⚠️ 缺少基本信息，价格和成交量数据不完整\n"

        if not derivatives_info:
            missing_data_note += "⚠️ 缺少衍生品数据，资金因子评估受限\n"

        if not news:
            missing_data_note += "⚠️ 缺少新闻数据，叙事因子评估受限\n"

        if missing_data_note:
            data_context += f"\n【数据缺失提醒】\n{missing_data_note}"

        prompt = f"""
你是一名机构级加密资产量化研究员，专注于多因子概率建模与风险评估。

你必须严格基于以下【六因子评分模型】和【实际数据】进行分析，
禁止主观猜测、禁止编造数据、禁止脱离已给定信息。

====================
【实际数据】
====================
{data_context}

====================
【六因子量化评分模型】
====================

请基于以上实际数据，分别对以下六个因子进行量化打分：

1. 趋势因子（Trend Factor）   ：-2 ~ +2
   - 均线方向（7日 vs 30日均线）
   - 价格结构（短期、中期趋势）
   - 趋势通道状态

2. 动量因子（Momentum Factor）：-2 ~ +2
   - RSI 区间（超买/超卖状态）
   - 近期涨跌强度（1日、7日、30日涨跌幅）
   - 动量延续性

3. 成交量因子（Volume Factor）：-2 ~ +2
   - 成交量水平
   - 量价匹配度
   - 成交活跃度

4. 资金因子（Capital Factor）：-2 ~ +2
   - 主动买卖比（多头/空头比例）
   - 资金费率反映的市场情绪
   - 衍生品市场多空结构

5. 波动率因子（Volatility）：-1 ~ +1
   - 波动率水平
   - 波动扩散/收敛状态
   - 趋势稳定性

6. 叙事因子（Narrative Factor）：-2 ~ +2
   - 新闻情绪（基于新闻数量和内容）
   - 市场关注度
   - 舆论一致性

====================
【评分规则与数据缺失处理】
====================

如果某些数据缺失，请基于可用数据进行合理评估：
1. 趋势因子：如果有价格数据但无均线数据，基于价格涨跌幅评估
2. 动量因子：如果有价格数据但无RSI，基于涨跌幅和价格序列评估
3. 成交量因子：如果成交量数据缺失，给予中性评分（0分）
4. 资金因子：如果衍生品数据缺失，给予中性评分（0分）
5. 波动率因子：如果波动率数据缺失，基于价格变化幅度评估
6. 叙事因子：如果新闻数据缺失，给予中性评分（0分）

====================
【概率映射规则】
====================

Total Score = 六因子得分总和
范围：-11 ~ +11

请严格按下表映射胜率：

Total Score ≥ +7      → 买入胜率 70%~80%
+4 ≤ Score ≤ +6       → 买入胜率 60%~69%
+1 ≤ Score ≤ +3       → 买入胜率 52%~59%
-1 ≤ Score ≤ 0        → 买入胜率 48%~51%
-4 ≤ Score ≤ -2       → 买入胜率 40%~47%
Score ≤ -5            → 买入胜率 30%~39%

卖出胜率 = 100% - 买入胜率

禁止自行修改映射区间。

====================
【分析约束】
====================

1. 禁止给出买卖建议
2. 禁止使用确定性措辞
3. 必须说明评分依据，并引用具体数据
4. 必须强调概率不确定性
5. 必须说明数据缺失对分析的影响
6. 所有结论必须可追溯到因子和数据

====================
【请严格输出以下结构】
====================

【六因子评分表】
- 趋势因子：X / 2 （评分依据：...）
- 动量因子：X / 2 （评分依据：...）
- 成交量因子：X / 2 （评分依据：...）
- 资金因子：X / 2 （评分依据：...）
- 波动率因子：X / 1 （评分依据：...）
- 叙事因子：X / 2 （评分依据：...）

【综合得分】
Total Score = X / 11

【胜率映射结果】
买入胜率：XX%
卖出胜率：XX%

【量化逻辑说明】
（逐条详细解释每个因子为何得分，引用具体数据）

【数据质量说明】
（说明数据完整性对分析的影响）

【综合倾向判断】
（偏多 / 偏空 / 中性，保持克制）

【风险偏好适配说明】
（仅描述适合人群，不给操作建议）
"""

        # 调用LLM
        response = llm_service.call_llm(prompt, lang)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}量化分析报告：\n\n基于六因子评分模型与实际数据\n\n分析结果：\n{response}\n\n已完成{symbol}的量化分析，基于实际数据和六因子评分模型。"


class SummaryAnalysisInput(SymbolAndLangInput):
    """总结分析输入模型"""
    pass


class SummaryAnalysisTool(CryptoAnalystTool):
    """总结分析工具 - 综合所有分析给出最终总结"""

    name: str = "summary_analysis"
    description: str = "综合技术分析、新闻分析、衍生品分析和量化分析，给出加密货币的最终总结。当用户询问综合判断、整体评估、最终结论时使用此工具。"
    args_schema: type = SummaryAnalysisInput

    def execute(self, symbol: str, lang: str = "zh") -> str:
        """执行总结分析"""
        symbol = validate_symbol(symbol)
        lang = validate_language(lang)

        # 总结分析提示词
        if lang == "en":
            prompt = f"""
Summarize the overall outlook of {symbol} based on comprehensive analysis including:
1. Technical analysis (price trends, support/resistance)
2. News analysis (market sentiment, events)
3. Derivatives analysis (long-short structure, funding)
4. Quantitative analysis (probability assessment)

Highlight key risks and uncertainties.
"""
        else:
            prompt = f"""
请对 {symbol} 做最终总结：

- 综合技术分析（价格趋势、支撑阻力）
- 新闻分析（市场情绪、事件影响）
- 衍生品分析（多空结构、资金面）
- 量化分析（概率判断）

输出：
【整体判断】
【关键风险】
【不确定性说明】
"""

        # 调用LLM
        response = llm_service.call_llm(prompt, lang)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}综合总结分析报告：\n\n基于多维度分析\n\n总结结果：\n{response}\n\n已完成{symbol}的综合总结分析。"