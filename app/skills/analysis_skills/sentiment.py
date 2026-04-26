"""情绪分析 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_buy_sell_ratio,
    get_open_interest,
    get_trading_volume,
    get_funding_rate
)


class SentimentAnalysisSkill(BaseSkill):
    """情绪分析 Skill - 市场情绪、多空结构"""

    name = "sentiment_analysis"
    description = "情绪分析（市场情绪、多空结构）"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return intent.intent_type == "analyze_sentiment"

    def get_required_apis(self) -> list:
        """需要调用衍生品相关的 API"""
        return [
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_trading_volume",
            "get_funding_rate"
        ]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行分析（并发调用多个 API）"""
        # 并发调用所有相关 API
        tasks = [
            asyncio.to_thread(get_buy_sell_ratio, symbol),
            asyncio.to_thread(get_open_interest, symbol),
            asyncio.to_thread(get_trading_volume, symbol),
            asyncio.to_thread(get_funding_rate, symbol)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 解析结果
        data = {}
        api_calls = []

        api_names = [
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_trading_volume",
            "get_funding_rate"
        ]

        for api_name, result in zip(api_names, results):
            if not isinstance(result, Exception):
                data[api_name] = result
                api_calls.append(api_name)

        # 进行情绪分析
        sentiment = self._analyze_sentiment(data)

        return SkillResult(
            skill_name=self.name,
            data={
                **data,
                "sentiment": sentiment
            },
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _analyze_sentiment(self, data: dict) -> dict:
        """分析市场情绪"""
        sentiment = {
            "overall": "neutral",
            "long_short_ratio": "50:50",
            "signals": []
        }

        # 分析买卖比例
        if "get_buy_sell_ratio" in data:
            ratio_data = data["get_buy_sell_ratio"]
            if ratio_data:
                # 假设 ratio_data 是一个字典，包含 buy_ratio 和 sell_ratio
                buy_ratio = ratio_data.get("buy_ratio", 0.5)
                if buy_ratio > 0.6:
                    sentiment["overall"] = "bullish"
                    sentiment["signals"].append("多头占优")
                    sentiment["long_short_ratio"] = f"{int(buy_ratio * 100)}:{int((1 - buy_ratio) * 100)}"
                elif buy_ratio < 0.4:
                    sentiment["overall"] = "bearish"
                    sentiment["signals"].append("空头占优")
                    sentiment["long_short_ratio"] = f"{int(buy_ratio * 100)}:{int((1 - buy_ratio) * 100)}"

        # 分析资金费率
        if "get_funding_rate" in data:
            funding_rate = data["get_funding_rate"]
            if funding_rate > 0:
                sentiment["signals"].append("多头溢价 (funding rate > 0)")
            elif funding_rate < 0:
                sentiment["signals"].append("空头溢价 (funding rate < 0)")

        # 分析持仓量变化
        if "get_open_interest" in data:
            open_interest = data["get_open_interest"]
            sentiment["signals"].append(f"持仓量: {open_interest}")

        return sentiment
