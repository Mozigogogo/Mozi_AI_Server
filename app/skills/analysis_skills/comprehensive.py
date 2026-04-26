"""综合分析 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_header_data,
    get_kline_data,
    get_recent_news,
    get_buy_sell_ratio,
    get_open_interest,
    get_funding_rate
)


class ComprehensiveAnalysisSkill(BaseSkill):
    """综合分析 Skill - 多维度全面分析"""

    name = "comprehensive_analysis"
    description = "综合分析（多维度全面分析）"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return intent.intent_type == "analyze_comprehensive"

    def get_required_apis(self) -> list:
        """需要调用所有相关的 API"""
        return [
            "get_header_data",
            "get_kline_data",
            "get_recent_news",
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_funding_rate"
        ]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行综合分析（并发调用多个 API）"""
        # 并发调用所有相关 API
        tasks = [
            asyncio.to_thread(get_header_data, symbol),
            asyncio.to_thread(get_kline_data, symbol),
            asyncio.to_thread(get_recent_news, symbol, limit=5),
            asyncio.to_thread(get_buy_sell_ratio, symbol),
            asyncio.to_thread(get_open_interest, symbol),
            asyncio.to_thread(get_funding_rate, symbol)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 解析结果
        data = {}
        api_calls = []

        api_names = [
            "get_header_data",
            "get_kline_data",
            "get_recent_news",
            "get_buy_sell_ratio",
            "get_open_interest",
            "get_funding_rate"
        ]

        for api_name, result in zip(api_names, results):
            if not isinstance(result, Exception):
                data[api_name] = result
                api_calls.append(api_name)

        # 进行综合分析
        analysis = self._comprehensive_analysis(data)

        return SkillResult(
            skill_name=self.name,
            data={
                "raw_data": data,
                "analysis": analysis
            },
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _comprehensive_analysis(self, data: dict) -> dict:
        """综合分析"""
        analysis = {
            "basic_info": {},
            "trend": {},
            "sentiment": {},
            "news": {},
            "overall_assessment": "neutral"
        }

        # 基本面分析
        if "get_header_data" in data:
            header_data = data["get_header_data"]
            if header_data:
                analysis["basic_info"] = {
                    "price": header_data.get("price"),
                    "market_cap": header_data.get("market_cap"),
                    "rank": header_data.get("rank")
                }

        # 趋势分析
        if "get_kline_data" in data:
            kline_data = data["get_kline_data"]
            if kline_data and len(kline_data) > 1:
                latest = kline_data[-1]
                earliest = kline_data[0]

                close_latest = latest.get("close", 0)
                close_earliest = earliest.get("close", close_latest)

                if close_earliest > 0:
                    change_percent = ((close_latest - close_earliest) / close_earliest) * 100
                    analysis["trend"] = {
                        "current_price": close_latest,
                        "change_percent": round(change_percent, 2),
                        "direction": "up" if change_percent > 0 else "down"
                    }

        # 情绪分析
        bullish_signals = 0
        bearish_signals = 0

        if "get_buy_sell_ratio" in data:
            ratio_data = data["get_buy_sell_ratio"]
            if ratio_data:
                buy_ratio = ratio_data.get("buy_ratio", 0.5)
                if buy_ratio > 0.5:
                    bullish_signals += 1
                else:
                    bearish_signals += 1
                analysis["sentiment"]["buy_sell_ratio"] = buy_ratio

        if "get_funding_rate" in data:
            funding_rate = data["get_funding_rate"]
            if funding_rate > 0:
                bullish_signals += 1
                analysis["sentiment"]["funding_rate"] = "positive"
            elif funding_rate < 0:
                bearish_signals += 1
                analysis["sentiment"]["funding_rate"] = "negative"

        if "get_open_interest" in data:
            analysis["sentiment"]["open_interest"] = data["get_open_interest"]

        # 综合判断
        if bullish_signals > bearish_signals:
            analysis["overall_assessment"] = "bullish"
        elif bearish_signals > bullish_signals:
            analysis["overall_assessment"] = "bearish"
        else:
            analysis["overall_assessment"] = "neutral"

        # 新闻分析
        if "get_recent_news" in data:
            news_data = data["get_recent_news"]
            if isinstance(news_data, list):
                analysis["news"] = {
                    "count": len(news_data),
                    "latest": news_data[0] if news_data else None
                }

        return analysis
