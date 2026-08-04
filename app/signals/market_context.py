"""
BTC 大盘共振层 — 给所有单币信号加一层"看大盘再做决定"。

设计原则：
1. 方向无关：market_breadth 是大盘状态描述（bullish/neutral/risk_off），不是 long/short 推荐。
   bullish 表示大盘风险偏好上升（long 顺 / short 逆），risk_off 反之。
2. 与 Phase 0 BTC 护栏互补：Phase 0 是 ±3% 硬护栏（直接跳过扫描），
   本模块是 ±1% 软调节（confidence × 系数）。形成梯度响应。
3. 5 分钟 LRU 缓存：避免每个币扫描都打 BTC API。
"""
import threading
import time
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

CACHE_TTL_SEC = 300
_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {"value": None, "ts": 0.0}

BREADTH_CHANGE_THRESHOLD = 1.0  # ±1% 软阈值


def _compute_trend(closes: list, window: int = 20) -> str:
    """简单的趋势判定：当前价 vs MA(window)。返回 up/down/flat。"""
    if not closes or len(closes) < window + 1:
        return "flat"
    ma = sum(closes[-window:]) / window
    last = closes[-1]
    if last > ma * 1.005:
        return "up"
    if last < ma * 0.995:
        return "down"
    return "flat"


def _compute_dist_from_ma(closes: list, window: int = 20) -> Optional[float]:
    """当前价相对 MA(window) 的偏离 %（正=高于均线）。"""
    if not closes or len(closes) < window + 1:
        return None
    ma = sum(closes[-window:]) / window
    last = closes[-1]
    if ma <= 0:
        return None
    return (last - ma) / ma * 100


def _classify_breadth(change_24h: float, dist_ma20: Optional[float],
                      hourly_trend: str, daily_trend: str) -> str:
    """
    方向无关的大盘状态分类。
    bullish: 多周期共振向上（风险偏好上升）
    risk_off: 多周期共振向下（避险）
    neutral: 信号不一致 / 震荡
    """
    bullish_signals = 0
    bearish_signals = 0

    if change_24h >= BREADTH_CHANGE_THRESHOLD:
        bullish_signals += 1
    elif change_24h <= -BREADTH_CHANGE_THRESHOLD:
        bearish_signals += 1

    if dist_ma20 is not None:
        if dist_ma20 > 0.5:
            bullish_signals += 1
        elif dist_ma20 < -0.5:
            bearish_signals += 1

    if hourly_trend == "up":
        bullish_signals += 1
    elif hourly_trend == "down":
        bearish_signals += 1

    if daily_trend == "up":
        bullish_signals += 1
    elif daily_trend == "down":
        bearish_signals += 1

    # 至少 3 个信号一致才表态（避免噪音）
    if bullish_signals >= 3 and bearish_signals == 0:
        return "bullish"
    if bearish_signals >= 3 and bullish_signals == 0:
        return "risk_off"
    return "neutral"


def get_btc_trend(force_refresh: bool = False) -> Dict[str, Any]:
    """
    获取 BTC 大盘状态。5 分钟缓存。
    返回:
    {
        "change_24h": float,       # 24h 涨跌幅 %
        "change_7d": float,        # 7d 涨跌幅 %
        "dist_from_ma20": float,   # 相对日线 MA20 偏离 %
        "hourly_trend": str,       # up/down/flat
        "daily_trend": str,        # up/down/flat
        "market_breadth": str,     # bullish/neutral/risk_off
        "ts": float,
    }
    失败时返回 neutral 默认值。
    """
    now = time.time()
    with _cache_lock:
        if (
            not force_refresh
            and _cache["value"] is not None
            and now - _cache["ts"] < CACHE_TTL_SEC
        ):
            return _cache["value"]

    result: Dict[str, Any] = {
        "change_24h": 0.0,
        "change_7d": 0.0,
        "dist_from_ma20": None,
        "hourly_trend": "flat",
        "daily_trend": "flat",
        "market_breadth": "neutral",
        "ts": now,
        "source": "cache",
    }

    try:
        from app.services.data_service import get_multi_timeframe_klines
        from app.skills.analysis_skills.quantitative import _parse_kline

        tf = get_multi_timeframe_klines("BTC", (1, 2))
        hourly = _parse_kline(tf.get("hourly_72h", {}), None) or {}
        daily = _parse_kline(tf.get("daily_60d", {}), None) or {}

        h_closes = hourly.get("closes", []) or []
        d_closes = daily.get("closes", []) or []

        if len(h_closes) >= 24:
            change_24h = (h_closes[-1] - h_closes[-24]) / h_closes[-24] * 100
            result["change_24h"] = round(change_24h, 3)
            result["hourly_trend"] = _compute_trend(h_closes, window=24)

        if len(d_closes) >= 7:
            change_7d = (d_closes[-1] - d_closes[-7]) / d_closes[-7] * 100
            result["change_7d"] = round(change_7d, 3)

        if len(d_closes) >= 21:
            result["dist_from_ma20"] = round(_compute_dist_from_ma(d_closes, 20) or 0, 3)
            result["daily_trend"] = _compute_trend(d_closes, window=20)

        result["market_breadth"] = _classify_breadth(
            result["change_24h"],
            result["dist_from_ma20"],
            result["hourly_trend"],
            result["daily_trend"],
        )
        result["source"] = "live"

    except Exception as e:
        logger.warning(f"获取 BTC trend 失败，返回 neutral 默认: {type(e).__name__}: {e}")

    with _cache_lock:
        _cache["value"] = result
        _cache["ts"] = now
    return result


def get_market_breadth() -> str:
    """便捷接口：直接返回 'bullish' / 'neutral' / 'risk_off'。"""
    return get_btc_trend().get("market_breadth", "neutral")
