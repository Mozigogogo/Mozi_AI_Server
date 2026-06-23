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
from app.utils.logger import get_logger

logger = get_logger("app.signals.fusion")


def _extract_realtime_price(raw_data: dict) -> Optional[float]:
    """从 raw_data/header 中提取统一实时价。"""
    if not isinstance(raw_data, dict):
        return None

    value = raw_data.get("current_price")
    if value is not None:
        try:
            price = float(value)
            if price > 0:
                return price
        except (ValueError, TypeError):
            pass

    header = raw_data.get("header") or raw_data.get("get_header_data") or raw_data.get("header_data")
    if isinstance(header, dict):
        for key in ("currentPrice", "current_price", "price"):
            value = header.get(key)
            if value is None:
                continue
            try:
                price = float(value)
                if price > 0:
                    return price
            except (ValueError, TypeError):
                continue
    return None


def _apply_realtime_price(ohlcv: dict, current_price: Optional[float]) -> dict:
    """用实时价替换最后一根 close，并扩展 high/low。"""
    if not ohlcv or not current_price or current_price <= 0:
        return ohlcv

    for key in ("highs", "lows", "closes"):
        if key not in ohlcv or not ohlcv[key]:
            return ohlcv

    ohlcv["closes"][-1] = float(current_price)
    ohlcv["highs"][-1] = max(float(ohlcv["highs"][-1]), float(current_price))
    ohlcv["lows"][-1] = min(float(ohlcv["lows"][-1]), float(current_price))
    ohlcv["realtime_price"] = float(current_price)
    ohlcv["price_source"] = "header.currentPrice"
    return ohlcv


def _score_to_direction(score: float) -> SignalDirection:
    if score >= 50:
        return SignalDirection.LONG
    elif score <= -50:
        return SignalDirection.SHORT
    return SignalDirection.NEUTRAL


def _bigorder_source(coin: str, weight: float, lang: str = "zh") -> Optional[SignalSource]:
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

    if lang == "en":
        detail = (
            f"{exchange} {level} signal, net flow {net_flow:+,.0f}, "
            f"buy {buy_amount:,.0f}/sell {sell_amount:,.0f}"
        )
    else:
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


def _quantitative_source(
    coin: str, weight: float, lang: str = "zh",
    entry_ohlcv: Optional[dict] = None,
) -> Optional[SignalSource]:
    """获取量化六因子信号源。

    entry_ohlcv: 上层（endpoints/scan_all_coins）已拉的 1h OHLCV，传入复用避免重复 HTTP 请求。
    """
    try:
        from app.skills.analysis_skills.quantitative import QuantitativeAnalysisSkill, _parse_kline
        from app.services.data_service import (
            get_header_data, get_kline_data, get_trade_volume,
            get_buy_sell_ratio, get_open_interest, get_funding_rate,
        )
    except ImportError:
        return None

    try:
        header = get_header_data(coin)
        kline = get_kline_data(coin, 2)
        volume = get_trade_volume(coin)

        # 1h K线 72 根（满足 ema_triple 需 55）。优先复用上层传入的 entry_ohlcv，否则自己拉
        ohlcv_1h = entry_ohlcv
        if ohlcv_1h is None:
            try:
                kline_1h = get_kline_data(coin, 1)
                ohlcv_1h = _parse_kline(kline_1h, min_bars=55)
            except Exception:
                pass

        # 资金数据（激活 capital 因子）— 任一失败不拖垮整张卡
        bs_ratio = oi = fr = None
        try: bs_ratio = get_buy_sell_ratio(coin)
        except Exception: pass
        try: oi = get_open_interest(coin)
        except Exception: pass
        try: fr = get_funding_rate(coin)
        except Exception: pass

        raw_data = {
            "get_header_data": header,
            "get_kline_data": kline,
            "get_trade_volume": volume,
            "get_buy_sell_ratio": bs_ratio,
            "get_open_interest": oi,
            "get_funding_rate": fr,
            "hourly_ohlcv": ohlcv_1h,
        }

        skill = QuantitativeAnalysisSkill()
        result = skill.analyze(coin, raw_data)
    except Exception:
        return None

    if not result or result.get("error"):
        return None

    signal = result.get("signal") or result.get("交易信号") or {}
    composite = signal.get("composite_score", signal.get("综合评分", 0))
    direction_str = signal.get("direction", signal.get("方向", "neutral"))
    strength = signal.get("strength", signal.get("强度", "none"))
    # Normalize Chinese strength to English for display
    strength_map = {"强": "strong", "中等": "moderate", "弱": "weak", "无": "none"}
    strength_en = strength_map.get(strength, strength)
    confidence = signal.get("confidence_pct", signal.get("胜率估计", 0))

    direction_alias = {
        "做多": "long",
        "long": "long",
        "做空": "short",
        "short": "short",
        "观望": "neutral",
        "neutral": "neutral",
    }
    direction_str = direction_alias.get(str(direction_str), "neutral")

    try:
        composite = float(composite)
    except (ValueError, TypeError):
        composite = 0

    if isinstance(confidence, str):
        try:
            confidence = float(confidence.replace("%", ""))
        except ValueError:
            confidence = 0

    # 多周期共振标记（前端信号卡直接看到分歧/共振）
    multi_tf = result.get("多周期共振") or {}
    tf_state = multi_tf.get("共振状态", "")
    daily_c = multi_tf.get("日线综合评分")
    hourly_c = multi_tf.get("1h综合评分")
    tf_marker = ""
    if tf_state == "周期分歧" and daily_c is not None and hourly_c is not None:
        tf_marker = f" | ⚠️ 周期分歧（日{daily_c:.0f} vs 1h{hourly_c:.0f}）"
    elif tf_state == "同向共振":
        tf_marker = " | ✓ 双周期共振"

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
        detail=(
            f"Score {composite:.0f}, strength {strength_en}, confidence {confidence}%{tf_marker}"
            if lang == "en" else
            f"综合评分{composite:.0f}, 强度{strength}, 置信度{confidence}%{tf_marker}"
        ),
    )


def _technical_source(ohlcv: dict, weight: float, lang: str = "zh") -> Optional[SignalSource]:
    """
    技术分析信号源（专业升级版）
    """
    closes = ohlcv.get("closes", [])
    highs = ohlcv.get("highs", [])
    lows = ohlcv.get("lows", [])
    volumes = ohlcv.get("volumes", [])

    if len(closes) < 25:
        return None

    from app.skills.analysis_skills.indicators import (
        ema, ema_triple, adx, supertrend,
        rsi, macd, detect_divergence,
        obv, bollinger_bands, atr,
    )

    price = closes[-1]
    score = 0
    details = []
    en = lang == "en"

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    # ── 1. EMA 排列 (权重 25%) ─────────────────────────────
    if len(closes) >= 55:
        fast_p, mid_p, slow_p = 9, 21, 55
    else:
        fast_p, mid_p, slow_p = 5, 13, 21

    e_fast, e_mid, e_slow = ema_triple(closes, fast_p, mid_p, slow_p)
    e9_v, e21_v, e55_v = last_valid(e_fast), last_valid(e_mid), last_valid(e_slow)

    if e9_v and e21_v and e55_v:
        bull_stack = e9_v > e21_v > e55_v
        bear_stack = e9_v < e21_v < e55_v

        if bull_stack:
            score += 25
            details.append(f"EMA bull stack({fast_p}>{mid_p}>{slow_p})" if en else f"EMA多头排列({fast_p}>{mid_p}>{slow_p})")
        elif bear_stack:
            score -= 25
            details.append(f"EMA bear stack({fast_p}<{mid_p}<{slow_p})" if en else f"EMA空头排列({fast_p}<{mid_p}<{slow_p})")
        elif e9_v > e21_v:
            score += 10
            details.append("Short-term EMA bullish" if en else "短期EMA偏多")
        elif e9_v < e21_v:
            score -= 10
            details.append("Short-term EMA bearish" if en else "短期EMA偏空")

    # ── 2. ADX 趋势强度 (权重 15%) ─────────────────────────
    if len(closes) >= 28:
        adx_vals, pdi, mdi = adx(highs, lows, closes, 14)
        adx_v = last_valid(adx_vals)
        pdi_v = last_valid(pdi)
        mdi_v = last_valid(mdi)

        if adx_v is not None:
            if adx_v > 25:  # 趋势有效
                if pdi_v and mdi_v and pdi_v > mdi_v:
                    score += 15
                    details.append(f"ADX={adx_v:.0f} trend+DI bull" if en else f"ADX={adx_v:.0f}趋势强+DI多头")
                elif pdi_v and mdi_v and mdi_v > pdi_v:
                    score -= 15
                    details.append(f"ADX={adx_v:.0f} trend+DI bear" if en else f"ADX={adx_v:.0f}趋势强+DI空头")
            elif adx_v < 20:
                details.append(f"ADX={adx_v:.0f} sideways" if en else f"ADX={adx_v:.0f}震荡市")

    # ── 3. RSI + 背离 (权重 20%) ─────────────────────────────
    rsi_vals = rsi(closes, 14)
    rsi_v = last_valid(rsi_vals)

    if rsi_v is not None:
        if rsi_v >= 75:
            score -= 20
            details.append(f"RSI={rsi_v:.0f} extreme OB" if en else f"RSI={rsi_v:.0f}严重超买")
        elif rsi_v >= 65:
            score -= 10
            details.append(f"RSI={rsi_v:.0f} OB zone" if en else f"RSI={rsi_v:.0f}超买区")
        elif rsi_v >= 55:
            score += 10
            details.append(f"RSI={rsi_v:.0f} bullish" if en else f"RSI={rsi_v:.0f}偏强")
        elif rsi_v <= 25:
            score += 20
            details.append(f"RSI={rsi_v:.0f} extreme OS" if en else f"RSI={rsi_v:.0f}严重超卖")
        elif rsi_v <= 35:
            score += 10
            details.append(f"RSI={rsi_v:.0f} OS zone" if en else f"RSI={rsi_v:.0f}超卖区")
        elif rsi_v <= 45:
            score -= 10
            details.append(f"RSI={rsi_v:.0f} bearish" if en else f"RSI={rsi_v:.0f}偏弱")

        # 背离检测
        div = detect_divergence(closes, rsi_vals, 20)
        if div == "bullish_divergence":
            score += 15
            details.append("RSI bull div" if en else "RSI底背离")
        elif div == "bearish_divergence":
            score -= 15
            details.append("RSI bear div" if en else "RSI顶背离")

    # ── 4. MACD (权重 15%) ──────────────────────────────────
    if len(closes) >= 26:
        macd_line, sig_line, hist = macd(closes, 12, 26, 9)
        macd_v = last_valid(macd_line)
        hist_v = last_valid(hist)

        if hist_v is not None and macd_v is not None:
            if hist_v > 0 and macd_v > 0:
                score += 15
                details.append("MACD golden+above zero" if en else "MACD金叉+零轴上方")
            elif hist_v > 0:
                score += 8
                details.append("MACD histogram +" if en else "MACD柱正值")
            elif hist_v < 0 and macd_v < 0:
                score -= 15
                details.append("MACD death+below zero" if en else "MACD死叉+零轴下方")
            elif hist_v < 0:
                score -= 8
                details.append("MACD histogram -" if en else "MACD柱负值")

            # MACD 背离
            macd_div = detect_divergence(closes, macd_line, 20)
            if macd_div == "bullish_divergence":
                score += 10
                details.append("MACD bull div" if en else "MACD底背离")
            elif macd_div == "bearish_divergence":
                score -= 10
                details.append("MACD bear div" if en else "MACD顶背离")

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
                details.append(f"BB upper({bb_pos:.0f}%)" if en else f"BB上轨({bb_pos:.0f}%)")
            elif bb_pos <= 10:
                score += 10
                details.append(f"BB lower({bb_pos:.0f}%)" if en else f"BB下轨({bb_pos:.0f}%)")
            elif 40 <= bb_pos <= 60:
                details.append(f"BB mid({bb_pos:.0f}%)" if en else f"BB中部({bb_pos:.0f}%)")

    # ── 6. Supertrend (权重 10%) ─────────────────────────────
    st_line, st_dir = supertrend(highs, lows, closes, 10, 3.0)
    if st_dir[-1] == 1:
        score += 10
        details.append("Supertrend bull" if en else "Supertrend多头")
    elif st_dir[-1] == -1:
        score -= 10
        details.append("Supertrend bear" if en else "Supertrend空头")

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
                details.append("Vol-price bullish" if en else "量价齐升")
            elif not obv_up and price_up:
                score -= 5
                details.append("Vol-price divergence" if en else "量价背离")

    # 限幅
    score = max(-100, min(100, score))

    direction = (
        SignalDirection.LONG if score > 0 else
        SignalDirection.SHORT if score < 0 else
        SignalDirection.NEUTRAL
    )

    detail_str = ", ".join(details) if details else ("No clear signal" if en else "技术指标信号不明确")

    return SignalSource(
        name="technical",
        score=abs(score),
        direction=direction,
        weight=weight,
        detail=detail_str,
    )


def fuse_signals(coin: str, ohlcv: dict, raw_data: dict, relaxed: bool = False, lang: str = "zh") -> Optional[SignalCard]:
    """
    多维信号融合，生成交易信号卡

    Args:
        relaxed: True=降低门槛，始终返回卡（C级兜底），用于 Chat 场景
    """
    raw_data = raw_data or {}
    realtime_price = _extract_realtime_price(raw_data)
    ohlcv = _apply_realtime_price(ohlcv, realtime_price)
    entry_ohlcv = _apply_realtime_price(raw_data.get("entry_ohlcv") or {}, realtime_price)

    # ── 获取自适应引擎 ──────────────────────────────────────────
    engine = get_strategy_engine()

    # ── 先做数学推导（用于获取市场状态）──────────────────────────
    closes = ohlcv.get("closes", [])
    math_result = None
    regime = "quiet"

    if len(closes) >= 40:
        math_result = run_math_derivation(closes, direction="long", lang=lang)
        if math_result and math_result.regime:
            regime = math_result.regime.regime

    # ── 获取自适应权重 ──────────────────────────────────────────
    adaptive_weights = engine.get_adaptive_weights(regime)
    regime_params = engine.get_regime_params(regime)

    # ── 收集信号源 ──────────────────────────────────────────────
    sources: List[SignalSource] = []

    # 大单信号：时间衰减版（自适应 T½ + 吸筹 override）；失败兜底走 12h 聚合
    bigorder_src = None
    try:
        from app.signals.alpha_scanner import get_bigorder_decay_signal, detect_accumulation_pattern
        # 用真正的 vol_regime（不是 market regime），否则映射不准
        vol_regime_for_decay = (
            math_result.vol_cone.regime if math_result and math_result.vol_cone and math_result.vol_cone.regime
            else regime  # 兜底用 market regime（不精确但不会崩）
        )
        decay_src = get_bigorder_decay_signal(
            coin, adaptive_weights.get("bigorder_anomaly", 0.35),
            vol_regime=vol_regime_for_decay,
            dual_window=(vol_regime_for_decay == "extreme"),
        )
        if decay_src:
            # 吸筹 pattern 命中时强制 LONG（覆盖简单 net_flow 判断）
            try:
                accumulation = detect_accumulation_pattern(coin)
                if accumulation:
                    decay_src.direction = SignalDirection.LONG
                    decay_src.detail += (
                        f" | 吸筹 pattern 触发 LONG override"
                        f" (top5买{accumulation['top5_buy_ratio'] * 100:.0f}%/"
                        f"散户卖{accumulation['small_sell_ratio'] * 100:.0f}%)"
                    )
            except Exception:
                pass
            bigorder_src = decay_src
    except Exception:
        pass  # v2 失败兜底走 legacy 聚合

    if not bigorder_src:
        # 兜底：优先用 Alpha 扫描传入的12h聚合，否则读实时快照
        bigorder_src = raw_data.get("bigorder_12h") or _bigorder_source(coin, adaptive_weights.get("bigorder_anomaly", 0.35), lang)
    if bigorder_src:
        sources.append(bigorder_src)

    quant_src = _quantitative_source(
        coin, adaptive_weights.get("quantitative", 0.35), lang,
        entry_ohlcv=raw_data.get("entry_ohlcv"),
    )
    if quant_src:
        sources.append(quant_src)

    tech_src = _technical_source(ohlcv, adaptive_weights.get("technical", 0.30), lang)
    if tech_src:
        sources.append(tech_src)

    if len(sources) < 2:
        if not relaxed:
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
    elif relaxed:
        # relaxed 模式：方向不明确时取最大权重方向
        direction = SignalDirection.LONG if weighted_direction >= 0 else SignalDirection.SHORT
        confidence = min(confidence, 30)
    else:
        return None

    # 方向一致性
    consistent_count = sum(1 for s in sources if s.direction == direction)
    if consistent_count < 2:
        if not relaxed:
            return None

    # ── 信号等级 ──────────────────────────────────────────────
    math_confirms = math_result and math_result.math_score_adjustment > 15
    if len(sources) >= 3 and consistent_count >= 3 and confidence >= 65 and math_confirms:
        grade = SignalGrade.S
    elif len(sources) >= 3 and consistent_count >= 3 and confidence >= 65:
        grade = SignalGrade.A
    elif consistent_count >= 2 and confidence >= 50:
        grade = SignalGrade.A
    elif consistent_count >= 2 and confidence >= 35:
        grade = SignalGrade.B
    else:
        grade = SignalGrade.C if relaxed else SignalGrade.B

    # ── 价格计算 ──────────────────────────────────────────────
    ohlcv_close = ohlcv.get("closes", [-1])[-1] if ohlcv.get("closes") else 0
    price = realtime_price or ohlcv_close
    if price <= 0:
        return None

    # 价格合理性校验：实时价偏离 K 线收盘价超过 50% → 回退到收盘价（防止数据源异常）
    if realtime_price and ohlcv_close > 0:
        deviation = abs(realtime_price - ohlcv_close) / ohlcv_close
        if deviation > 0.5:
            price = ohlcv_close

    # ATR：小时线可用时优先用于短线进出场，否则回退到主周期（日线）
    from app.skills.analysis_skills.indicators import atr as calc_atr
    atr_ohlcv = entry_ohlcv if entry_ohlcv and len(entry_ohlcv.get("closes", [])) >= 15 else ohlcv
    atr_closes = atr_ohlcv.get("closes", closes)
    atr_vals = calc_atr(atr_ohlcv.get("highs", atr_closes), atr_ohlcv.get("lows", atr_closes), atr_closes, 14)

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    atr_v = last_valid(atr_vals) or (price * 0.02)
    # 极小价币种 ATR 可能因浮点精度归零，设最小值为价格的 1.5%
    atr_v = max(atr_v, price * 0.015)

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
        if not relaxed:
            return None
        # relaxed: 调整 TP 使盈亏比至少 1.5
        tp_mult_actual = 1.5 * sl_mult
        if direction == SignalDirection.LONG:
            take_profit = price + tp_mult_actual * atr_v
        else:
            take_profit = price - tp_mult_actual * atr_v
        reward = abs(take_profit - price)
        risk_reward = round(reward / risk, 1) if risk > 0 else 1.5
        grade = SignalGrade.C

    # ── Kelly 仓位 ────────────────────────────────────────────
    base_position = {"S": 8.0, "A": 5.0, "B": 3.0, "C": 2.0}.get(grade, 3.0)
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

    # ── 自适应价格精度 ────────────────────────────────────────
    def _prec(p):
        if p >= 1000: return 1
        elif p >= 1: return 4
        elif p >= 0.001: return 6
        elif p >= 0.00001: return 10
        else: return 12
    prec = _prec(price)

    # ── 生成信号卡 ────────────────────────────────────────────
    engine.increment_generated()

    return SignalCard(
        coin=coin.upper(),
        direction=direction,
        grade=grade,
        current_price=price,
        entry_low=round(entry_low, prec),
        entry_high=round(entry_high, prec),
        stop_loss=round(stop_loss, prec),
        take_profit=round(take_profit, prec),
        risk_reward_ratio=risk_reward,
        confidence=round(confidence, 1),
        sources=sources,
        position_pct=round(position_pct, 1),
        invalidation_price=round(invalidation, prec),
        math=math_summary,
        strategy=strategy_meta,
        status=SignalStatus.PENDING,
    )


def generate_card_for_chat(coin: str, tier: str = "pro", always: bool = False, lang: str = "zh") -> Optional[dict]:
    """
    供 Agent 对话流调用的信号卡生成器

    Args:
        always: True=始终返回卡（C级兜底），False=可能返回 None

    返回 signal_card 事件数据（前端直接渲染为卡片）或 None
    """
    from app.services.data_service import get_header_data, get_multi_timeframe_klines, get_trade_volume
    from app.skills.analysis_skills.quantitative import _parse_kline
    from app.signals.backtest import backtest_signal

    try:
        header = get_header_data(coin)
        timeframes = get_multi_timeframe_klines(coin, (1, 2, 3, 4))
        volume_data = get_trade_volume(coin)

        daily_data = timeframes.get("daily_60d") or timeframes.get("daily_30d") or {}
        hourly_data = timeframes.get("hourly_72h") or {}

        ohlcv = _parse_kline(daily_data, volume_data)
        if not ohlcv:
            logger.warning(f"generate_card_for_chat({coin}): K 线数据不足（daily keys={list(timeframes.keys())}）")
            return None

        entry_ohlcv = _parse_kline(hourly_data, min_bars=15)
        raw_data = {
            "header": header,
            "current_price": _extract_realtime_price({"header": header}),
            "timeframes": timeframes,
            "entry_ohlcv": entry_ohlcv,
        }

        signal_card = fuse_signals(coin, ohlcv, raw_data, relaxed=always, lang=lang)
        if not signal_card:
            return None

        # C 级卡不做回测（不参与结算统计）
        bt = None
        if signal_card.grade != SignalGrade.C:
            bt = backtest_signal(coin, signal_card.direction, signal_card.grade)
        if bt:
            signal_card.win_rate = bt["win_rate"]
            signal_card.sample_count = bt["sample_count"]
            signal_card.avg_profit_pct = bt["avg_profit_pct"]

        # chat 路径也要持久化，否则用户主动问的卡不进结算系统
        try:
            from app.signals.settlement import save_signal_card
            save_signal_card(signal_card)
        except Exception:
            pass

        event_data = _build_card_event(signal_card, bt, tier)
        event_data["price_source"] = "header.currentPrice"
        event_data["kline_periods"] = {
            "entry": "hourly_72h" if entry_ohlcv else "daily_30d",
            "signal": "daily_30d",
            "weekly": "weekly_1y",
            "monthly": "monthly_all",
        }
        event_data["display"] = signal_card.format_card(lang)
        return event_data

    except Exception as e:
        logger.error(f"generate_card_for_chat({coin}) 失败: {type(e).__name__}: {e}")
        return None


def _build_card_event(signal_card: SignalCard, bt_result: dict, tier: str) -> dict:
    """构建前端渲染用的 signal_card 事件数据（价格字段已格式化为字符串）"""
    from app.utils.formatters import format_price_change
    card = {
        "coin": signal_card.coin,
        "direction": signal_card.direction.value,
        "grade": signal_card.grade.value,
        "confidence": signal_card.confidence,
        "current_price": format_price_change(signal_card.current_price),
        "entry_zone": [format_price_change(signal_card.entry_low), format_price_change(signal_card.entry_high)],
        "stop_loss": format_price_change(signal_card.stop_loss),
        "take_profit": format_price_change(signal_card.take_profit),
        "risk_reward": signal_card.risk_reward_ratio,
        "position_pct": signal_card.position_pct,
        "invalidation": format_price_change(signal_card.invalidation_price) if signal_card.invalidation_price else None,
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
