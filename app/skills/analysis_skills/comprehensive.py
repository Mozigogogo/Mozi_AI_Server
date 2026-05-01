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

        # 构建传给LLM的数据（包含摘要+关键原始数据）
        llm_data = self._build_llm_data(data)

        return SkillResult(
            skill_name=self.name,
            data=llm_data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _build_llm_data(self, data: dict) -> dict:
        """构建传给LLM的数据：摘要 + 关键原始数据"""
        result = {}

        # 1. 基本面（header_data）
        if "get_header_data" in data and data["get_header_data"]:
            h = data["get_header_data"]
            try:
                price = float(h.get("currentPrice", 0))
                change = float(h.get("priceChange_24h", 0))
            except (ValueError, TypeError):
                price = 0
                change = 0
            result["实时数据"] = {
                "当前价格": price,
                "24h涨跌额": change,
                "24h涨跌幅": h.get("priceChangePercentage_24h"),
                "24h最高": h.get("high_24h"),
                "24h最低": h.get("low_24h"),
                "市值": h.get("marketCap"),
                "排名": h.get("marketCapRank"),
            }

        # 2. K线趋势（kline_data）
        if "get_kline_data" in data and data["get_kline_data"]:
            kd = data["get_kline_data"]
            if isinstance(kd, dict) and "values" in kd:
                values = kd["values"]
                if values and len(values) > 1:
                    try:
                        close_latest = float(values[-1][3])
                        close_earliest = float(values[0][3])
                        change_pct = ((close_latest - close_earliest) / close_earliest * 100) if close_earliest else 0
                    except (ValueError, TypeError, IndexError):
                        close_latest = 0
                        change_pct = 0
                    result["30天趋势"] = {
                        "起始价": close_earliest if values else 0,
                        "最新收盘价": close_latest,
                        "30天涨跌幅": round(change_pct, 2),
                        "数据天数": len(values),
                    }

        # 3. 多空比（buy_sell_ratio）- 精简：每个交易所只保留最近5天
        if "get_buy_sell_ratio" in data and data["get_buy_sell_ratio"]:
            ratio_raw = data["get_buy_sell_ratio"]
            ratio_trimmed = {}
            for exchange, exchange_data in ratio_raw.items():
                if isinstance(exchange_data, dict):
                    trimmed = {}
                    for k, v in exchange_data.items():
                        if isinstance(v, list) and len(v) > 7:
                            trimmed[k] = v[-7:]
                        else:
                            trimmed[k] = v
                    ratio_trimmed[exchange] = trimmed
                else:
                    ratio_trimmed[exchange] = exchange_data
            result["多空比数据"] = ratio_trimmed

        # 4. 资金费率（funding_rate）- 直接传（数据量小）
        if "get_funding_rate" in data and data["get_funding_rate"]:
            result["资金费率"] = data["get_funding_rate"]

        # 5. 持仓量（open_interest）- 精简：每个交易所只保留最近7天
        if "get_open_interest" in data and data["get_open_interest"]:
            oi_raw = data["get_open_interest"]
            oi_trimmed = {}
            for k, v in oi_raw.items():
                if k == "data" and isinstance(v, dict):
                    trimmed_data = {}
                    for exchange, values in v.items():
                        if isinstance(values, list) and len(values) > 7:
                            trimmed_data[exchange] = values[-7:]
                        else:
                            trimmed_data[exchange] = values
                    oi_trimmed[k] = trimmed_data
                elif k == "dates" and isinstance(v, list) and len(v) > 7:
                    oi_trimmed[k] = v[-7:]
                else:
                    oi_trimmed[k] = v
            result["持仓量"] = oi_trimmed

        # 6. 新闻
        if "get_recent_news" in data and data["get_recent_news"]:
            news_list = data["get_recent_news"]
            if isinstance(news_list, list):
                result["最新新闻"] = news_list[:5]

        return result
