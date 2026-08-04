"""
资金费率 alpha（Phase 4 Layer 1）— 双向，方向由 regime + breadth 决定。

设计原则（行情无关）：
- 触发器本身是中性的，pick_direction 根据 regime 决定方向
- 极端持仓（多头/空头拥挤）→ 反向交易（squeeze play）
- regime 不匹配时返回 None

触发逻辑（双向对称）：
- LONG 候选：资金费率均值 < -0.05%（空头拥挤，轧空概率高）
- SHORT 候选：资金费率均值 > 0.05%（多头拥挤，多杀多概率高）
- 多交易所一致性加分（>=4 个一致方向）

pick_direction 规则：
- regime=volatile + breadth≠risk_off → LONG 候选
- regime=volatile + breadth≠bullish → SHORT 候选
- regime=mean_reverting 同上
- 其他 → None（趋势市不出，避免逆势）
"""
import os
from typing import Any, Dict, Optional

from app.signals.models import SignalSource, SignalDirection
from app.utils.logger import get_logger

logger = get_logger(__name__)

EXTREME_NEG = -0.05  # 空头拥挤阈值（%）
EXTREME_POS = 0.05   # 多头拥挤阈值（%）
BASE_WEIGHT = 0.15


def _parse_pct(s: Any) -> Optional[float]:
    """解析 '0.0054%' / '0.0054' / 0.0054 → float 百分比。"""
    if s is None:
        return None
    try:
        if isinstance(s, str):
            s = s.strip().rstrip("%")
        return float(s)
    except (ValueError, TypeError):
        return None


def _aggregate_funding(funding_data: Dict) -> Dict[str, Any]:
    """
    funding_data 来自 data_service.get_funding_rate，形如：
    {exchanges: {coinbase: "0.0017%", binance: "0.0054%", ...}}

    返回 {avg, median, min, max, n_long, n_short}
    """
    exchanges = funding_data.get("exchanges") or {}
    values = []
    for v in exchanges.values():
        p = _parse_pct(v)
        if p is not None:
            values.append(p)
    if not values:
        return {"avg": None}

    values.sort()
    n = len(values)
    avg = sum(values) / n
    median = values[n // 2]
    n_long = sum(1 for v in values if v > 0)
    n_short = sum(1 for v in values if v < 0)
    return {
        "avg": avg,
        "median": median,
        "min": values[0],
        "max": values[-1],
        "n": n,
        "n_long": n_long,
        "n_short": n_short,
    }


def evaluate(
    coin: str,
    regime: str,
    market_breadth: str = "neutral",
    funding_data: Optional[Dict] = None,
) -> Optional[SignalSource]:
    """
    评估资金费率 alpha。返回 SignalSource 或 None。

    Args:
        coin: 币种
        regime: 当前 market_regime
        market_breadth: bullish/neutral/risk_off
        funding_data: 可选预取的 funding_rate 数据（避免重复请求）
    """
    if funding_data is None:
        try:
            from app.services.data_service import get_funding_rate
            funding_data = get_funding_rate(coin)
        except Exception as e:
            logger.warning(f"alpha_funding_rate 获取数据失败 {coin}: {type(e).__name__}: {e}")
            return None

    agg = _aggregate_funding(funding_data)
    avg = agg.get("avg")
    if avg is None:
        return None

    # 候选判断
    long_candidate = avg < EXTREME_NEG  # 空头拥挤 → 轧空 → LONG
    short_candidate = avg > EXTREME_POS  # 多头拥挤 → 多杀多 → SHORT

    if not long_candidate and not short_candidate:
        return None

    # 方向决策（带候选信息）
    direction = pick_direction(regime, market_breadth, long_candidate, short_candidate)
    if direction is None:
        return None

    # 多交易所一致性加分（>=4 个一致方向）
    consistency_bonus = 0
    if agg.get("n", 0) >= 4:
        if direction == SignalDirection.LONG and agg.get("n_short", 0) >= 4:
            consistency_bonus = 10
        elif direction == SignalDirection.SHORT and agg.get("n_long", 0) >= 4:
            consistency_bonus = 10

    # 评分：偏离阈值越远分越高
    if direction == SignalDirection.LONG:
        # avg 越负越好（空头越拥挤）
        magnitude = min(25, abs(avg - EXTREME_NEG) * 5 + 5)
    else:
        magnitude = min(25, (avg - EXTREME_POS) * 5 + 5)

    base_score = 50.0 + magnitude + consistency_bonus
    score = min(85, base_score)
    if score < 40:
        return None

    return SignalSource(
        name="alpha_funding_rate",
        score=round(score, 1),
        weight=BASE_WEIGHT,
        direction=direction,
        detail=f"funding_rate regime={regime} breadth={market_breadth} "
               f"avg={avg:+.4f}% med={agg.get('median'):+.4f}% "
               f"n_long={agg.get('n_long')}/n_short={agg.get('n_short')}",
        extra={
            "regime": regime, "breadth": market_breadth,
            "avg": round(avg, 4), "median": round(agg.get("median") or 0, 4),
            "n_long": agg.get("n_long", 0), "n_short": agg.get("n_short", 0),
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
    双向对称：只在高波动 / 震荡市出（趋势市避免逆势）。
    """
    regime = (regime or "").lower()
    breadth = (market_breadth or "neutral").lower()

    valid_regimes = {"volatile", "mean_reverting", "quiet"}
    if regime not in valid_regimes:
        return None

    if long_candidate and breadth != "risk_off":
        return SignalDirection.LONG

    if short_candidate and breadth != "bullish":
        return SignalDirection.SHORT

    return None
