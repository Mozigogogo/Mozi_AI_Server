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
                "chat": """你是加密货币分析助手。用简洁中文回答。

格式要求（严格遵守）：
1. 关键数字必须**加粗**，如价格、涨跌幅、百分比
2. 涨用📈 跌用📉 标注
3. 多个数据点用换行列表展示，每行一个要点
4. 150字以内，末尾加一句风险提示

用户问题：{question}
数据时间：{timestamp}
数据：
{data}
要求：{answer_requirements}""",

                "think": """你是专业加密货币分析师。严格约束：
1. 只分析用户问的币种，禁止提及其他币种
2. 必须引用实时数据中的具体价格、涨跌幅等数值
3. 多空比字段：longShortData=多/空人数比(>1看多,<1看空)；longData=多头占比；shortData=空头占比

问题：{question}
时间：{timestamp}
数据：
{data}
要求：{answer_requirements}

格式要求（严格遵守）：
分3段，每段用###标题+emoji开头。关键数字**加粗**。200-300字。末尾1句风险提示。必须完整不截断。

### 💰 价格趋势
引用实时价格、24h涨跌幅、30天趋势。涨用📈 跌用📉

### 📊 衍生品情绪
多空比用🟢(偏多)/🔴(偏空)/⚪(中性)标注。资金费率正负标注。各交易所数据用换行列表。

### 🎯 综合判断
1-2句总结 + 风险提示""",

                "quantitative": """你是量化研究员。严格约束：
1. 只分析用户问的币种，禁止提及其他币种
2. 必须引用实时价格
3. 禁止给出买卖建议，强调不确定性
4. 多空比：longShortData=多/空人数比(>1看多,<1看空)

时间：{timestamp}
数据：
{data}
要求：{answer_requirements}

格式要求（严格遵守，必须完整不截断）：

## 📊 六因子评分表
| 因子 | 得分 | 满分 | 评价 |
|------|------|------|------|
正分用🟢，负分用🔴，零分用⚪。直接引用数据中scores和explanations。

## 🎯 综合得分
用大字展示：**Total Score = total_score / 11**
胜率区间用数据中的buy_win_rate和sell_win_rate。
附一行得分条形图：用██表示正分，用░░表示负分，共11格。

## 📝 量化逻辑
逐条解释每个因子为何得分，每条1句话。引用实时价格。

## ⚖️ 综合倾向
用数据中的tendency。1-2句话。偏多用🟢，偏空用🔴，中性用⚪。

## ⚠️ 风险提示
1句。""",
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

                "think": """You are a professional cryptocurrency analyst. Analyze in English. Constraints:
1. Only analyze the coin mentioned, never mention other coins
2. Must cite specific price, change percentages from the real-time data. Never say "data missing"
3. Ratio fields: longShortData=long/short ratio (>1 bullish, <1 bearish); longData=long %; shortData=short %. Do not confuse them

Question: {question}
Time: {timestamp}
Data:
{data}

Requirements: {answer_requirements}

200-300 words concise analysis, 3 sections: price trend, derivatives sentiment, overall judgment. 3-4 sentences per section. End with 1 risk disclaimer sentence. Must be complete, no truncation.""",

                "quantitative": """You are a quantitative researcher analyzing six-factor scoring data. Constraints:
1. Only analyze the coin mentioned, never mention other coins
2. Must cite specific price from real-time data
3. Never give buy/sell recommendations, emphasize uncertainty
4. Ratio fields: longShortData=long/short ratio (>1 bullish, <1 bearish)

Time: {timestamp}
Data:
{data}
Requirements: {answer_requirements}

Output format (must be complete, no truncation):

## 📊 Six-Factor Scoring Table
| Factor | Score | Max | Rating |
Use scores and explanations from data directly.

## 🎯 Total Score
Total Score = total_score from data / 11. Win rates from buy_win_rate and sell_win_rate.

## 📝 Quantitative Logic
Explain each factor's score in 1-2 sentences. Cite real-time price.

## ⚖️ Overall Tendency
Use tendency from data. 1-2 sentences.

## ⚠️ Risk Disclaimer
For reference only, not investment advice. Crypto is volatile."""
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
                max_tokens = 1500
                timeout_seconds = 60.0
            elif mode == "think":
                max_tokens = 1200
                timeout_seconds = 45.0
            else:
                max_tokens = 600
                timeout_seconds = 20.0

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
        流式生成回答（使用用户语言，带总超时控制）

        Args:
            skill_result: Skill 执行结果
            intent: 意图信息
            mode: 模式（chat/think/quantitative）

        Yields:
            str: 流式回答内容
        """
        import asyncio

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

            # 设置 token 限制和总超时
            if mode == "quantitative":
                max_tokens = 1500
                timeout_seconds = 60.0
            elif mode == "think":
                max_tokens = 1200
                timeout_seconds = 50.0
            else:
                max_tokens = 600
                timeout_seconds = 30.0

            # 流式调用 LLM（带空响应重试，最多2次）
            for attempt in range(2):
                try:
                    stream = await asyncio.wait_for(
                        self.client.chat.completions.create(
                            model=settings.deepseek_model,
                            max_tokens=max_tokens,
                            timeout=timeout_seconds,
                            messages=[{"role": "user", "content": prompt}],
                            stream=True
                        ),
                        timeout=timeout_seconds + 5
                    )

                    has_content = False
                    async for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta.content:
                            has_content = True
                            yield chunk.choices[0].delta.content

                    if has_content:
                        return
                    elif attempt == 0:
                        print(f"  ⚠️ LLM流式响应为空，重试...")
                except asyncio.TimeoutError:
                    print(f"  ⚠️ LLM响应超时({timeout_seconds}s)，{'重试...' if attempt == 0 else '切换兜底'}")

            # 所有流式都失败，非流式兜底
            print(f"  ⚠️ LLM流式失败，切换非流式兜底...")
            try:
                fallback = await asyncio.wait_for(
                    self.client.chat.completions.create(
                        model=settings.deepseek_model,
                        max_tokens=max_tokens,
                        timeout=timeout_seconds,
                        messages=[{"role": "user", "content": prompt}],
                        stream=False
                    ),
                    timeout=timeout_seconds + 5
                )
                text = fallback.choices[0].message.content.strip()
                if text:
                    yield text
                    return
            except (asyncio.TimeoutError, Exception) as fe:
                print(f"  ❌ 非流式兜底失败: {fe}")

            yield "抱歉，生成回答时出现问题，请重新提问。" if intent.language == "zh" else "Sorry, an error occurred. Please try again."

        except Exception as e:
            print(f"流式回答生成失败: {e}")
            if intent.language == "zh":
                yield "抱歉，生成回答时出现问题，请重新提问。"
            else:
                yield "Sorry, an error occurred. Please try again."

    def _format_data(self, data: Any) -> str:
        """格式化数据为可读文本"""
        if isinstance(data, dict):
            # 特殊处理衍生品数据结构（包含exchanges和data字段）
            if "exchanges" in data and "data" in data:
                formatted_parts = []
                formatted_parts.append(f"币种: {data.get('coin', 'N/A')}")

                # 处理metric，如果有的话
                metric = data.get('metric', 'N/A')
                if metric != 'N/A':
                    formatted_parts.append(f"指标: {metric}")

                # 提取各交易所最新数据（过滤null值和异常值）
                nested_data = data.get("data", {})
                if isinstance(nested_data, dict):
                    # 清理unit，去掉"(bar)"后缀
                    unit = data.get('unit', '')
                    if '(bar)' in unit:
                        unit = unit.replace('(bar)', '').strip()

                    formatted_parts.append(f"\n各交易所最新数据 (30天):")
                    valid_exchanges = []

                    for exchange, values in nested_data.items():
                        if isinstance(values, list) and values:
                            # 找到最新的非null值
                            latest_value = None
                            for val in reversed(values):
                                if val is not None and not (isinstance(val, float) and (val > 10000 or val < 0)):
                                    latest_value = val
                                    break

                            if latest_value is not None:
                                valid_exchanges.append((exchange, latest_value))

                    # 按成交量排序，只显示前10个有数据的交易所
                    valid_exchanges.sort(key=lambda x: x[1], reverse=True)
                    for exchange, value in valid_exchanges[:10]:
                        formatted_parts.append(f"  - {exchange}: {value} {unit}")

                    if len(valid_exchanges) > 10:
                        formatted_parts.append(f"  ... 以及其他{len(valid_exchanges)-10}个交易所")

                return "\n".join(formatted_parts)

            # 处理普通字典
            items = []
            for key, value in data.items():
                if isinstance(value, (int, float)):
                    items.append(f"{key}: {value}")
                elif isinstance(value, dict):
                    # 检测多空比数据: {exchange: {longShortData:[], shortData:[], longData:[]}}
                    dict_values = [v for v in value.values() if isinstance(v, dict)]
                    is_exchange_ratio = (
                        len(dict_values) > 0 and
                        all(any(k2.endswith('Data') for k2 in v) for v in dict_values)
                    )
                    nested_items = []
                    if is_exchange_ratio:
                        nested_items.append("  【多空比数据说明: longShortData=多/空人数比(>1看多,<1看空); longData=多头占比; shortData=空头占比】")
                        for exchange, exchange_data in value.items():
                            if isinstance(exchange_data, dict):
                                ls = exchange_data.get('longShortData', [])
                                nested_items.append(f"  {exchange} 多空比={ls[-1] if ls else 'N/A'}(近5日:{ls[-5:] if len(ls)>=5 else ls})")
                                short = exchange_data.get('shortData', [])
                                long_d = exchange_data.get('longData', [])
                                if short:
                                    nested_items.append(f"    多头占比={long_d[-1] if long_d else 'N/A'}, 空头占比={short[-1]}")
                    else:
                        for nk, nv in value.items():
                            if isinstance(nv, (int, float)):
                                nested_items.append(f"  {nk}: {nv}")
                            elif isinstance(nv, list) and nv:
                                if nk == 'longShortData':
                                    nested_items.append(f"  多空比(longShortData): 最新={nv[-1]}, 5日={[nv[i] for i in range(-5,0)]}")
                                elif nk == 'shortData':
                                    nested_items.append(f"  空头占比(shortData): 最新={nv[-1]}, 5日={[nv[i] for i in range(-5,0)]}")
                                elif nk == 'longData':
                                    nested_items.append(f"  多头占比(longData): 最新={nv[-1]}, 5日={[nv[i] for i in range(-5,0)]}")
                                elif nk == 'xAxisData':
                                    nested_items.append(f"  日期范围: {nv[0]} ~ {nv[-1]}")
                                else:
                                    nested_items.append(f"  {nk}: [{len(nv)} items, latest: {nv[-1]}]")
                            elif isinstance(nv, dict) and "exchanges" in nv:
                                nested_items.append(f"  {nk}: 包含 {len(nv.get('exchanges', []))} 个交易所的数据")
                            elif isinstance(nv, dict):
                                # 展开简单 dict（如资金费率的 exchanges）
                                for dk, dv in nv.items():
                                    nested_items.append(f"    {dk}: {dv}")
                            else:
                                nv_str = str(nv)
                                if len(nv_str) > 100:
                                    nv_str = nv_str[:100] + "..."
                                nested_items.append(f"  {nk}: {nv_str}")
                    items.append(f"{key}:")
                    items.extend(nested_items[:20])
                elif isinstance(value, list):
                    if value:
                        latest = value[-1] if len(value) > 0 else "N/A"
                        items.append(f"{key}: [{len(value)} items, latest: {latest}]")
                    else:
                        items.append(f"{key}: []")
                else:
                    value_str = str(value)
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
