"""市场趋势查询 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import get_kline_data, get_header_data


class MarketTrendSkill(BaseSkill):
    """市场趋势查询 Skill - 获取 K 线和趋势"""

    name = "market_trend"
    description = "查询趋势、走势、涨跌幅"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return (
            intent.intent_type == "query_trend" and
            "get_kline_data" in intent.required_apis
        )

    def get_required_apis(self) -> list:
        """需要调用 kline_data 和 header_data API"""
        return ["get_kline_data", "get_header_data"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行查询（并发调用 API）"""
        kline_data, header_data = await asyncio.gather(
            asyncio.to_thread(get_kline_data, symbol),
            asyncio.to_thread(get_header_data, symbol),
            return_exceptions=True
        )

        if isinstance(kline_data, Exception):
            kline_data = {}
        if isinstance(header_data, Exception):
            header_data = {}

        # 计算趋势（本地计算）
        trend_info = self._calculate_trend(kline_data)

        # 构建完整数据
        data = {
            "币种": symbol,
            "趋势": trend_info,
            "K线摘要": self._summarize_kline(kline_data),
        }

        # 添加实时价格信息
        if header_data and isinstance(header_data, dict):
            try:
                price = float(header_data.get("currentPrice", 0))
            except (ValueError, TypeError):
                price = 0
            data["实时价格"] = {
                "当前价格": price,
                "24h涨跌幅": header_data.get("priceChangePercentage_24h"),
                "24h最高": header_data.get("high_24h"),
                "24h最低": header_data.get("low_24h"),
                "成交量": header_data.get("volume"),
                "市值": header_data.get("marketCap"),
            }

        # K线数据标注为历史日线
        kline_dates = kline_data.get("categoryData", []) if isinstance(kline_data, dict) else []
        data["K线数据(历史日线)"] = {
            "最新日期": kline_dates[-1] if kline_dates else "N/A",
            "该日收盘价": data["趋势"].get("current_price") if data.get("趋势") else None,
        }

        api_calls = []
        if kline_data and not isinstance(kline_data, Exception):
            api_calls.append("get_kline_data")
        if header_data and not isinstance(header_data, Exception):
            api_calls.append("get_header_data")

        return SkillResult(
            skill_name=self.name,
            data=data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _calculate_trend(self, kline_data: list) -> dict:
        """计算趋势信息"""
        if not kline_data:
            return {
                "direction": "unknown",
                "change_percent": 0,
                "high": 0,
                "low": 0
            }

        # 处理字典格式的 kline_data
        if isinstance(kline_data, dict) and "values" in kline_data:
            values = kline_data["values"]
            if not values or len(values) < 2:
                return {
                    "direction": "unknown",
                    "change_percent": 0,
                    "high": 0,
                    "low": 0
                }

            # values 格式: [open, high, low, close]
            close_latest = float(values[-1][3]) if len(values[-1]) > 3 else 0
            close_earliest = float(values[0][3]) if len(values[0]) > 3 else 0

            # 计算涨跌幅
            change_percent = 0
            if close_earliest > 0:
                change_percent = ((close_latest - close_earliest) / close_earliest) * 100

            # 判断方向
            if change_percent > 1:
                direction = "上涨"
            elif change_percent < -1:
                direction = "下跌"
            else:
                direction = "震荡"

            # 计算最高最低（用 close 价格）
            all_closes = [float(v[3]) if len(v) > 3 else 0 for v in values]
            high = max(all_closes) if all_closes else 0
            low = min(all_closes) if all_closes else 0

            return {
                "direction": direction,
                "change_percent": round(change_percent, 2),
                "high": high,
                "low": low,
                "current_price": close_latest,
                "data_points": len(values)
            }
        else:
            return {
                "direction": "unknown",
                "change_percent": 0,
                "high": 0,
                "low": 0
            }

    def _summarize_kline(self, kline_data: list) -> str:
        """总结 K 线数据"""
        if not kline_data:
            return "无数据"

        # kline_data 可能是字典格式
        if isinstance(kline_data, dict) and "values" in kline_data:
            count = len(kline_data["values"])
        else:
            count = len(kline_data)
        return f"{count} 天的 K 线数据"
