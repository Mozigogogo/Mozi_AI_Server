"""技术分析 Skill"""
import asyncio
import pandas as pd
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import get_kline_data


class TechnicalAnalysisSkill(BaseSkill):
    """技术分析 Skill - 趋势、支撑阻力、指标分析"""

    name = "technical_analysis"
    description = "技术面分析（趋势、支撑阻力、指标）"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        """匹配意图"""
        return (
            intent.intent_type == "analyze_technical" and
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
        """执行分析（只调用必要的 API）"""
        # 只调用 get_kline_data
        kline_data = await asyncio.to_thread(get_kline_data, symbol)

        # 计算技术指标（本地计算）
        indicators = self._calculate_indicators(kline_data)

        return SkillResult(
            skill_name=self.name,
            data={
                "indicators": indicators,
                "kline_summary": self._summarize_kline(kline_data),
                "symbol": symbol
            },
            timestamp=self._get_timestamp(),
            api_calls=["get_kline_data"]
        )

    def _calculate_indicators(self, kline_data: list) -> dict:
        """计算技术指标"""
        if not kline_data:
            return {}

        try:
            df = pd.DataFrame(kline_data)

            # 确保有 close 列
            if 'close' not in df.columns:
                return {}

            # 计算移动平均线
            df['ma7'] = df['close'].rolling(7).mean()
            df['ma20'] = df['close'].rolling(20).mean()

            # 计算 RSI (14天)
            df['rsi'] = self._calculate_rsi(df['close'], 14)

            # 计算支撑和阻力
            df['support'] = df['close'].rolling(20).min()
            df['resistance'] = df['close'].rolling(20).max()

            # 获取最新的值
            latest = df.iloc[-1]

            return {
                "current_price": latest.get('close', 0),
                "ma7": latest.get('ma7', 0),
                "ma20": latest.get('ma20', 0),
                "rsi": latest.get('rsi', 50),
                "support": latest.get('support', 0),
                "resistance": latest.get('resistance', 0),
                "trend": self._determine_trend(df)
            }

        except Exception as e:
            print(f"计算技术指标失败: {e}")
            return {}

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """计算 RSI"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _determine_trend(self, df: pd.DataFrame) -> str:
        """判断趋势"""
        if 'ma7' not in df.columns or 'ma20' not in df.columns:
            return "unknown"

        latest = df.iloc[-1]
        ma7 = latest.get('ma7', 0)
        ma20 = latest.get('ma20', 0)

        if ma7 > ma20:
            return "上涨趋势 (bullish)"
        elif ma7 < ma20:
            return "下跌趋势 (bearish)"
        else:
            return "震荡 (sideways)"

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
