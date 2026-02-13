from typing import Dict, Any, List
from langchain.tools import BaseTool
from pydantic import BaseModel, Field
from app.agents.tools.base import CryptoAnalystTool, SymbolAndLangInput
from app.utils.validators import validate_symbol, validate_language


class PromptBuilderInput(SymbolAndLangInput):
    """提示词构建输入模型"""
    pass


class PromptBuilderTool(CryptoAnalystTool):
    """提示词构建工具 - 构建各种分析提示词"""

    name: str = "build_analysis_prompt"
    description: str = "构建加密货币分析提示词模板。当需要自定义分析框架或提示词时使用此工具。"
    args_schema: type = PromptBuilderInput

    def execute(self, symbol: str, lang: str = "zh") -> str:
        """执行提示词构建"""
        symbol = validate_symbol(symbol)
        lang = validate_language(lang)

        prompts = self._build_all_prompts(symbol, lang)

        # 格式化提示词为字符串
        formatted_prompts = f"{symbol}分析提示词模板（语言：{lang}）：\n\n"
        for prompt_type, prompt_content in prompts.items():
            formatted_prompts += f"【{prompt_type}】\n{prompt_content}\n\n"

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"{formatted_prompts}已构建{symbol}的各种分析提示词模板。"

    def _build_all_prompts(self, symbol: str, lang: str) -> Dict[str, str]:
        """构建所有提示词"""
        prompts = {}

        # 技术分析提示词
        prompts["technical_analysis"] = self._build_technical_prompt(symbol, lang)

        # 新闻分析提示词
        prompts["news_analysis"] = self._build_news_prompt(symbol, lang)

        # 衍生品分析提示词
        prompts["derivatives_analysis"] = self._build_derivatives_prompt(symbol, lang)

        # 量化分析提示词
        prompts["quantitative_analysis"] = self._build_quantitative_prompt(symbol, lang)

        # 总结提示词
        prompts["summary"] = self._build_summary_prompt(symbol, lang)

        return prompts

    def _build_technical_prompt(self, symbol: str, lang: str) -> str:
        """构建技术分析提示词"""
        if lang == "en":
            return f"""
You are a professional crypto technical analyst. Analyze {symbol} based on the provided data.

Analysis should include:
1. Trend analysis (uptrend/downtrend/sideways)
2. Support and resistance levels
3. Key technical indicators (MA, RSI, etc.)
4. Volume analysis
5. Risk assessment

Provide structured analysis in markdown format.
"""
        else:
            return f"""
你是一位专业的加密货币技术分析师。请基于提供的数据分析{symbol}。

分析应包括：
1. 趋势分析（上升/下降/盘整）
2. 支撑位和阻力位
3. 关键技术指标（移动平均线、RSI等）
4. 成交量分析
5. 风险评估

请以Markdown格式提供结构化分析。
"""

    def _build_news_prompt(self, symbol: str, lang: str) -> str:
        """构建新闻分析提示词"""
        if lang == "en":
            return f"""
Analyze recent news related to {symbol}.

Output format:
[Summary]
[Sentiment Analysis (Bullish/Bearish/Neutral)]
[Potential Impact]
[Risk Notice]
"""
        else:
            return f"""
请分析以下与 {symbol} 相关的近期新闻：

输出格式：
【新闻摘要】
【情绪倾向（利多 / 利空 / 中性）】
【潜在影响】
【风险提示】
"""

    def _build_derivatives_prompt(self, symbol: str, lang: str) -> str:
        """构建衍生品分析提示词"""
        if lang == "en":
            return f"""
Analyze the derivatives market data for {symbol}:

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
            return f"""
以下是 {symbol} 的衍生品与资金面数据（多交易所）：

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

    def _build_quantitative_prompt(self, symbol: str, lang: str) -> str:
        """构建量化分析提示词"""
        # 量化分析提示词较长，使用原F3提示词
        return f"""
你是一名机构级加密资产量化研究员，专注于多因子概率建模与风险评估。

你必须严格基于以下【六因子评分模型】进行分析，
禁止主观猜测、禁止编造数据、禁止脱离已给定信息。

====================
【六因子量化评分模型】
====================

请分别对以下六个因子进行量化打分：

1. 趋势因子（Trend Factor）   ：-2 ~ +2
   - 均线方向
   - 价格结构
   - 趋势通道状态

2. 动量因子（Momentum Factor）：-2 ~ +2
   - RSI 区间
   - 超买/超卖状态
   - 近期涨跌强度

3. 成交量因子（Volume Factor）：-2 ~ +2
   - 放量有效性
   - 量价匹配度
   - 成交延续性

4. 资金因子（Capital Factor）：-2 ~ +2
   - 主动买卖比
   - 持仓变化
   - 费率结构

5. 波动率因子（Volatility）：-1 ~ +1
   - 波动扩散/收敛
   - 趋势稳定性

6. 叙事因子（Narrative Factor）：-2 ~ +2
   - 新闻情绪
   - 监管风险
   - 项目进展
   - 舆论一致性

====================
【评分计算规则】
====================

Total Score = 六因子得分总和
范围：-11 ~ +11

====================
【概率映射规则】
====================

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
3. 必须说明评分依据
4. 必须强调概率不确定性
5. 所有结论必须可追溯到因子

====================
【请严格输出以下结构】
====================

【六因子评分表】
- 趋势因子：X / 2
- 动量因子：X / 2
- 成交量因子：X / 2
- 资金因子：X / 2
- 波动率因子：X / 1
- 叙事因子：X / 2

【综合得分】
Total Score = X / 11

【胜率映射结果】
买入胜率：XX%
卖出胜率：XX%

【量化逻辑说明】
（逐条解释每个因子为何得分）

【综合倾向判断】
（偏多 / 偏空 / 中性，保持克制）

【风险偏好适配说明】
（仅描述适合人群，不给操作建议）
"""

    def _build_summary_prompt(self, symbol: str, lang: str) -> str:
        """构建总结提示词"""
        if lang == "en":
            return f"""
Summarize the overall outlook of {symbol} based on comprehensive analysis.

Highlight:
1. Key findings from technical analysis
2. News sentiment and impact
3. Derivatives market structure
4. Quantitative probability assessment
5. Major risks and uncertainties
"""
        else:
            return f"""
请对 {symbol} 做最终总结：

- 综合技术分析结果
- 新闻情绪与影响
- 衍生品市场结构
- 量化概率判断
- 主要风险与不确定性

输出：
【整体判断】
【关键风险】
【不确定性说明】
"""


class SystemPromptTool(CryptoAnalystTool):
    """系统提示词工具 - 获取系统提示词"""

    name: str = "get_system_prompt"
    description: str = "获取系统提示词模板，定义AI助手的角色和行为准则。"
    args_schema: type = None  # 无输入参数

    def execute(self) -> str:
        """执行系统提示词获取"""
        zh_prompt = self._build_zh_system_prompt()
        en_prompt = self._build_en_system_prompt()

        # 返回格式化字符串（LangChain工具期望返回字符串）
        return f"系统提示词模板：\n\n中文系统提示词：\n{zh_prompt}\n\n英文系统提示词：\n{en_prompt}\n\n已获取中英文系统提示词模板。"

    def _build_zh_system_prompt(self) -> str:
        """构建中文系统提示词"""
        return (
            "你是一位专业、谨慎、遵守合规要求的加密货币分析师。\n"
            "你的职责是基于提供的数据进行客观分析，不提供投资建议或确定性结论。\n"
            "所有分析必须基于数据，强调风险，使用谨慎措辞。\n"
            "禁止给出买卖建议、目标价格或任何形式的投资推荐。\n"
            "必须始终提醒用户加密货币市场的高风险性和高波动性。"
        )

    def _build_en_system_prompt(self) -> str:
        """构建英文系统提示词"""
        return (
            "You are a professional, cautious, compliance-aware crypto analyst.\n"
            "Your role is to provide objective analysis based on provided data, "
            "not to give investment advice or definitive conclusions.\n"
            "All analysis must be data-driven, risk-aware, and use cautious language.\n"
            "Do NOT provide buy/sell recommendations, price targets, or any form of investment advice.\n"
            "Always remind users of the high risk and volatility in cryptocurrency markets."
        )