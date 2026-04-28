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

            latest = values[-1]
            earliest = values[0]
            # values 格式: [open, close, low, high]
            close_latest = float(latest[1]) if len(latest) > 1 else 0
            close_earliest = float(earliest[1]) if len(earliest) > 1 else 0
        elif isinstance(kline_data, list):
            if len(kline_data) < 2:
                return {
                    "direction": "unknown",
                    "change_percent": 0,
                    "high": 0,
                    "low": 0
                }

            latest = kline_data[-1]
            earliest = kline_data[0]
            close_latest = float(latest.get("close", 0)) if isinstance(latest, dict) else float(latest)
            close_earliest = float(earliest.get("close", 0)) if isinstance(earliest, dict) else float(earliest)
        else:
            return {
                "direction": "unknown",
                "change_percent": 0,
                "high": 0,
                "low": 0
            }

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
        if isinstance(kline_data, dict) and "values" in kline_data:
            all_closes = [float(v[1]) if len(v) > 1 else 0 for v in kline_data["values"]]
            high = max(all_closes) if all_closes else 0
            low = min(all_closes) if all_closes else 0
        elif isinstance(kline_data, list):
            all_closes = [float(d.get("close", 0)) if isinstance(d, dict) else float(d) for d in kline_data]
            high = max(all_closes) if all_closes else 0
            low = min(all_closes) if all_closes else 0
        else:
            high = 0
            low = 0

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

        # kline_data 可能是字典格式
        if isinstance(kline_data, dict) and "values" in kline_data:
            count = len(kline_data["values"])
        else:
            count = len(kline_data)
        return f"{count} 天的 K 线数据"
