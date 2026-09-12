"""美股问答 Skill — 报价/档案/K线/区间涨跌/交易时段"""
import asyncio
from typing import Any, Dict, List, Optional

from app.skills.base import BaseSkill, IntentInfo, SkillResult
from app.services.data_service import (
    get_us_quote,
    get_us_kline,
    get_us_kline_realtime,
    get_us_return_investment,
    get_us_session,
    validate_us_ticker,
)


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class UsStockSkill(BaseSkill):
    """美股问答 — asset_class=us_stock 的所有意图统一走这里（注册顺序在加密 skill 之前）"""

    name = "us_stock"
    description = "美股报价、档案、K线、区间涨跌、交易时段查询与分析"

    def match(self, intent: IntentInfo, mode: str = "chat") -> bool:
        return intent.asset_class == "us_stock"

    def get_required_apis(self) -> list:
        return ["get_us_quote", "get_us_kline_data", "get_us_return_investment"]

    async def execute_async(self, symbol: str, intent: IntentInfo) -> SkillResult:
        ticker = (symbol or "").strip().upper()

        # ticker 校验（无效时给明确文案，避免后续 API 全空）
        v = await asyncio.to_thread(validate_us_ticker, ticker)
        if not v.get("valid"):
            return SkillResult(
                skill_name=self.name,
                data={"错误": f"无效的美股代码: {ticker}"},
                timestamp=self._get_timestamp(),
                api_calls=[],
            )

        # 深度按意图：query_price 最浅，analyze_* 最全
        itype = intent.intent_type
        need_kline = itype in (
            "query_trend", "analyze_comprehensive", "analyze_technical",
            "analyze_quantitative", "analyze_signal", "query_derivatives",
        )
        need_roi = itype.startswith("analyze") or itype in ("query_trend", "query_derivatives")
        need_news = itype == "query_news"

        tasks = [asyncio.to_thread(get_us_quote, ticker), asyncio.to_thread(get_us_session, ticker)]
        names = ["quote", "session"]
        if need_kline:
            tasks.append(asyncio.to_thread(get_us_kline, ticker, 2))       # 日线 30 根
            tasks.append(asyncio.to_thread(get_us_kline_realtime, ticker, "1h", 24, 1))  # 日内 24 根
            names += ["kline_daily", "kline_hourly"]
        if need_roi:
            tasks.append(asyncio.to_thread(get_us_return_investment, ticker))
            names.append("roi")

        results = await asyncio.gather(*tasks, return_exceptions=True)
        raw = {n: r for n, r in zip(names, results) if not isinstance(r, Exception)}

        data: Dict[str, Any] = {}

        # 1. 实时报价（key 命名与加密 skill 对齐，agent 兜底逻辑能识别"实时数据.当前价格"）
        quote = raw.get("quote") or {}
        if quote:
            last = _f(quote.get("lastPrice"))
            # 后端个别标的 priceChangePercent 异常（AAPL 实测 2.09e18）——>50% 视为脏数据丢弃
            pct = _f(quote.get("priceChangePercent"))
            if pct is not None and abs(pct) > 50:
                pct = None
            chg = _f(quote.get("priceChange"))
            if chg is not None and last and abs(chg) > last * 0.5:
                chg = None
            data["实时数据"] = {
                "当前价格": last,
                "涨跌": chg,
                "涨跌幅%": pct,
                "日内最高": _f(quote.get("highPrice")),
                "日内最低": _f(quote.get("lowPrice")),
                "成交量": _f(quote.get("volume")),
                "成交额": _f(quote.get("quoteVolume")),
                "数据时间": quote.get("ts"),
            }

        # 2. 公司档案（精简，长简介截断）
        profile = {
            k: quote.get(k) for k in (
                "nameCn", "name", "sectorCn", "sector", "industry", "marketCap",
                "week52High", "week52Low", "beta", "averageVolume", "dividendYield",
                "peRatio", "ipoDate", "country",
            ) if quote.get(k) not in (None, "")
        }
        desc = quote.get("descriptionCn") or quote.get("description") or ""
        if desc:
            profile["简介"] = str(desc)[:120]
        if profile:
            data["公司档案"] = profile

        # 3. 日线（30 根 → 摘要 + 最近序列）
        daily = raw.get("kline_daily") or {}
        rows: List[dict] = daily.get("list") or []
        closes = [c for c in (_f(r.get("closePrice")) for r in rows) if c is not None]
        if closes:
            first, last = closes[0], closes[-1]
            data["日线(近30天)"] = {
                "最新收盘": last,
                "区间涨跌幅%": round((last - first) / first * 100, 2) if first else None,
                "区间最高": max(closes),
                "区间最低": min(closes),
                "最近10日收盘": [
                    {"日期": r.get("dt"), "收盘": _f(r.get("closePrice"))}
                    for r in rows[-10:]
                ],
            }

        # 4. 日内小时线（实时链路）
        hourly = raw.get("kline_hourly") or {}
        hrows: List[dict] = hourly.get("list") or []
        hcloses = [c for c in (_f(r.get("close_price")) for r in hrows) if c is not None]
        if hcloses:
            data["日内小时线(最近24根收盘)"] = hcloses

        # 5. 区间涨跌（1日/7日/1月/1年）
        roi = raw.get("roi") or {}
        if roi:
            data["区间涨跌"] = roi

        # 6. 交易时段
        session = raw.get("session") or {}
        if session:
            data["交易时段"] = session

        # 7. 新闻：美股新闻接口暂缺
        if need_news:
            data["新闻"] = "暂无美股新闻数据源"

        api_calls = ["get_us_quote"] + [n for n in names[1:] if n in raw]
        return SkillResult(
            skill_name=self.name,
            data=data,
            timestamp=self._get_timestamp(),
            api_calls=api_calls,
        )
