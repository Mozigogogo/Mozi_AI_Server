"""LLM 智能分析 - 异动信号解读"""
import json
import asyncio
from typing import Optional
from openai import AsyncOpenAI
from app.bigorder.models import AnomalySignal, SignalLevel
from app.core.llm_client import get_llm_client
from config.settings import settings
from app.utils.logger import get_logger

logger = get_logger("app.bigorder.llm_analyzer")


class LLMAnalyzer:
    """调用 DeepSeek 对异动信号生成智能解读"""

    def __init__(self):
        # 使用共享 LLM 客户端，但模型用 bigorder 专属配置
        self.client = get_llm_client()
        self.model = settings.bigorder_deepseek_model

    def _get_prompt(self, lang: str = "zh") -> str:
        if lang == "en":
            return """You are a cryptocurrency large-trade anomaly analysis expert. Based on the following anomaly signal data, generate a concise and professional interpretation.

Signal Data:
{signal_data}

Output Requirements (strictly follow):
1. Direction: Active buying or active selling (1 sentence)
2. Analysis: Possible reasons based on four-dimensional scores (2-3 sentences)
3. Impact: Potential short-term price impact (1-2 sentences)
4. Advice: Data-driven rational suggestion (1 sentence)

Format:
🔴/🟡 【{coin} {level} Signal】
📊 Direction: ...
🔍 Analysis: ...
⚡ Impact: ...
💡 Advice: ...

Keep under 200 words. Do not mention other coins. Do not fabricate data."""
        return """你是加密货币大单异动分析专家。根据以下异动信号数据，生成简洁专业的解读。

信号数据：
{signal_data}

输出要求（严格遵守）：
1. 方向判断：主动买入还是主动卖出（1句话）
2. 异动原因：结合四维得分分析可能原因（2-3句）
3. 影响评估：对短期价格的可能影响（1-2句）
4. 操作建议：基于数据的理性建议（1句）

格式：
🔴/🟡 【{coin} {level}信号】
📊 方向：...
🔍 分析：...
⚡ 影响：...
💡 建议：...

总字数 200 字以内。禁止提及其他币种。禁止编造数据。"""

    async def analyze(self, signal: AnomalySignal, lang: str = "zh") -> str:
        """对信号生成 LLM 解读"""
        s = signal.score
        prompt_template = self._get_prompt(lang)

        if lang == "en":
            summary = {
                "coin": signal.coin,
                "exchange": signal.exchange,
                "total_score": s.total_score,
                "signal_level": "Strong (Red)" if s.level == SignalLevel.STRONG else "Medium (Yellow)",
                "net_flow": f"{signal.net_flow:,.2f} USD (score: {s.net_flow.score})",
                "large_order_density": f"{signal.buy_count + signal.sell_count} trades (score: {s.density.score})",
                "buy_sell_ratio": f"{s.ratio.raw_value:.4f} (score: {s.ratio.score})",
                "price_change_1h": f"{signal.price_change_pct:+.2f}% (score: {s.price_change.score})",
                "buy_amount": f"{signal.buy_amount:,.2f}",
                "sell_amount": f"{signal.sell_amount:,.2f}",
                "Top5_large_orders": [
                    {"side": t.side, "price": t.deal_price, "qty": t.deal_quantity, "amount": f"{t.amount:,.2f}"}
                    for t in signal.top_orders[:5]
                ]
            }
            level_text = "Strong" if s.level == SignalLevel.STRONG else "Medium"
        else:
            summary = {
                "币种": signal.coin,
                "交易所": signal.exchange,
                "综合得分": s.total_score,
                "信号等级": "强烈(红标)" if s.level == SignalLevel.STRONG else "中等(黄标)",
                "净资金流": f"{signal.net_flow:,.2f} USD (得分{s.net_flow.score})",
                "大单密度": f"{signal.buy_count + signal.sell_count}笔 (得分{s.density.score})",
                "买卖比": f"{s.ratio.raw_value:.4f} (得分{s.ratio.score})",
                "近1h价格变化": f"{signal.price_change_pct:+.2f}% (得分{s.price_change.score})",
                "买入金额": f"{signal.buy_amount:,.2f}",
                "卖出金额": f"{signal.sell_amount:,.2f}",
                "Top5大单": [
                    {"side": t.side, "price": t.deal_price, "qty": t.deal_quantity, "amount": f"{t.amount:,.2f}"}
                    for t in signal.top_orders[:5]
                ]
            }
            level_text = "强烈" if s.level == SignalLevel.STRONG else "中等"

        prompt = prompt_template.format(
            signal_data=json.dumps(summary, ensure_ascii=False, indent=2),
            coin=signal.coin,
            level=level_text
        )

        try:
            response = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=500,
                    messages=[{"role": "user", "content": prompt}]
                ),
                timeout=30.0
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"LLM 分析失败: {e}")
            if lang == "en":
                return f"⚠️ LLM analysis unavailable ({signal.coin} total score: {s.total_score})"
            return f"⚠️ LLM解读暂不可用（{signal.coin} 综合得分{s.total_score}）"

    async def analyze_and_enrich(self, signal: AnomalySignal, lang: str = "zh") -> AnomalySignal:
        """分析并回填到 signal"""
        signal.llm_analysis = await self.analyze(signal, lang=lang)
        return signal
