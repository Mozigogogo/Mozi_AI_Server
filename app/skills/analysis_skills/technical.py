"""技术分析 Skill"""
import asyncio
import pandas as pd
from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_kline_data, get_header_data,
    get_buy_sell_ratio, get_funding_rate
)


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
        """需要调用的 API"""
        return ["get_kline_data", "get_header_data", "get_buy_sell_ratio", "get_funding_rate"]

    async def execute_async(
        self,
        symbol: str,
        intent: IntentInfo
    ) -> SkillResult:
        """执行分析（并发调用API）"""
        kline_data, header_data, ratio_data, funding_data = await asyncio.gather(
            asyncio.to_thread(get_kline_data, symbol),
            asyncio.to_thread(get_header_data, symbol),
            asyncio.to_thread(get_buy_sell_ratio, symbol),
            asyncio.to_thread(get_funding_rate, symbol),
            return_exceptions=True
        )

        if isinstance(kline_data, Exception):
            kline_data = {}
        if isinstance(header_data, Exception):
            header_data = {}
        if isinstance(ratio_data, Exception):
            ratio_data = {}
        if isinstance(funding_data, Exception):
            funding_data = {}

        # 检查 API 返回的 data 是否为 None（币种符号错误时会出现）
        if isinstance(header_data, dict) and header_data.get("code") is not None and header_data.get("code") != 0:
            header_data = {}
        if isinstance(kline_data, dict) and kline_data.get("code") is not None and kline_data.get("code") != 0:
            kline_data = {}

        # 提取实时价格（来自 header API）
        real_time_price = None
        price_change_24h = None
        if header_data and isinstance(header_data, dict):
            real_time_price = header_data.get("currentPrice")
            price_change_24h = header_data.get("priceChangePercentage_24h")

        # 计算技术指标（传入实时价格）
        indicators = self._calculate_indicators(kline_data, real_time_price)

        # 提取K线信息
        values = kline_data.get("values", []) if isinstance(kline_data, dict) else []
        close_prices = [day[3] for day in values if isinstance(day, list) and len(day) >= 4]
        kline_latest = close_prices[-1] if close_prices else 0
        kline_dates = kline_data.get("categoryData", []) if isinstance(kline_data, dict) else []
        kline_last_date = kline_dates[-1] if kline_dates else "N/A"

        # 构建返回数据 — 明确区分实时 vs 历史
        data = {
            "币种": symbol,
            "实时数据": {
                "当前价格": real_time_price,
                "24h涨跌幅": price_change_24h,
                "24h最高": header_data.get("high_24h") if isinstance(header_data, dict) else None,
                "24h最低": header_data.get("low_24h") if isinstance(header_data, dict) else None,
            },
            "技术指标": indicators,
            "K线数据(历史日线)": {
                "最新日期": kline_last_date,
                "该日收盘价": kline_latest,
                "数据天数": len(close_prices),
                "价格区间": {
                    "min": float(min(close_prices)) if close_prices else 0,
                    "max": float(max(close_prices)) if close_prices else 0,
                },
            },
        }

        # 多空比精简 — 标注数据截至日期
        if ratio_data and isinstance(ratio_data, dict):
            ratio_summary = {}
            ratio_last_date = None
            for exchange, exchange_data in ratio_data.items():
                if isinstance(exchange_data, dict):
                    ls = exchange_data.get("longShortData", [])
                    dates = exchange_data.get("xAxisData", [])
                    if dates and not ratio_last_date:
                        ratio_last_date = dates[-1]
                    ratio_summary[exchange] = {
                        "多空比": ls[-1] if ls else "N/A",
                        "多头占比": exchange_data.get("longData", [None])[-1],
                        "空头占比": exchange_data.get("shortData", [None])[-1],
                    }
            if ratio_summary:
                label = f"多空比(截至{ratio_last_date or '昨日'})"
                data[label] = ratio_summary

        # 资金费率
        if funding_data and isinstance(funding_data, dict):
            data["资金费率"] = funding_data

        api_calls = []
        if kline_data and not isinstance(kline_data, Exception):
            api_calls.append("get_kline_data")
        if header_data and not isinstance(header_data, Exception):
            api_calls.append("get_header_data")
        if ratio_data and not isinstance(ratio_data, Exception):
            api_calls.append("get_buy_sell_ratio")
        if funding_data and not isinstance(funding_data, Exception):
            api_calls.append("get_funding_rate")

        return SkillResult(
            skill_name=self.name,
            data=data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls
        )

    def _calculate_indicators(self, kline_data: dict, real_time_price=None) -> dict:
        """计算技术指标，使用实时价格作为当前价"""
        if not kline_data:
            return {}

        try:
            values = kline_data.get("values", [])

            if not values or len(values) == 0:
                return {}

            close_prices = [day[3] for day in values if isinstance(day, list) and len(day) >= 4]

            if len(close_prices) == 0:
                return {}

            close_prices = [float(price) for price in close_prices if price is not None]

            # 用实时价格替换最后一个K线收盘价，使均线/趋势判断更准确
            if real_time_price is not None:
                try:
                    rt_price = float(real_time_price)
                    close_prices[-1] = rt_price
                except (ValueError, TypeError):
                    pass

            df = pd.DataFrame({"close": close_prices})

            df['ma7'] = df['close'].rolling(7).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            df['rsi'] = self._calculate_rsi(df['close'], 14)
            df['support'] = df['close'].rolling(20).min()
            df['resistance'] = df['close'].rolling(20).max()

            latest = df.iloc[-1]

            return {
                "当前价格(实时)": latest.get('close', 0),
                "ma7": round(latest.get('ma7', 0), 2),
                "ma20": round(latest.get('ma20', 0), 2),
                "rsi": round(latest.get('rsi', 50), 2),
                "支撑位": round(latest.get('support', 0), 2),
                "阻力位": round(latest.get('resistance', 0), 2),
                "趋势": self._determine_trend(df)
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
