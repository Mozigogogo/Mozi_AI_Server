"""综合分析 Skill（量化优化版）

优化点：
1. 新增 TechnicalIndicators 工具类：基于K线收盘价计算 MA / EMA / RSI / MACD / 布林带 / 波动率 / 动量
2. 新增"综合趋势评分"：融合价格动量、RSI、均线信号、资金费率，生成 -100~100 的量化倾向分数
3. 使用 logging 替代 print，便于分级与落盘
4. 拆分原来冗长的 _build_llm_data，职责单一、易于单测
5. 对外接口（execute_async / SkillResult）保持不变，可直接替换原文件
"""
import asyncio
import logging
import statistics
from typing import Optional

from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_header_data,
    get_kline_data,
    get_recent_news,
    get_buy_sell_ratio,
    get_open_interest,
    get_funding_rate,
)

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """技术指标计算工具类，输入为按时间升序排列的收盘价序列"""

    @staticmethod
    def sma(closes: list, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        return round(sum(closes[-period:]) / period, 6)

    @staticmethod
    def ema(closes: list, period: int) -> Optional[float]:
        if len(closes) < period:
            return None
        k = 2 / (period + 1)
        ema_val = sum(closes[:period]) / period
        for price in closes[period:]:
            ema_val = price * k + ema_val * (1 - k)
        return round(ema_val, 6)

    @staticmethod
    def rsi(closes: list, period: int = 14) -> Optional[float]:
        """相对强弱指标，常用判断超买(>70)/超卖(<30)"""
        if len(closes) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 2)

    @staticmethod
    def macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9) -> Optional[dict]:
        if len(closes) < slow + signal:
            return None

        def ema_series(values: list, period: int) -> list:
            k = 2 / (period + 1)
            series = [sum(values[:period]) / period]
            for price in values[period:]:
                series.append(price * k + series[-1] * (1 - k))
            return series

        fast_series = ema_series(closes, fast)
        slow_series = ema_series(closes, slow)
        offset = len(fast_series) - len(slow_series)
        macd_line = [f - s for f, s in zip(fast_series[offset:], slow_series)]
        if len(macd_line) < signal:
            return None
        signal_series = ema_series(macd_line, signal)
        hist = macd_line[-1] - signal_series[-1]
        return {
            "macd": round(macd_line[-1], 4),
            "signal": round(signal_series[-1], 4),
            "histogram": round(hist, 4),
        }

    @staticmethod
    def bollinger_bands(closes: list, period: int = 20, num_std: float = 2) -> Optional[dict]:
        if len(closes) < period:
            return None
        window = closes[-period:]
        mid = sum(window) / period
        std = statistics.pstdev(window)
        return {
            "中轨": round(mid, 4),
            "上轨": round(mid + num_std * std, 4),
            "下轨": round(mid - num_std * std, 4),
        }

    @staticmethod
    def volatility(closes: list, period: int = 7) -> Optional[float]:
        """基于对数收益率的波动率（百分比），衡量近期价格波动剧烈程度"""
        if len(closes) < period + 1:
            return None
        window = closes[-(period + 1):]
        returns = []
        for i in range(1, len(window)):
            if window[i - 1] > 0:
                returns.append((window[i] - window[i - 1]) / window[i - 1])
        if not returns:
            return None
        return round(statistics.pstdev(returns) * 100, 2)

    @staticmethod
    def momentum(closes: list, period: int = 7) -> Optional[float]:
        """N日动量（百分比），正值代表上涨惯性，负值代表下跌惯性"""
        if len(closes) <= period:
            return None
        base = closes[-1 - period]
        if base == 0:
            return None
        return round((closes[-1] - base) / base * 100, 2)


class ComprehensiveAnalysisSkill(BaseSkill):
    """综合分析 Skill - 多维度全面分析 + 量化趋势信号"""

    name = "comprehensive_analysis"
    description = "综合分析（多维度全面分析 + 量化趋势信号）"

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
            "get_funding_rate",
        ]

    async def execute_async(self, symbol: str, intent: IntentInfo) -> SkillResult:
        """执行综合分析（并发调用多个 API）"""
        api_specs = {
            "get_header_data": (get_header_data, (symbol,), {}),
            "get_kline_data": (get_kline_data, (symbol,), {}),
            "get_recent_news": (get_recent_news, (symbol,), {"limit": 5}),
            "get_buy_sell_ratio": (get_buy_sell_ratio, (symbol,), {}),
            "get_open_interest": (get_open_interest, (symbol,), {}),
            "get_funding_rate": (get_funding_rate, (symbol,), {}),
        }

        api_names = list(api_specs.keys())
        tasks = [
            asyncio.to_thread(func, *args, **kwargs)
            for func, args, kwargs in api_specs.values()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        data = {}
        api_calls = []
        for api_name, result in zip(api_names, results):
            if isinstance(result, Exception):
                logger.warning("%s 调用失败: %s", api_name, result)
                continue
            if result is None:
                logger.warning("%s 返回 None（symbol=%s 可能有误）", api_name, symbol)
                continue
            if isinstance(result, dict) and result.get("code") not in (None, 0):
                logger.warning("%s 返回错误: %s", api_name, result.get("errorMsg"))
                continue
            data[api_name] = result
            api_calls.append(api_name)

        llm_data = self._build_llm_data(data)

        return SkillResult(
            skill_name=self.name,
            data=llm_data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls,
        )

    # ---------------------------------------------------------------------
    # 数据构建
    # ---------------------------------------------------------------------

    def _build_llm_data(self, data: dict) -> dict:
        """构建传给LLM/量化模块的数据：摘要 + 关键原始数据 + 技术指标 + 综合评分"""
        result = {}

        closes = None
        if "get_kline_data" in data:
            closes = self._extract_closes(data["get_kline_data"])

        if "get_header_data" in data:
            result["实时数据"] = self._parse_header(data["get_header_data"])

        if closes:
            result["30天趋势(历史日线)"] = self._build_kline_summary(data["get_kline_data"], closes)
            quant_signals = self._build_quant_signals(closes)
            if quant_signals:
                result["量化技术指标"] = quant_signals

        if "get_buy_sell_ratio" in data and data["get_buy_sell_ratio"]:
            result.update(self._build_ratio_section(data["get_buy_sell_ratio"]))

        if "get_funding_rate" in data and data["get_funding_rate"]:
            result["资金费率"] = data["get_funding_rate"]

        if "get_open_interest" in data and data["get_open_interest"]:
            result["持仓量"] = self._trim_open_interest(data["get_open_interest"])

        if "get_recent_news" in data and isinstance(data["get_recent_news"], list):
            result["最新新闻"] = data["get_recent_news"][:5]

        # 融合多维数据生成一个量化倾向评分，供模型/规则引擎直接消费
        score = self._composite_trend_score(result)
        if score is not None:
            result["综合趋势评分"] = score

        return result

    @staticmethod
    def _extract_closes(kline_data) -> Optional[list]:
        """从K线原始数据中提取按时间升序排列的收盘价序列"""
        if not isinstance(kline_data, dict) or "values" not in kline_data:
            return None
        closes = []
        for row in kline_data["values"]:
            try:
                closes.append(float(row[3]))
            except (ValueError, TypeError, IndexError):
                continue
        return closes if len(closes) > 1 else None

    @staticmethod
    def _parse_header(h: dict) -> dict:
        try:
            price = float(h.get("currentPrice", 0))
            change = float(h.get("priceChange_24h", 0))
        except (ValueError, TypeError):
            price, change = 0, 0
        return {
            "当前价格": price,
            "24h涨跌额": change,
            "24h涨跌幅": h.get("priceChangePercentage_24h"),
            "24h最高": h.get("high_24h"),
            "24h最低": h.get("low_24h"),
            "市值": h.get("marketCap"),
            "排名": h.get("marketCapRank"),
        }

    @staticmethod
    def _build_kline_summary(kd: dict, closes: list) -> dict:
        dates = kd.get("categoryData", [])
        change_pct = ((closes[-1] - closes[0]) / closes[0] * 100) if closes[0] else 0
        return {
            "起始价": closes[0],
            "最新日收盘价(非实时)": closes[-1],
            "最新日期": dates[-1] if dates else "N/A",
            "30天涨跌幅": round(change_pct, 2),
            "数据天数": len(closes),
        }

    @staticmethod
    def _build_quant_signals(closes: list) -> dict:
        """基于收盘价序列计算量化技术指标"""
        ti = TechnicalIndicators
        signals = {
            "MA5": ti.sma(closes, 5),
            "MA10": ti.sma(closes, 10),
            "MA20": ti.sma(closes, 20),
            "EMA12": ti.ema(closes, 12),
            "EMA26": ti.ema(closes, 26),
            "RSI14": ti.rsi(closes, 14),
            "MACD": ti.macd(closes),
            "布林带": ti.bollinger_bands(closes),
            "7日波动率(%)": ti.volatility(closes, 7),
            "7日动量(%)": ti.momentum(closes, 7),
        }

        ma5, ma20 = signals.get("MA5"), signals.get("MA20")
        if ma5 is not None and ma20 is not None:
            signals["均线信号"] = "金叉(短期强于长期)" if ma5 > ma20 else "死叉(短期弱于长期)"

        # 去掉数据不足时计算出的 None，避免污染下游
        return {k: v for k, v in signals.items() if v is not None}

    @staticmethod
    def _build_ratio_section(ratio_raw: dict) -> dict:
        ratio_trimmed = {}
        ratio_last_date = None
        for exchange, exchange_data in ratio_raw.items():
            if isinstance(exchange_data, dict):
                dates = exchange_data.get("xAxisData", [])
                if dates and not ratio_last_date:
                    ratio_last_date = dates[-1]
                trimmed = {
                    k: (v[-7:] if isinstance(v, list) and len(v) > 7 else v)
                    for k, v in exchange_data.items()
                }
                ratio_trimmed[exchange] = trimmed
            else:
                ratio_trimmed[exchange] = exchange_data
        label = f"多空比数据(截至{ratio_last_date or '昨日'})"
        return {label: ratio_trimmed}

    @staticmethod
    def _trim_open_interest(oi_raw: dict) -> dict:
        oi_trimmed = {}
        for k, v in oi_raw.items():
            if k == "data" and isinstance(v, dict):
                oi_trimmed[k] = {
                    exchange: (values[-7:] if isinstance(values, list) and len(values) > 7 else values)
                    for exchange, values in v.items()
                }
            elif k == "dates" and isinstance(v, list) and len(v) > 7:
                oi_trimmed[k] = v[-7:]
            else:
                oi_trimmed[k] = v
        return oi_trimmed

    @staticmethod
    def _extract_first_funding_rate(funding) -> Optional[float]:
        """尽量从不同结构的资金费率返回值中提取一个最新数值"""
        if not funding:
            return None
        try:
            if isinstance(funding, dict):
                for v in funding.values():
                    if isinstance(v, (int, float)):
                        return float(v)
                    if isinstance(v, dict) and "rate" in v:
                        return float(v["rate"])
                    if isinstance(v, list) and v:
                        last = v[-1]
                        if isinstance(last, (int, float)):
                            return float(last)
                        if isinstance(last, dict):
                            for key in ("rate", "fundingRate", "value"):
                                if key in last:
                                    return float(last[key])
        except (ValueError, TypeError):
            return None
        return None

    @classmethod
    def _composite_trend_score(cls, result: dict) -> Optional[dict]:
        """
        融合价格动量、RSI、均线信号、资金费率，生成一个 -100~100 的趋势倾向评分。
        分数越大代表数据面越偏多，越小代表越偏空；该评分基于历史统计规则计算，
        仅作为量化参考信号之一，不构成投资建议。
        """
        quant = result.get("量化技术指标", {})
        if not quant:
            return None

        score = 0.0
        factors = []

        momentum = quant.get("7日动量(%)")
        if momentum is not None:
            contrib = max(min(momentum * 2, 30), -30)
            score += contrib
            factors.append(f"7日动量贡献: {round(contrib, 1)}")

        rsi = quant.get("RSI14")
        if rsi is not None:
            if rsi > 70:
                score -= 15
                factors.append("RSI超买(>70): -15")
            elif rsi < 30:
                score += 15
                factors.append("RSI超卖(<30): +15")

        ma_signal = quant.get("均线信号")
        if ma_signal:
            if "金叉" in ma_signal:
                score += 10
                factors.append("均线金叉: +10")
            else:
                score -= 10
                factors.append("均线死叉: -10")

        funding_rate_val = cls._extract_first_funding_rate(result.get("资金费率"))
        if funding_rate_val is not None:
            contrib = max(min(funding_rate_val * 1000, 15), -15)
            score += contrib
            factors.append(f"资金费率贡献: {round(contrib, 1)}")

        if not factors:
            return None

        score = max(min(score, 100), -100)
        if score > 20:
            bias = "偏多"
        elif score < -20:
            bias = "偏空"
        else:
            bias = "中性"

        return {
            "评分": round(score, 1),
            "方向": bias,
            "依据": factors,
            "说明": "该评分基于历史数据统计规则计算，仅供参考，不构成投资建议",
        }