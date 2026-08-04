"""
均值回归 alpha（Phase 4 Layer 1）— 双向，方向由 regime + breadth 决定。

设计原则（行情无关）：
- 触发器本身是中性的，pick_direction 根据 regime 决定方向
- 震荡市选超跌反弹 / 超涨回落，趋势市不出
- regime 不匹配时返回 None（不出该 alpha）

触发逻辑（双向对称）：
- LONG 候选：RSI(14) < 30（超跌）+ 4h 阳包阴（reversal 信号）
- SHORT 候选：RSI(14) > 70（超涨）+ 4h 阴包阳（reversal 信号）
- 量能放大加分（反转更可信）

pick_direction 规则：
- regime=mean_reverting + breadth≠risk_off → LONG 候选
- regime=mean_reverting + breadth≠bullish → SHORT 候选
- regime=volatile + breadth 同上 → 也可
- 其他 → None
"""
from typing import Any, Dict, Optional

from app.signals.models import SignalSource, SignalDirection
from app.utils.logger import get_logger

logger = get_logger(__name__)

RSI_PERIOD = 14
RSI_OVERSOLD = 30.0      # LONG 触发阈值
RSI_OVERBOUGHT = 70.0    # SHORT 触发阈值
BASE_WEIGHT = 0.20


def _compute_rsi(closes: list, period: int = RSI_PERIOD) -> Optional[float]:
    """Wilder's RSI。"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _safe_ma(values, window):
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def evaluate(
    coin: str,
    ohlcv: Dict[str, list],
    regime: str,
    market_breadth: str = "neutral",
    kline_4h: Optional[Dict[str, list]] = None,
) -> Optional[SignalSource]:
    """
    评估均值回归 alpha。返回 SignalSource 或 None。

    Args:
        ohlcv: {closes, highs, lows, volumes} 字典（1d 或 4h）
        regime: 当前 market_regime（detect_regime 输出）
        market_breadth: bullish/neutral/risk_off
        kline_4h: 可选，4h K线用于反转确认（阳包阴 / 阴包阳）
                  形如 {closes, opens, highs, lows}
    """
    closes = ohlcv.get("closes") or []
    volumes = ohlcv.get("volumes") or []

    if len(closes) < RSI_PERIOD + 5:
        return None

    rsi = _compute_rsi(closes)
    if rsi is None:
        return None

    # LONG 候选：RSI 超跌
    long_candidate = rsi < RSI_OVERSOLD
    # SHORT 候选：RSI 超涨
    short_candidate = rsi > RSI_OVERBOUGHT

    if not long_candidate and not short_candidate:
        return None

    # 方向决策（带候选信息：mean_reversion 的 long/short 候选互斥，必须让 pick_direction 知道）
    direction = pick_direction(regime, market_breadth, long_candidate, short_candidate)
    if direction is None:
        return None

    # 4h 反转确认（可选加分项）
    reversal_confirmed = False
    if kline_4h:
        k4_closes = kline_4h.get("closes") or []
        k4_opens = kline_4h.get("opens") or []
        if len(k4_closes) >= 2 and len(k4_opens) >= 2:
            cur_close = k4_closes[-1]
            cur_open = k4_opens[-1]
            prev_close = k4_closes[-2]
            prev_open = k4_opens[-2]
            if direction == SignalDirection.LONG:
                # 阳包阴：上一根阴线 + 当前阳线吞没
                reversal_confirmed = (
                    prev_close < prev_open
                    and cur_close > cur_open
                    and cur_close >= prev_open
                    and cur_open <= prev_close
                )
            else:
                # 阴包阳：上一根阳线 + 当前阴线吞没
                reversal_confirmed = (
                    prev_close > prev_open
                    and cur_close < cur_open
                    and cur_close <= prev_open
                    and cur_open >= prev_close
                )

    # 量能放大（反转更可信）
    vol_expansion = False
    if len(volumes) >= 6:
        recent_vol = sum(volumes[-3:]) / 3
        prior_vol = sum(volumes[-6:-3]) / 3
        if prior_vol > 0 and recent_vol > prior_vol * 1.2:
            vol_expansion = True

    # 评分
    base_score = 50.0
    if direction == SignalDirection.LONG:
        # RSI 越低分越高
        base_score += min(25, (RSI_OVERSOLD - rsi) * 2)
    else:
        base_score += min(25, (rsi - RSI_OVERBOUGHT) * 2)

    if reversal_confirmed:
        base_score += 15
    if vol_expansion:
        base_score += 10

    score = min(90, base_score)
    if score < 40:
        return None

    return SignalSource(
        name="alpha_mean_reversion",
        score=round(score, 1),
        weight=BASE_WEIGHT,
        direction=direction,
        detail=f"mean_reversion regime={regime} breadth={market_breadth} "
               f"rsi={rsi:.1f} reversal={reversal_confirmed} vol_expansion={vol_expansion}",
        extra={
            "regime": regime, "breadth": market_breadth,
            "rsi": round(rsi, 2),
            "reversal_confirmed": reversal_confirmed,
            "vol_expansion": vol_expansion,
        },
    )


def pick_direction(
    regime: str,
    market_breadth: str,
    long_candidate: bool = False,
    short_candidate: bool = False,
) -> Optional[SignalDirection]:
    """
    根据 regime + breadth + 候选 决定方向。None 表示不出该 alpha。
    双向对称：mean_reversion 的 long/short 候选互斥，根据 RSI 候选 + breadth 选方向。
    """
    regime = (regime or "").lower()
    breadth = (market_breadth or "neutral").lower()

    valid_regimes = {"mean_reverting", "volatile", "quiet"}
    if regime not in valid_regimes:
        return None

    # LONG 候选 + breadth 非 risk_off → LONG
    if long_candidate and breadth != "risk_off":
        return SignalDirection.LONG

    # SHORT 候选 + breadth 非 bullish → SHORT
    if short_candidate and breadth != "bullish":
        return SignalDirection.SHORT

    return None
