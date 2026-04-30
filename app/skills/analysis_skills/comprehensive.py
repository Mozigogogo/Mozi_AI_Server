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
            else:
                print(f"  警告: {api_name} 调用失败: {str(result)}")
                # 记录失败，但不中断处理

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

        # 基本面分析（优先使用header_data的准确24小时数据）
        if "get_header_data" in data:
            header_data = data["get_header_data"]
            if header_data:
                # 提取准确的24小时数据
                current_price = header_data.get("currentPrice")
                price_change_24h = header_data.get("priceChange_24h")
                price_change_percent_24h = header_data.get("priceChangePercentage_24h")
                high_24h = header_data.get("high_24h")
                low_24h = header_data.get("low_24h")

                # 转换为数值（如果需要）
                try:
                    current_price = float(current_price) if current_price else 0
                    price_change_24h = float(price_change_24h) if price_change_24h else 0
                except (ValueError, TypeError):
                    current_price = 0
                    price_change_24h = 0

                analysis["basic_info"] = {
                    "price": current_price,
                    "price_change_24h": price_change_24h,
                    "price_change_percent_24h": price_change_percent_24h,
                    "high_24h": high_24h,
                    "low_24h": low_24h,
                    "market_cap": header_data.get("marketCap"),
                    "rank": header_data.get("marketCapRank")
                }

                # 趋势分析（使用准确的24小时数据）
                analysis["trend"] = {
                    "current_price": current_price,
                    "change_24h": price_change_24h,
                    "change_percent_24h": price_change_percent_24h,
                    "high_24h": high_24h,
                    "low_24h": low_24h,
                    "direction": "up" if price_change_24h > 0 else "down"
                }

        # K线数据分析（补充长期趋势）
        if "get_kline_data" in data:
            kline_data = data["get_kline_data"]
            if kline_data and isinstance(kline_data, dict) and "values" in kline_data:
                values = kline_data["values"]
                if values and len(values) > 1:
                    latest = values[-1]
                    earliest = values[0]
                    # values 格式: [open, high, low, close] - 修复索引错误
                    close_latest = float(latest[3]) if len(latest) > 3 else 0
                    close_earliest = float(earliest[3]) if len(earliest) > 3 else close_latest

                    if close_earliest > 0:
                        # 这是30天趋势，不是24小时趋势
                        long_term_change_percent = ((close_latest - close_earliest) / close_earliest) * 100
                        analysis["trend"]["long_term_change_percent"] = round(long_term_change_percent, 2)

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
            funding_rate_data = data["get_funding_rate"]
            # funding_rate 是字典，包含 exchanges 键
            if isinstance(funding_rate_data, dict) and "exchanges" in funding_rate_data:
                exchanges = funding_rate_data["exchanges"]
                # 计算平均资金费率
                if exchanges:
                    rates = []
                    for exchange, rate_str in exchanges.items():
                        # 解析费率字符串，如 "-0.0002%"
                        try:
                            rate = float(rate_str.replace("%", ""))
                            rates.append(rate)
                        except (ValueError, AttributeError):
                            continue

                    if rates:
                        avg_rate = sum(rates) / len(rates)
                        if avg_rate > 0:
                            bullish_signals += 1
                            analysis["sentiment"]["funding_rate"] = "positive"
                        elif avg_rate < 0:
                            bearish_signals += 1
                            analysis["sentiment"]["funding_rate"] = "negative"
                        else:
                            analysis["sentiment"]["funding_rate"] = "neutral"

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
