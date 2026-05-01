"""技术分析 Skill"""
import asyncio
import pandas as pd
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import get_kline_data, get_header_data


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
        """需要调用 kline_data 和 header_data API"""
        return ["get_kline_data", "get_header_data"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行分析（并发调用API）"""
        # 并发调用 get_kline_data 和 get_header_data
        kline_data, header_data = await asyncio.gather(
            asyncio.to_thread(get_kline_data, symbol),
            asyncio.to_thread(get_header_data, symbol),
            return_exceptions=True
        )

        # 处理异常
        if isinstance(kline_data, Exception):
            kline_data = {}
        if isinstance(header_data, Exception):
            header_data = {}

        # 计算技术指标（本地计算）
        indicators = self._calculate_indicators(kline_data)

        # 提取实时价格（优先用header_data）
        real_time_price = None
        price_change_24h = None
        if header_data and isinstance(header_data, dict):
            real_time_price = header_data.get("currentPrice")
            price_change_24h = header_data.get("priceChangePercentage_24h")

        # 提取K线信息
        values = kline_data.get("values", []) if isinstance(kline_data, dict) else []
        close_prices = [day[3] for day in values if isinstance(day, list) and len(day) >= 4]
        kline_latest = close_prices[-1] if close_prices else 0

        api_calls = []
        if kline_data:
            api_calls.append("get_kline_data")
        if header_data:
            api_calls.append("get_header_data")

        return SkillResult(
            skill_name=self.name,
            data={
                "indicators": indicators,
                "kline_summary": self._summarize_kline(kline_data),
                "symbol": symbol,
                "data_points": len(close_prices),
                "price_range": {
                    "min": float(min(close_prices)) if close_prices else 0,
                    "max": float(max(close_prices)) if close_prices else 0
                },
                "latest_price": real_time_price or kline_latest,
                "real_time_price": real_time_price,
                "price_change_24h": price_change_24h,
                "kline_latest_close": kline_latest
            },
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _calculate_indicators(self, kline_data: dict) -> dict:
        """计算技术指标"""
        if not kline_data:
            return {}

        try:
            # kline_data 格式: {"values": [[open, high, low, close], ...], "categoryData": [...]}
            # 提取收盘价 (每个子数组的第4个元素是 close price)
            values = kline_data.get("values", [])

            if not values or len(values) == 0:
                print("kline_data.values 为空")
                return {}

            # 提取收盘价（每个 OHLC 数组的第4个元素，索引3）
            close_prices = [day[3] for day in values if isinstance(day, list) and len(day) >= 4]

            if len(close_prices) == 0:
                print("无法提取收盘价")
                return {}

            # 转换为数值类型（关键修复：处理字符串数据）
            close_prices = [float(price) for price in close_prices if price is not None]

            # 转换为 pandas DataFrame
            df = pd.DataFrame({"close": close_prices})

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

            # 计算涨跌幅
            if len(close_prices) >= 2:
                previous_price = close_prices[-2]
                current_price = close_prices[-1]
                price_change = current_price - previous_price
                price_change_percent = (price_change / previous_price) * 100
            else:
                price_change = 0
                price_change_percent = 0

            return {
                "current_price": latest.get('close', 0),
                "previous_price": close_prices[-2] if len(close_prices) >= 2 else latest.get('close', 0),
                "price_change": price_change,
                "price_change_percent": price_change_percent,
                "ma7": latest.get('ma7', 0),
                "ma20": latest.get('ma20', 0),
                "rsi": latest.get('rsi', 50),
                "support": latest.get('support', 0),
                "resistance": latest.get('resistance', 0),
                "trend": self._determine_trend(df)
            }

        except Exception as e:
            print(f"计算技术指标失败: {e}")
            import traceback
            traceback.print_exc()
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

        # 比较当前价格与均线的位置
        current_price = latest.get('close', 0)

        if current_price > ma7 and current_price > ma20:
            return "上涨趋势 (bullish) - 价格站上短期和长期均线上方"
        elif current_price < ma7 and current_price < ma20:
            return "下跌趋势 (bearish) - 价格位于短期和长期均线下方"
        elif ma7 > ma20:
            return "震荡转强 (bullish crossover) - 短期均线上穿长期均线"
        elif ma7 < ma20:
            return "震荡转弱 (bearish crossover) - 短期均线下穿长期均线"
        else:
            return "震荡整理 (sideways) - 价格在均线附近徘徊"

    def _summarize_kline(self, kline_data: dict) -> str:
        """总结 K 线数据"""
        if not kline_data:
            return "无数据"

        # kline_data 格式: {"values": [[open, high, low, close], ...], "categoryData": [...]}
        values = kline_data.get("values", [])

        # 提取收盘价数量
        close_prices = [day[3] for day in values if isinstance(day, list) and len(day) >= 4]

        count = len(close_prices)

        if count == 0:
            return "无可用价格数据"

        # 计算价格范围
        price_min = float(min(close_prices))
        price_max = float(max(close_prices))

        return f"{count} 天的价格数据，价格区间: ${price_min:.2f} - ${price_max:.2f}"
