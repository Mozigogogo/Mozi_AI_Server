"""市场趋势查询 Skill"""
import asyncio
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import get_kline_data


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
        """只需要调用 kline_data API"""
        return ["get_kline_data"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行查询（只调用必要的 API）"""
        # 只调用 get_kline_data
        kline_data = await asyncio.to_thread(get_kline_data, symbol)

        # 计算趋势（本地计算）
        trend_info = self._calculate_trend(kline_data)

        return SkillResult(
            skill_name=self.name,
            data={
                "trend": trend_info,
                "kline_summary": self._summarize_kline(kline_data)
            },
            timestamp=self._get_timestamp(),
            api_calls=["get_kline_data"]
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

        # 取最后一天和第一天对比
        latest = kline_data[-1] if kline_data else {}
        earliest = kline_data[0] if len(kline_data) > 1 else {}

        close_latest = latest.get("close", 0)
        close_earliest = earliest.get("close", close_latest)

        # 计算涨跌幅
        change_percent = 0
        if close_earliest > 0:
            change_percent = ((close_latest - close_earliest) / close_earliest) * 100

        # 判断方向
        if change_percent > 1:
            direction = "上涨"  # up
        elif change_percent < -1:
            direction = "下跌"  # down
        else:
            direction = "震荡"  # sideways

        # 计算最高最低
        all_closes = [d.get("close", 0) for d in kline_data]
        high = max(all_closes) if all_closes else 0
        low = min(all_closes) if all_closes else 0

        return {
            "direction": direction,
            "change_percent": round(change_percent, 2),
            "high": high,
            "low": low,
            "current_price": close_latest
        }

    def _summarize_kline(self, kline_data: list) -> str:
        """总结 K 线数据"""
        if not kline_data:
            return "无数据"

        count = len(kline_data)
        return f"{count} 天的 K 线数据"
