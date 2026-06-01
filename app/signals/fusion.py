"""
交易信号卡 - 多维信号融合引擎（数学推导 + 自适应权重版）

升级点：
1. 自适应权重替代静态权重
2. 技术分析信号源升级（多时间框架 + 专业指标）
3. 集成第一性原理数学推导
4. 市场状态感知的策略参数
"""
import math
from typing import Optional, List
from datetime import datetime

from app.signals.models import (
    SignalCard, SignalSource, SignalDirection,
    SignalGrade, SignalStatus, MathDerivationSummary, StrategyMeta,
)
from app.signals.math_engine import run_math_derivation
from app.signals.adaptive_strategy import get_strategy_engine
import app.bigorder.deps as bigorder_deps
from config.settings import settings


def _score_to_direction(score: float) -> SignalDirection:
    if score >= 50:
        return SignalDirection.LONG
    elif score <= -50:
        return SignalDirection.SHORT
    return SignalDirection.NEUTRAL


def _bigorder_source(coin: str, weight: float) -> Optional[SignalSource]:
    """获取大单异动信号源"""
    cached = None
    if bigorder_deps.scorer:
        cached = bigorder_deps.scorer.get_coin_signal(coin.upper())
    else:
        try:
            import redis
            r = redis.Redis(
                host=settings.redis_host, port=settings.redis_port,
                db=settings.redis_db, password=settings.redis_password or None,
                decode_responses=True, socket_connect_timeout=3,
            )
            data = r.hgetall(f"signal:coin:{coin.upper()}")
            if data:
                data.pop("llm_analysis", None)
                cached = data
        except Exception:
            pass

    if not cached:
        return None

    total_score = float(cached.get("total_score", 0))
    level = cached.get("level", "none")
    net_flow = float(cached.get("net_flow", 0))
    exchange = cached.get("exchange", "")
    buy_amount = float(cached.get("buy_amount", 0))
    sell_amount = float(cached.get("sell_amount", 0))

    if net_flow > 0 and buy_amount > sell_amount * 1.5:
        direction = SignalDirection.LONG
        normalized = total_score
    elif net_flow < 0 and sell_amount > buy_amount * 1.5:
        direction = SignalDirection.SHORT
        normalized = total_score
    else:
        direction = SignalDirection.NEUTRAL
        normalized = total_score * 0.5

    detail = (
        f"{exchange} {level}信号, 净流入{net_flow:+,.0f}, "
        f"买{buy_amount:,.0f}/卖{sell_amount:,.0f}"
    )

    return SignalSource(
        name="bigorder_anomaly",
        score=normalized,
        direction=direction,
        weight=weight,
        detail=detail,
    )


def _quantitative_source(coin: str, weight: float) -> Optional[SignalSource]:
    """获取量化六因子信号源"""
    try:
        from app.skills.analysis_skills.quantitative import QuantitativeAnalysisSkill
        from app.services.data_service import get_kline_data, get_trade_volume
    except ImportError:
        return None

    try:
        kline = get_kline_data(coin, 2)
        volume = get_trade_volume(coin)
        raw_data = {"get_kline_data": kline, "get_trade_volume": volume}

        skill = QuantitativeAnalysisSkill()
        result = skill.analyze(coin, raw_data)
    except Exception:
        return None

    if not result or result.get("error") or not result.get("signal"):
        return None

    signal = result["signal"]
    composite = signal.get("composite_score", 0)
    direction_str = signal.get("direction", "neutral")
    strength = signal.get("strength", "none")
    confidence = signal.get("confidence_pct", 0)

    direction_map = {
        "long": SignalDirection.LONG,
        "short": SignalDirection.SHORT,
        "neutral": SignalDirection.NEUTRAL,
    }

    return SignalSource(
        name="quantitative",
        score=abs(composite),
        direction=direction_map.get(direction_str, SignalDirection.NEUTRAL),
        weight=weight,
        detail=f"综合评分{composite:.0f}, 强度{strength}, 置信度{confidence}%",
    )


def _technical_source(ohlcv: dict, weight: float) -> Optional[SignalSource]:
    """
    技术分析信号源（专业升级版）

    从简单的 MA7/MA20 升级为：
    1. EMA 多头/空头排列 (9/21/55)
    2. ADX 趋势强度过滤
    3. RSI 超买超卖 + 背离检测
    4. MACD 方向 + 动量
    5. 布林带位置
    6. Supertrend 方向
    7. 量价确认 (OBV 趋势)
    """
    closes = ohlcv.get("closes", [])
    highs = ohlcv.get("highs", [])
    lows = ohlcv.get("lows", [])
    volumes = ohlcv.get("volumes", [])

    if len(closes) < 55:
        return None

    from app.skills.analysis_skills.indicators import (
        ema, ema_triple, adx, supertrend,
        rsi, macd, detect_divergence,
        obv, bollinger_bands, atr,
    )

    price = closes[-1]
    score = 0
    details = []

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    # ── 1. EMA 排列 (权重 25%) ─────────────────────────────
    e9, e21, e55 = ema_triple(closes, 9, 21, 55)
    e9_v, e21_v, e55_v = last_valid(e9), last_valid(e21), last_valid(e55)

    if e9_v and e21_v and e55_v:
        bull_stack = e9_v > e21_v > e55_v
        bear_stack = e9_v < e21_v < e55_v

        if bull_stack:
            score += 25
            details.append(f"EMA多头排列(9>{21}>{55})")
        elif bear_stack:
            score -= 25
            details.append(f"EMA空头排列(9<{21}<{55})")
        elif e9_v > e21_v:
            score += 10
            details.append("短期EMA偏多")
        elif e9_v < e21_v:
            score -= 10
            details.append("短期EMA偏空")

    # ── 2. ADX 趋势强度 (权重 15%) ─────────────────────────
    adx_vals, pdi, mdi = adx(highs, lows, closes, 14)
    adx_v = last_valid(adx_vals)
    pdi_v = last_valid(pdi)
    mdi_v = last_valid(mdi)

    if adx_v is not None:
        if adx_v > 25:  # 趋势有效
            if pdi_v and mdi_v and pdi_v > mdi_v:
                score += 15
                details.append(f"ADX={adx_v:.0f}趋势强+DI多头")
            elif pdi_v and mdi_v and mdi_v > pdi_v:
                score -= 15
                details.append(f"ADX={adx_v:.0f}趋势强+DI空头")
        elif adx_v < 20:
            details.append(f"ADX={adx_v:.0f}震荡市")

    # ── 3. RSI + 背离 (权重 20%) ─────────────────────────────
    rsi_vals = rsi(closes, 14)
    rsi_v = last_valid(rsi_vals)

    if rsi_v is not None:
        if rsi_v >= 75:
            score -= 20
            details.append(f"RSI={rsi_v:.0f}严重超买")
        elif rsi_v >= 65:
            score -= 10
            details.append(f"RSI={rsi_v:.0f}超买区")
        elif rsi_v >= 55:
            score += 10
            details.append(f"RSI={rsi_v:.0f}偏强")
        elif rsi_v <= 25:
            score += 20
            details.append(f"RSI={rsi_v:.0f}严重超卖")
        elif rsi_v <= 35:
            score += 10
            details.append(f"RSI={rsi_v:.0f}超卖区")
        elif rsi_v <= 45:
            score -= 10
            details.append(f"RSI={rsi_v:.0f}偏弱")

        # 背离检测
        div = detect_divergence(closes, rsi_vals, 20)
        if div == "bullish_divergence":
            score += 15
            details.append("RSI底背离")
        elif div == "bearish_divergence":
            score -= 15
            details.append("RSI顶背离")

    # ── 4. MACD (权重 15%) ──────────────────────────────────
    macd_line, sig_line, hist = macd(closes, 12, 26, 9)
    macd_v = last_valid(macd_line)
    hist_v = last_valid(hist)

    if hist_v is not None and macd_v is not None:
        if hist_v > 0 and macd_v > 0:
            score += 15
            details.append("MACD金叉+零轴上方")
        elif hist_v > 0:
            score += 8
            details.append("MACD柱正值")
        elif hist_v < 0 and macd_v < 0:
            score -= 15
            details.append("MACD死叉+零轴下方")
        elif hist_v < 0:
            score -= 8
            details.append("MACD柱负值")

        # MACD 背离
        macd_div = detect_divergence(closes, macd_line, 20)
        if macd_div == "bullish_divergence":
            score += 10
            details.append("MACD底背离")
        elif macd_div == "bearish_divergence":
            score -= 10
            details.append("MACD顶背离")

    # ── 5. 布林带位置 (权重 10%) ─────────────────────────────
    bb_up, bb_mid, bb_low = bollinger_bands(closes, 20, 2.0)
    bb_u = last_valid(bb_up)
    bb_l = last_valid(bb_low)

    if bb_u and bb_l:
        bb_range = bb_u - bb_l
        if bb_range > 0:
            bb_pos = (price - bb_l) / bb_range * 100
            if bb_pos >= 90:
                score -= 10
                details.append(f"BB上轨({bb_pos:.0f}%)")
            elif bb_pos <= 10:
                score += 10
                details.append(f"BB下轨({bb_pos:.0f}%)")
            elif 40 <= bb_pos <= 60:
                details.append(f"BB中部({bb_pos:.0f}%)")

    # ── 6. Supertrend (权重 10%) ─────────────────────────────
    st_line, st_dir = supertrend(highs, lows, closes, 10, 3.0)
    if st_dir[-1] == 1:
        score += 10
        details.append("Supertrend多头")
    elif st_dir[-1] == -1:
        score -= 10
        details.append("Supertrend空头")

    # ── 7. OBV 量价确认 (权重 5%) ───────────────────────────
    if volumes and len(volumes) >= len(closes):
        obv_vals = obv(closes, volumes)
        obv_ema = ema(obv_vals, 10)
        obv_now = last_valid(obv_ema)

        # OBV 趋势判断
        count = 0
        obv_prev = None
        for v in reversed(obv_ema):
            if v is not None and not math.isnan(v):
                count += 1
                if count == 6:
                    obv_prev = v
                    break

        if obv_now and obv_prev:
            price_up = closes[-1] > closes[-5] if len(closes) >= 6 else None
            obv_up = obv_now > obv_prev

            if obv_up and price_up:
                score += 5
                details.append("量价齐升")
            elif not obv_up and price_up:
                score -= 5
                details.append("量价背离")

    # 限幅
    score = max(-100, min(100, score))

    direction = (
        SignalDirection.LONG if score > 0 else
        SignalDirection.SHORT if score < 0 else
        SignalDirection.NEUTRAL
    )

    detail_str = ", ".join(details) if details else "技术指标信号不明确"

    return SignalSource(
        name="technical",
        score=abs(score),
        direction=direction,
        weight=weight,
        detail=detail_str,
    )


def fuse_signals(coin: str, ohlcv: dict, raw_data: dict) -> Optional[SignalCard]:
    """
    多维信号融合，生成交易信号卡（数学推导 + 自适应权重版）
    """
    # ── 获取自适应引擎 ──────────────────────────────────────────
    engine = get_strategy_engine()

    # ── 先做数学推导（用于获取市场状态）──────────────────────────
    closes = ohlcv.get("closes", [])
    math_result = None
    regime = "quiet"

    if len(closes) >= 60:
        math_result = run_math_derivation(closes, direction="long")
        if math_result and math_result.regime:
            regime = math_result.regime.regime

    # ── 获取自适应权重 ──────────────────────────────────────────
    adaptive_weights = engine.get_adaptive_weights(regime)
    regime_params = engine.get_regime_params(regime)

    # ── 收集信号源 ──────────────────────────────────────────────
    sources: List[SignalSource] = []

    bigorder_src = _bigorder_source(coin, adaptive_weights.get("bigorder_anomaly", 0.35))
    if bigorder_src:
        sources.append(bigorder_src)

    quant_src = _quantitative_source(coin, adaptive_weights.get("quantitative", 0.35))
    if quant_src:
        sources.append(quant_src)

    tech_src = _technical_source(ohlcv, adaptive_weights.get("technical", 0.30))
    if tech_src:
        sources.append(tech_src)

    if len(sources) < 2:
        return None

    # ── 融合计算 ──────────────────────────────────────────────
    direction_score = sum(
        (1 if s.direction == SignalDirection.LONG else -1 if s.direction == SignalDirection.SHORT else 0) * s.score * s.weight
        for s in sources
    )
    total_weight = sum(s.weight for s in sources)
    weighted_direction = direction_score / total_weight if total_weight > 0 else 0

    confidence = min(95, sum(s.score * s.weight for s in sources) / total_weight * 100) if total_weight > 0 else 0

    # ── 数学推导修正 ──────────────────────────────────────────
    math_adjustment = 0.0
    if math_result:
        math_adjustment = math_result.math_score_adjustment
        confidence = min(95, confidence + math_adjustment * 0.3)

    if weighted_direction > 15:
        direction = SignalDirection.LONG
    elif weighted_direction < -15:
        direction = SignalDirection.SHORT
    else:
        return None

    # 方向一致性
    consistent_count = sum(1 for s in sources if s.direction == direction)
    if consistent_count < 2:
        return None

    # ── 数学推导二次确认 ──────────────────────────────────────
    if math_result and len(closes) >= 60:
        # 重新计算方向相关的数学推导
        dir_str = "long" if direction == SignalDirection.LONG else "short"
        math_result = run_math_derivation(closes, direction=dir_str)

        # 如果数学推导强烈反对，提升门槛（仅极高负分+低置信度才否决）
        if math_result.math_score_adjustment < -50:
            if confidence < 45:
                return None

    # ── 信号等级 ──────────────────────────────────────────────
    math_confirms = math_result and math_result.math_score_adjustment > 15
    if len(sources) >= 3 and consistent_count >= 3 and confidence >= 65 and math_confirms:
        grade = SignalGrade.S
    elif len(sources) >= 3 and consistent_count >= 3 and confidence >= 65:
        grade = SignalGrade.A
    elif consistent_count >= 2 and confidence >= 50:
        grade = SignalGrade.A
    else:
        grade = SignalGrade.B

    # ── 价格计算 ──────────────────────────────────────────────
    price = ohlcv["closes"][-1] if ohlcv.get("closes") else 0
    if price <= 0:
        return None

    # ATR
    from app.skills.analysis_skills.indicators import atr as calc_atr
    atr_vals = calc_atr(ohlcv.get("highs", closes), ohlcv.get("lows", closes), closes, 14)

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    atr_v = last_valid(atr_vals) or (price * 0.02)

    # 使用自适应策略参数
    sl_mult = regime_params.get("stop_loss_atr_mult", 1.5)
    tp_mult = regime_params.get("tp_atr_mult", 3.0)

    if direction == SignalDirection.LONG:
        entry_low = max(price * 0.995, price - 0.3 * atr_v)
        entry_high = price + 0.3 * atr_v
        stop_loss = price - sl_mult * atr_v
        take_profit = price + tp_mult * atr_v
        invalidation = stop_loss
    else:
        entry_low = price - 0.3 * atr_v
        entry_high = min(price * 1.005, price + 0.3 * atr_v)
        stop_loss = price + sl_mult * atr_v
        take_profit = price - tp_mult * atr_v
        invalidation = stop_loss

    risk = abs(price - stop_loss)
    reward = abs(take_profit - price)
    risk_reward = round(reward / risk, 1) if risk > 0 else 0

    if risk_reward < 1.0:
        return None

    # ── Kelly 仓位 ────────────────────────────────────────────
    base_position = {"S": 8.0, "A": 5.0, "B": 3.0}.get(grade, 3.0)
    kelly_adj = math_result.kelly_fraction if math_result else 0
    position_pct = base_position
    if kelly_adj > 0:
        position_pct = min(10.0, base_position * (1 + kelly_adj))
    elif kelly_adj == 0 and math_result:
        position_pct = max(1.0, base_position * 0.5)

    # ── 构建数学推导摘要 ──────────────────────────────────────
    math_summary = None
    if math_result:
        math_summary = MathDerivationSummary(
            hurst=math_result.hurst.hurst if math_result.hurst else None,
            hurst_interpretation=math_result.hurst.interpretation if math_result.hurst else "",
            entropy_predictability=math_result.entropy.predictability if math_result.entropy else None,
            kelly_fraction=math_result.kelly_fraction,
            monte_carlo_bull_prob=math_result.monte_carlo.bull_prob if math_result.monte_carlo else None,
            monte_carlo_bear_prob=math_result.monte_carlo.bear_prob if math_result.monte_carlo else None,
            monte_carlo_var95=math_result.monte_carlo.var_95 if math_result.monte_carlo else None,
            vol_regime=math_result.vol_cone.regime if math_result.vol_cone else "",
            vol_percentile=math_result.vol_cone.percentile if math_result.vol_cone else None,
            market_regime=math_result.regime.regime if math_result.regime else "",
            market_regime_confidence=math_result.regime.confidence if math_result.regime else None,
            math_score_adjustment=math_result.math_score_adjustment,
            math_confidence=math_result.math_confidence,
            key_findings=math_result.key_findings,
        )

    # ── 策略元数据 ────────────────────────────────────────────
    strategy_meta = StrategyMeta(
        strategy_version=engine.state.version,
        regime=regime,
        adaptive_weights=adaptive_weights,
        global_win_rate=engine.state.global_win_rate,
        evolution_count=len(engine.state.evolution_history),
    )

    # ── 生成信号卡 ────────────────────────────────────────────
    engine.increment_generated()

    return SignalCard(
        coin=coin.upper(),
        direction=direction,
        grade=grade,
        current_price=price,
        entry_low=round(entry_low, 4),
        entry_high=round(entry_high, 4),
        stop_loss=round(stop_loss, 4),
        take_profit=round(take_profit, 4),
        risk_reward_ratio=risk_reward,
        confidence=round(confidence, 1),
        sources=sources,
        position_pct=round(position_pct, 1),
        invalidation_price=round(invalidation, 4),
        math=math_summary,
        strategy=strategy_meta,
        status=SignalStatus.PENDING,
    )


def generate_card_for_chat(coin: str, tier: str = "pro") -> Optional[dict]:
    """
    供 Agent 对话流调用的信号卡生成器

    返回 signal_card 事件数据（前端直接渲染为卡片）或 None
    """
    from app.services.data_service import get_kline_data as _get_kline
    from app.skills.analysis_skills.quantitative import _parse_kline
    from app.signals.backtest import backtest_signal

    try:
        kline_data = _get_kline(coin, 2)
        ohlcv = _parse_kline(kline_data)
        if not ohlcv:
            return None

        signal_card = fuse_signals(coin, ohlcv, {})
        if not signal_card:
            return None

        bt = backtest_signal(coin, signal_card.direction, signal_card.grade)
        if bt:
            signal_card.win_rate = bt["win_rate"]
            signal_card.sample_count = bt["sample_count"]
            signal_card.avg_profit_pct = bt["avg_profit_pct"]

        event_data = _build_card_event(signal_card, bt, tier)
        event_data["display"] = signal_card.format_card()
        return event_data

    except Exception:
        return None


def _build_card_event(signal_card: SignalCard, bt_result: dict, tier: str) -> dict:
    """构建前端渲染用的 signal_card 事件数据"""
    card = {
        "coin": signal_card.coin,
        "direction": signal_card.direction.value,
        "grade": signal_card.grade.value,
        "confidence": signal_card.confidence,
        "current_price": signal_card.current_price,
        "entry_zone": [signal_card.entry_low, signal_card.entry_high],
        "stop_loss": signal_card.stop_loss,
        "take_profit": signal_card.take_profit,
        "risk_reward": signal_card.risk_reward_ratio,
        "position_pct": signal_card.position_pct,
        "invalidation": signal_card.invalidation_price,
        "sources": [
            {"name": s.name, "score": round(s.score, 1),
             "direction": s.direction.value, "detail": s.detail}
            for s in signal_card.sources
        ],
    }

    event = {"type": "signal_card", "card": card, "tier": tier}

    if signal_card.math:
        if tier == "pro":
            event["math"] = {
                "hurst": signal_card.math.hurst,
                "hurst_interp": signal_card.math.hurst_interpretation,
                "predictability": signal_card.math.entropy_predictability,
                "kelly": signal_card.math.kelly_fraction,
                "mc_bull_prob": signal_card.math.monte_carlo_bull_prob,
                "mc_bear_prob": signal_card.math.monte_carlo_bear_prob,
                "mc_var95": signal_card.math.monte_carlo_var95,
                "vol_regime": signal_card.math.vol_regime,
                "vol_percentile": signal_card.math.vol_percentile,
                "market_regime": signal_card.math.market_regime,
                "findings": signal_card.math.key_findings,
            }
        else:
            event["math"] = {
                "hurst": signal_card.math.hurst,
                "mc_bull_prob": signal_card.math.monte_carlo_bull_prob,
                "market_regime": signal_card.math.market_regime,
            }

    if bt_result:
        event["backtest"] = {
            "win_rate": bt_result.get("win_rate"),
            "sample_count": bt_result.get("sample_count"),
            "sharpe": bt_result.get("sharpe_ratio"),
        }

    if signal_card.strategy:
        event["strategy"] = {
            "version": signal_card.strategy.strategy_version,
            "regime": signal_card.strategy.regime,
            "global_win_rate": signal_card.strategy.global_win_rate,
        }

    return event
