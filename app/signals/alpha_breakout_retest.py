"""
对称突破回踩 alpha（Phase 4 Layer 1）— 双向，方向由 regime + breadth 决定。

设计原则（行情无关）：
- 触发器本身是中性的，pick_direction 根据 regime 决定方向
- 牛市选 LONG，熊市选 SHORT，行情切换自动适配
- regime 不匹配时返回 None（不出该 alpha）

触发逻辑：
- 找最近 N 日（默认 60）的高点 / 低点
- 若当前价距高点 <2% 且近 3 日有回调（high 不再创新高）→ LONG 候选（突破后回踩）
- 若当前价距低点 <2% 且近 3 日有反弹（low 不再创新低）→ SHORT 候选（破位后反弹）
- 量能收缩加分（最近 3 日成交量低于前 3 日）

pick_direction 规则：
- regime=trending_up_breakout + breadth≠risk_off → LONG
- regime=trending_down_breakdown + breadth≠bullish → SHORT
- regime=trending_up（未细分）+ breadth=bullish → LONG
- regime=trending_down（未细分）+ breadth=risk_off → SHORT
- 其他 → None
"""
from typing import Any, Dict, Optional

from app.signals.models import SignalSource, SignalDirection
from app.utils.logger import get_logger

logger = get_logger(__name__)

WINDOW = 60
DIST_THRESHOLD = 2.0  # 距极值 <2% 视为"接近突破"
BASE_WEIGHT = 0.20  # 该 alpha 在 sources 加权中的默认权重


def _safe_ma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def evaluate(
    coin: str,
    ohlcv: Dict[str, list],
    regime: str,
    market_breadth: str = "neutral",
) -> Optional[SignalSource]:
    """
    评估突破回踩 alpha。返回 SignalSource 或 None。

    Args:
        ohlcv: {closes, highs, lows, volumes} 字典
        regime: 当前 market_regime（detect_regime 输出）
        market_breadth: bullish/neutral/risk_off
    """
    closes = ohlcv.get("closes") or []
    highs = ohlcv.get("highs") or closes
    lows = ohlcv.get("lows") or closes
    volumes = ohlcv.get("volumes") or []

    if len(closes) < 30 or len(highs) < 30 or len(lows) < 30:
        return None

    window = min(WINDOW, len(closes))
    recent_high = max(highs[-window:])
    recent_low = min(lows[-window:])
    last = closes[-1]

    if recent_high <= 0 or recent_low <= 0:
        return None

    dist_from_high = (last - recent_high) / recent_high * 100  # 负值
    dist_from_low = (last - recent_low) / recent_low * 100     # 正值

    # 突破后回踩判断：最近 3 天没有创新高（high 不再创新高 = 回踩）
    last_3_highs = highs[-3:] if len(highs) >= 3 else highs
    last_3_lows = lows[-3:] if len(lows) >= 3 else lows
    no_new_high = max(last_3_highs) < recent_high * 0.999
    no_new_low = min(last_3_lows) > recent_low * 1.001

    # 量能收缩（最近 3 日 vol 低于前 3 日）
    vol_contracted = False
    if len(volumes) >= 6:
        recent_vol = sum(volumes[-3:]) / 3
        prior_vol = sum(volumes[-6:-3]) / 3
        if prior_vol > 0 and recent_vol < prior_vol * 0.9:
            vol_contracted = True

    long_candidate = dist_from_high > -DIST_THRESHOLD and no_new_high
    short_candidate = dist_from_low < DIST_THRESHOLD and no_new_low

    if not long_candidate and not short_candidate:
        return None

    # 决定方向（行情无关：根据 regime + breadth）
    direction = pick_direction(regime, market_breadth)
    if direction is None:
        return None

    # 如果 regime 让 long 候选走 short 方向（或反之），跳过
    if direction == SignalDirection.LONG and not long_candidate:
        return None
    if direction == SignalDirection.SHORT and not short_candidate:
        return None

    # 评分
    base_score = 50.0
    if direction == SignalDirection.LONG:
        # 越接近高点分越高（突破信号越强）
        base_score += min(20, -dist_from_high * 2)
    else:
        base_score += min(20, dist_from_low * 2)

    if vol_contracted:
        base_score += 10  # 量能收缩加分

    score = min(90, base_score)
    if score < 40:
        return None

    return SignalSource(
        name="alpha_breakout_retest",
        score=round(score, 1),
        weight=BASE_WEIGHT,
        direction=direction,
        detail=f"breakout_retest regime={regime} breadth={market_breadth} "
               f"dist_high={dist_from_high:+.1f}% dist_low={dist_from_low:+.1f}% "
               f"vol_contracted={vol_contracted}",
        raw={
            "regime": regime, "breadth": market_breadth,
            "dist_from_high": round(dist_from_high, 2),
            "dist_from_low": round(dist_from_low, 2),
        },
    )


def pick_direction(regime: str, market_breadth: str) -> Optional[SignalDirection]:
    """
    根据 regime + breadth 决定方向。None 表示不出该 alpha。
    双向对称：牛市优先 LONG，熊市优先 SHORT，震荡不出。
    """
    regime = (regime or "").lower()
    breadth = (market_breadth or "neutral").lower()

    # LONG 触发：突破初期 + 大盘非避险
    long_regimes = {"trending_up_breakout", "trending_up"}
    if regime in long_regimes and breadth != "risk_off":
        return SignalDirection.LONG

    # SHORT 触发：破位初期 + 大盘非狂热
    short_regimes = {"trending_down_breakdown", "trending_down"}
    if regime in short_regimes and breadth != "bullish":
        return SignalDirection.SHORT

    return None
