from typing import Dict, Any, Generator
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolAndLangInput, SymbolAndQuestionInput
from app.services.data_service import get_kline_data, get_header_data, get_news_from_mysql, get_all_derivatives_data, calculate_technical_indicators
from app.services.llm_service import llm_service
from app.utils.formatters import (
    format_kline_data,
    format_header_data,
    format_news_data,
    format_derivatives_data,
    create_analysis_prompt
)
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

        # 获取实际数据（修复的关键步骤）
        kline_data = get_kline_data(symbol)
        header_data = get_header_data(symbol)
        derivatives_data = get_all_derivatives_data(symbol)
        news = get_news_from_mysql(symbol, limit=15)  # 减少新闻数量节省token

        # 预计算技术指标（方案3）
        technical_indicators = calculate_technical_indicators(kline_data)

        # 提取基础信息（简化）
        formatted_header = format_header_data(header_data, for_llm=True)

        # 格式化衍生品数据（精简版）
        formatted_derivatives = format_derivatives_data(derivatives_data, for_quantitative=False)

        # 格式化新闻（只取标题）
        formatted_news = "\n".join([n.split("｜")[1] if "｜" in n else n for n in news[:10]]) if news else "暂无新闻"

        # 优化的量化分析提示词（方案2 - 精简版本）
        prompt = f"""【{symbol}量化分析】

【核心数据】
价格: ${technical_indicators['current_price']} ({technical_indicators['price_change_pct']:+.2f}%)
MA7: ${technical_indicators['ma7']} MA30: ${technical_indicators['ma30']}
RSI: {technical_indicators['rsi']} 波动率: {technical_indicators['volatility']}%
市值: ${header_data.get('marketCap', 'N/A')} 排名: #{header_data.get('marketCapRank', 'N/A')}

【评分】基于以上数据，对六个因子评分：
1.趋势(-2~+2):方向判断 MA7>MA30=1，RSI<30=1，价格底部=0 → 总分？
2.动量(-2~+2):超买超卖 RSI<30=1，上涨=1，下跌=-1 → 总分？
3.成交量(-2~+2):流动性 市值排名前10=1，前50=0.5，后=-0.5 → 总分？
4.资金(-2~+2):衍生品 多空结构，持仓变化，费率结构 → 总分？
5.波动率(-1~+1):波动性 高>50=0.5，低<20=-0.5，中=0 → 总分？
6.叙事(-2~+2):新闻情绪 新闻数量{len(news)}条，正面/中性/负面 → 总分？

【规则】
总分=六因子之和 范围:-11~+11
胜率映射:≥7→70-80% | 4-6→60-69% | 1-3→52-59% | -1-0→48-51% | -4~-2→40-47% | ≤-5→30-39%

【输出格式】
指标:MA7={technical_indicators['ma7']} MA30={technical_indicators['ma30']} RSI={technical_indicators['rsi']}
评分:趋势X 动量X 成交量X 资金X 波动率X 叙事X
总分:X/11 胜率:买入X% 卖出Y%
判断:偏多/偏空/中性
说明:每个因子评分依据，引用具体数据"""

        # 调用LLM
        response = llm_service.call_llm(prompt, lang)

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{symbol}量化分析报告：\n\n基于六因子评分模型\n\n数据来源：K线数据(30天) + 基础信息 + 衍生品数据 + 新闻数据({len(news)}条)\n\n分析结果：\n{response}\n\n已完成{symbol}的量化分析，基于六因子评分模型。"


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