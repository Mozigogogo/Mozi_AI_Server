"""LLM 智能分析 - 异动信号解读"""
import json
import asyncio
from typing import Optional
from openai import AsyncOpenAI
from app.bigorder.models import AnomalySignal, SignalLevel
from app.core.llm_client import get_llm_client
from config.settings import settings


class LLMAnalyzer:
    """调用 DeepSeek 对异动信号生成智能解读"""

    def __init__(self):
        # 使用共享 LLM 客户端，但模型用 bigorder 专属配置
        self.client = get_llm_client()
        self.model = settings.bigorder_deepseek_model
        self.prompt_template = self._get_prompt()

    def _get_prompt(self) -> str:
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

    async def analyze(self, signal: AnomalySignal) -> str:
        """对信号生成 LLM 解读"""
        s = signal.score
        summary = {
            "币种": signal.coin,
            "交易所": signal.exchange,
            "综合得分": s.total_score,
            "信号等级": "强烈(红标)" if s.level == SignalLevel.STRONG else "中等(黄标)",
            "净资金流": f"{signal.net_flow:,.2f} USD (得分{s.net_flow.score})",
            "大单密度": f"{signal.buy_count + signal.sell_count}笔 (得分{s.density.score})",
            "买卖比": f"{s.ratio.raw_value:.4f} (得分{s.ratio.score})",
            "价格变化": f"{signal.price_change_pct:+.2f}% (得分{s.price_change.score})",
            "买入金额": f"{signal.buy_amount:,.2f}",
            "卖出金额": f"{signal.sell_amount:,.2f}",
            "Top5大单": [
                {"side": t.side, "price": t.deal_price, "qty": t.deal_quantity, "amount": f"{t.amount:,.2f}"}
                for t in signal.top_orders[:5]
            ]
        }

        prompt = self.prompt_template.format(
            signal_data=json.dumps(summary, ensure_ascii=False, indent=2),
            coin=signal.coin,
            level="强烈" if s.level == SignalLevel.STRONG else "中等"
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
            print(f"LLM 分析失败: {e}")
            return f"⚠️ LLM解读暂不可用（{signal.coin} 综合得分{s.total_score}）"

    async def analyze_and_enrich(self, signal: AnomalySignal) -> AnomalySignal:
        """分析并回填到 signal"""
        signal.llm_analysis = await self.analyze(signal)
        return signal
