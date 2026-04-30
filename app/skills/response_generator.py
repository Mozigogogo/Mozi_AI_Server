"""回答生成器 - 语言跟随用户"""
from typing import Dict, Any, AsyncGenerator

from openai import AsyncOpenAI

from app.skills.base import SkillResult, IntentInfo
from app.core.config import get_settings

settings = get_settings()


class ResponseGenerator:
    """回答生成器 - 使用用户语言生成回答"""

    def __init__(self, openai_client: AsyncOpenAI = None):
        self.client = openai_client or AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_api_base
        )
        self.templates = self._get_prompt_templates()

    def _get_prompt_templates(self) -> Dict[str, Dict[str, str]]:
        """获取不同语言的 Prompt 模板"""
        return {
            "zh": {
                "chat": """你是一个友好的加密货币分析助手。请用简洁、友好的中文回答用户的问题。

用户问题：{question}
数据时间：{timestamp}
获取的数据：
{data}

回答要求：
{answer_requirements}

请直接回答，不超过 150 字。
在回答末尾，请简要说明：以上分析仅供参考，不构成投资建议。""",

                "think": """你是一个专业且友好的加密货币分析师。请用中文进行深度分析。

用户问题：{question}
数据时间：{timestamp}
获取的数据：
{data}

分析要求：
{answer_requirements}

【🚨 重要约束（必须严格遵守）】
1. **严格只分析用户问题中明确提到的币种**，绝对禁止添加任何其他币种的分析
2. **绝对禁止进行币种对比**，即使新闻中提到其他币种，也只专注于用户询问的币种
3. 如果获取的数据为空或无效，请基于通用技术分析框架进行分析，但**仅限于用户询问的币种**
4. 不要虚构数据或推测其他币种的情况
5. **对于涨跌幅/价格变化查询，必须严格使用提供的涨跌幅数据**：
   - 如果数据中包含 priceChange_24h、priceChangePercentage_24h、marketCapChange_24h 等字段，必须在回答中准确引用这些数值
   - 不要自行计算涨跌幅或基于价格范围推测百分比
   - 例如：如果 priceChangePercentage_24h 为 1.25%，回答中必须明确说明\"24小时涨幅为 1.25%\"
6. 对于趋势查询，使用价格走势、K线数据等技术形态分析

请提供结构化的深度分析（500-800字）。

重要提示：
1. 请在分析末尾添加"风险提示"部分，说明加密货币投资的高风险特性
2. 明确声明"以上分析仅供参考，不构成任何形式的投资建议"
3. 提醒用户请根据自身情况独立判断并谨慎决策
4. 表达方式请避免过于绝对的判断语气""",

                "quantitative": """你是一名机构级加密资产量化研究员，专注于多因子概率建模与风险评估。

请严格按照以下【六因子评分模型】进行分析，禁止主观猜测、禁止编造数据、禁止脱离已给定信息。

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
6. **严格只分析用户问题中明确提到的币种，绝对禁止添加任何其他币种的分析**
7. **绝对禁止进行币种对比或提及其他币种的数据**
====================
【量化分析数据】
====================
数据时间：{timestamp}
六因子评分结果：
{data}
====================
【请严格按以下Markdown格式输出】
====================

## 📊 【六因子评分表】

| 因子 | 得分 | 满分 | 评价 |
|------|------|------|------|
| 📈 趋势因子 | [填入得分] | 2 | [填入简短评价] |
| ⚡ 动量因子 | [填入得分] | 2 | [填入简短评价] |
| 💧 成交量因子 | [填入得分] | 2 | [填入简短评价] |
| 💰 资金因子 | [填入得分] | 2 | [填入简短评价] |
| 📊 波动率因子 | [填入得分] | 1 | [填入简短评价] |
| 📰 叙事因子 | [填入得分] | 2 | [填入简短评价] |

---

## 🎯 【综合得分】

### **Total Score = [填入总分] / 11**

> 得分区间说明：
> - +7 ~ +11: 🟢 **强烈看多** (买入胜率 70%~80%)
> - +4 ~ +6: 🟡 **看多** (买入胜率 60%~69%)
> - +1 ~ +3: 🔵 **偏多** (买入胜率 52%~59%)
> - -1 ~ 0: ⚪ **中性** (买入胜率 48%~51%)
> - -4 ~ -2: 🟠 **偏空** (买入胜率 40%~47%)
> - -11 ~ -5: 🔴 **强烈看空** (买入胜率 30%~39%)

---

## 📈 【胜率映射结果】

| 方向 | 胜率 |
|------|------|
| **买入胜率** | [填入买入胜率]% |
| **卖出胜率** | [填入卖出胜率]% |

> 💡 提示：胜率表示历史数据支持的概率，不代表未来保证

---

## 📝 【量化逻辑说明】

[逐条解释每个因子为何得分，每条1-2句话]

---

## ⚖️ 【综合倾向判断】

[偏多 / 偏空 / 中性，保持克制]

---

## 👥 【风险偏好适配说明】

[仅描述适合人群，不给操作建议]

---

## ⚠️ 【风险提示】

> ⚠️ **重要声明**
>
> 1. 以上分析仅供参考，不构成任何形式的投资建议
> 2. 加密货币市场波动剧烈，存在本金全部损失的风险
> 3. 请根据自身风险承受能力和投资目标独立判断
> 4. 建议在投资前进行充分的尽职调查
> 5. 过往表现不预示未来结果"""
            },
            "en": {
                "chat": """You are a friendly cryptocurrency analysis assistant. Please answer the user's question concisely and friendly in English.

Question: {question}
Data timestamp: {timestamp}
Retrieved data:
{data}

Answer requirements:
{answer_requirements}

Answer directly, within 150 words.

At the end, please briefly mention: The above analysis is for reference only and does not constitute investment advice.""",

                "think": """You are a professional cryptocurrency analyst. Please provide deep analysis in English.

Question: {question}
Data timestamp: {timestamp}
Retrieved data:
{data}

Analysis requirements:
{answer_requirements}

Important constraints:
1. **Only analyze the cryptocurrency mentioned in the user's question** - do not add analysis for other coins
2. If the retrieved data is empty or invalid, analyze based on general technical analysis framework, but **only for the coin the user asked about**
3. Do not fabricate data or speculate about other cryptocurrencies

Provide structured analysis (500-800 words).

Important: At the end, add a "Risk Warning" section stating that this is for reference only and does not constitute investment advice. Remind users to make independent decisions with caution.""",

                "quantitative": """You are an institutional-level crypto asset quantitative researcher specializing in multi-factor probability modeling and risk assessment.

Please conduct quantitative analysis strictly according to the following [Six-Factor Scoring Model], prohibit subjective guessing, prohibit fabricating data, and prohibit deviating from given information.

====================
【Six-Factor Quantitative Scoring Model】
====================
Please score each of the following six factors quantitatively:
1. Trend Factor: -2 ~ +2
   - Moving average direction
   - Price structure
   - Trend channel status
2. Momentum Factor: -2 ~ +2
   - RSI range
   - Overbought/oversold status
   - Recent trend strength
3. Volume Factor: -2 ~ +2
   - Volume effectiveness
   - Volume-price matching degree
   - Volume continuity
4. Capital Factor: -2 ~ +2
   - Active buy-sell ratio
   - Position change
   - Funding rate structure
5. Volatility Factor: -1 ~ +1
   - Volatility divergence/convergence
   - Trend stability
6. Narrative Factor: -2 ~ +2
   - News sentiment
   - Regulatory risk
   - Project progress
   - Social media consensus
====================
【Scoring Calculation Rules】
====================
Total Score = Sum of six factor scores
Range: -11 ~ +11
====================
【Probability Mapping Rules】
====================
Please strictly map win rates according to the following table:
Total Score ≥ +7      → Buy win rate 70%~80%
+4 ≤ Score ≤ +6       → Buy win rate 60%~69%
+1 ≤ Score ≤ +3       → Buy win rate 52%~59%
-1 ≤ Score ≤ 0        → Buy win rate 48%~51%
-4 ≤ Score ≤ -2       → Buy win rate 40%~47%
Score ≤ -5            → Buy win rate 30%~39%
Sell win rate = 100% - Buy win rate
Prohibit modifying mapping intervals.
====================
【Analysis Constraints】
====================
1. Prohibit giving buy/sell recommendations
2. Prohibit using definitive language
3. Must explain scoring basis
4. Must emphasize probability uncertainty
5. All conclusions must be traceable to factors
====================
【Quantitative Analysis Data】
====================
Data timestamp: {timestamp}
Six-factor scoring results:
{data}
====================
【Please strictly output the following structure in Markdown format】
====================

## 📊 【Six-Factor Scoring Table】

| Factor | Score | Max | Rating |
|--------|-------|-----|--------|
| 📈 Trend | [fill score] | 2 | [fill brief rating] |
| ⚡ Momentum | [fill score] | 2 | [fill brief rating] |
| 💧 Volume | [fill score] | 2 | [fill brief rating] |
| 💰 Capital | [fill score] | 2 | [fill brief rating] |
| 📊 Volatility | [fill score] | 1 | [fill brief rating] |
| 📰 Narrative | [fill score] | 2 | [fill brief rating] |

---

## 🎯 【Total Score】

### **Total Score = [fill total score] / 11**

> Score Range Explanation:
> - +7 ~ +11: 🟢 **Strongly Bullish** (Buy win rate 70%~80%)
> - +4 ~ +6: 🟡 **Bullish** (Buy win rate 60%~69%)
> - +1 ~ +3: 🔵 **Slightly Bullish** (Buy win rate 52%~59%)
> - -1 ~ 0: ⚪ **Neutral** (Buy win rate 48%~51%)
> - -4 ~ -2: 🟠 **Slightly Bearish** (Buy win rate 40%~47%)
> - -11 ~ -5: 🔴 **Strongly Bearish** (Buy win rate 30%~39%)

---

## 📈 【Win Rate Mapping Result】

| Direction | Win Rate |
|-----------|----------|
| **Buy** | [fill buy win rate]% |
| **Sell** | [fill sell win rate]% |

> 💡 Note: Win rate indicates historical data-supported probability, does not guarantee future results

---

## 📝 【Quantitative Logic Explanation】

[Explain why each factor scored point by point, 1-2 sentences per factor]

---

## ⚖️ 【Overall Tendency Judgment】

[Bullish biased / Bearish biased / Neutral, maintain restraint]

---

## 👥 【Risk Preference Adaptation】

[Only describe suitable population, no operation recommendations]

---

## ⚠️ 【Risk Warning】

> ⚠️ **Important Declaration**
>
> 1. The above analysis is for reference only and does not constitute any form of investment advice
> 2. The cryptocurrency market is highly volatile and carries the risk of total loss
> 3. Please make independent judgments based on your own risk tolerance and investment goals
> 4. It is recommended to conduct sufficient due diligence before investing
> 5. Past performance does not guarantee future results"""
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
            mode: 模式（chat/think/quantitative）

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

            # 调用 LLM 生成回答（添加超时设置）
            # 使用配置中的 token 限制
            if mode == "quantitative":
                max_tokens = 1200  # 量化分析需要更多 tokens，但限制token使用
                timeout_seconds = 60.0  # 量化分析需要更长时间
            elif mode == "think":
                max_tokens = 1000  # 深度分析模式使用中等 tokens
                timeout_seconds = 45.0  # 深度分析需要较长时间
            else:
                max_tokens = 600  # 简洁对话模式使用较少 tokens
                timeout_seconds = 20.0  # 普通对话 20 秒

            response = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                max_tokens=max_tokens,
                timeout=timeout_seconds,
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            response_text = response.choices[0].message.content.strip()
            return response_text

        except Exception as e:
            print(f"回答生成失败: {e}")
            # 返回错误消息
            if intent.language == "zh":
                return f"抱歉，生成回答时出错：{str(e)}"
            else:
                return f"Sorry, error generating response: {str(e)}"

    async def generate_response_stream(
        self,
        skill_result: SkillResult,
        intent: IntentInfo,
        mode: str = "chat"
    ) -> AsyncGenerator[str, None]:
        """
        流式生成回答（使用用户语言）

        Args:
            skill_result: Skill 执行结果
            intent: 意图信息
            mode: 模式（chat/think/quantitative）

        Yields:
            str: 流式回答内容
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

            # 设置 token 限制
            if mode == "quantitative":
                max_tokens = 1200
                timeout_seconds = 60.0
            elif mode == "think":
                max_tokens = 1000
                timeout_seconds = 45.0
            else:
                max_tokens = 600
                timeout_seconds = 20.0

            # 流式调用 LLM
            stream = await self.client.chat.completions.create(
                model=settings.deepseek_model,
                max_tokens=max_tokens,
                timeout=timeout_seconds,
                messages=[
                    {"role": "user", "content": prompt}
                ],
                stream=True  # 启用流式输出
            )

            # 流式输出
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            print(f"流式回答生成失败: {e}")
            # 返回错误消息
            if intent.language == "zh":
                yield f"抱歉，生成回答时出错：{str(e)}"
            else:
                yield f"Sorry, error generating response: {str(e)}"

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
