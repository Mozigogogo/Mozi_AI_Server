"""
量化决策分析 Skill — 专业六因子重构版
因子体系：趋势强度 / 动量质量 / 量价结构 / 资金结构 / 波动风险 / 市场结构
输出：可执行交易指令（入场区间、止损、止盈、仓位建议）
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .indicators import (
    ema_triple, adx, supertrend,
    rsi, macd, detect_divergence,
    obv, vwap,
    atr, bollinger_bands,
    swing_points, key_levels,
)

# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FactorResult:
    name: str
    score: float          # 归一化到 -100 ~ +100
    weight: float         # 在总评分中的权重 0~1
    signal: str           # 'bullish' | 'bearish' | 'neutral'
    strength: str         # 'strong' | 'moderate' | 'weak'
    detail: str           # 人类可读说明
    raw: dict = field(default_factory=dict)  # 原始指标值，供 LLM 进一步分析


@dataclass
class TradeSignal:
    direction: str             # 'long' | 'short' | 'neutral'
    strength: str              # 'strong' | 'moderate' | 'weak' | 'none'
    composite_score: float     # -100 ~ +100
    confidence_pct: int        # 建议胜率估计，0~100

    # 可执行价位
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float       # TP1：减仓 50%
    take_profit_2: float       # TP2：移动止损
    risk_reward_ratio: float

    # 仓位管理
    suggested_position_pct: float   # 建议仓位比例 0~1
    atr_value: float
    atr_pct: float                   # ATR / 当前价格

    # 关键价位
    key_resistances: list[float]
    key_supports: list[float]
    vwap_price: float
    bb_upper: float
    bb_lower: float
    supertrend_line: float

    # 风险提示
    invalidation_price: float   # 信号失效价格（即止损位）
    key_risk: str               # 最主要的风险描述
    factors: list[FactorResult] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# K 线解析
# ─────────────────────────────────────────────────────────────────────────────

def _parse_kline(kline_data: dict) -> Optional[dict]:
    """
    从 get_kline_data 返回值中解析 OHLCV 列表
    预期格式: {"values": [[timestamp, open, high, low, close, volume], ...]}
    返回 {"opens", "highs", "lows", "closes", "volumes"} 或 None
    """
    if not kline_data or not isinstance(kline_data, dict):
        return None
    values = kline_data.get("values", [])
    if not values or len(values) < 60:   # 至少需要 60 根K线
        return None

    opens, highs, lows, closes, volumes = [], [], [], [], []
    for bar in values:
        if not isinstance(bar, (list, tuple)) or len(bar) < 6:
            continue
        try:
            opens.append(float(bar[1]))
            highs.append(float(bar[2]))
            lows.append(float(bar[3]))
            closes.append(float(bar[4]))
            volumes.append(float(bar[5]))
        except (ValueError, TypeError):
            continue

    if len(closes) < 60:
        return None
    return dict(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)


# ─────────────────────────────────────────────────────────────────────────────
# 六因子评分
# ─────────────────────────────────────────────────────────────────────────────

def _score_trend(ohlcv: dict) -> FactorResult:
    """
    因子 1：趋势强度  权重 25%
    指标：EMA(9/21/55) 排列 + ADX(14) 有效性过滤 + Supertrend 方向
    分值：±100
    """
    closes = ohlcv["closes"]
    highs  = ohlcv["highs"]
    lows   = ohlcv["lows"]

    e9, e21, e55 = ema_triple(closes, 9, 21, 55)
    adx_vals, pdi, mdi = adx(highs, lows, closes, 14)
    st_line, st_dir   = supertrend(highs, lows, closes, 10, 3.0)

    # 取最新有效值
    def last(lst):
        for v in reversed(lst):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    e9_v   = last(e9);   e21_v = last(e21); e55_v = last(e55)
    adx_v  = last(adx_vals); pdi_v = last(pdi);   mdi_v = last(mdi)
    st_v   = last(st_line);  st_d  = st_dir[-1]
    price  = closes[-1]

    if any(v is None for v in [e9_v, e21_v, e55_v, adx_v]):
        return FactorResult("trend", 0, 0.25, "neutral", "weak", "趋势因子数据不足")

    trend_valid = adx_v > 25
    bull_stack  = e9_v > e21_v > e55_v
    bear_stack  = e9_v < e21_v < e55_v
    st_bull     = st_d == 1

    # 评分逻辑
    if   bull_stack and trend_valid and st_bull:  score = 100; sig = "bullish"; strength = "strong";   detail = f"EMA多头排列+ADX={adx_v:.1f}趋势强劲+Supertrend支撑"
    elif bull_stack and trend_valid:              score =  80; sig = "bullish"; strength = "strong";   detail = f"EMA多头排列+ADX={adx_v:.1f}，Supertrend空头需关注"
    elif bull_stack and st_bull:                  score =  55; sig = "bullish"; strength = "moderate"; detail = f"EMA多头排列但ADX={adx_v:.1f}<25，趋势偏弱"
    elif bull_stack:                              score =  30; sig = "bullish"; strength = "weak";     detail = f"EMA多头排列但趋势强度不足(ADX={adx_v:.1f})，建议等待确认"
    elif bear_stack and trend_valid and not st_bull: score = -100; sig = "bearish"; strength = "strong";  detail = f"EMA空头排列+ADX={adx_v:.1f}趋势强劲+Supertrend压制"
    elif bear_stack and trend_valid:              score = -80; sig = "bearish"; strength = "strong";   detail = f"EMA空头排列+ADX={adx_v:.1f}，注意短期反弹"
    elif bear_stack:                              score = -35; sig = "bearish"; strength = "weak";     detail = f"EMA空头排列但ADX={adx_v:.1f}<25，震荡可能性高"
    else:                                         score =   0; sig = "neutral"; strength = "weak";     detail = f"EMA无明确排列，ADX={adx_v:.1f}，市场震荡"

    raw = {"ema9": round(e9_v, 4), "ema21": round(e21_v, 4), "ema55": round(e55_v, 4),
           "adx": round(adx_v, 2), "plus_di": round(pdi_v, 2) if pdi_v else None,
           "minus_di": round(mdi_v, 2) if mdi_v else None,
           "supertrend": round(st_v, 4) if st_v else None, "st_direction": st_d}
    return FactorResult("trend", score, 0.25, sig, strength, detail, raw)


def _score_momentum(ohlcv: dict) -> FactorResult:
    """
    因子 2：动量质量  权重 20%
    指标：RSI(14) 超买超卖 + MACD 柱体方向 + 背离检测
    """
    closes = ohlcv["closes"]

    rsi_vals             = rsi(closes, 14)
    macd_line, sig_line, hist = macd(closes, 12, 26, 9)
    rsi_div              = detect_divergence(closes, rsi_vals, 20)
    macd_div             = detect_divergence(closes, macd_line, 20)

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not math.isnan(v): return v
        return None

    rsi_v  = last_valid(rsi_vals)
    hist_v = last_valid(hist)
    macd_v = last_valid(macd_line)
    sig_v  = last_valid(sig_line)

    if rsi_v is None:
        return FactorResult("momentum", 0, 0.20, "neutral", "weak", "动量因子数据不足")

    # RSI 基础分
    if   rsi_v >= 75: rsi_score = -80;  rsi_note = f"RSI={rsi_v:.1f} 严重超买，回调风险高"
    elif rsi_v >= 65: rsi_score = -30;  rsi_note = f"RSI={rsi_v:.1f} 超买区，上涨动能减弱"
    elif rsi_v >= 55: rsi_score =  60;  rsi_note = f"RSI={rsi_v:.1f} 强势区，多头占优"
    elif rsi_v >= 45: rsi_score =   0;  rsi_note = f"RSI={rsi_v:.1f} 中性区，方向不明"
    elif rsi_v >= 35: rsi_score = -50;  rsi_note = f"RSI={rsi_v:.1f} 弱势区，空头占优"
    elif rsi_v >= 25: rsi_score =  40;  rsi_note = f"RSI={rsi_v:.1f} 超卖区，反弹可能性增加"
    else:             rsi_score =  80;  rsi_note = f"RSI={rsi_v:.1f} 严重超卖，底部信号"

    # MACD 修正
    macd_adj = 0
    macd_note = ""
    if hist_v is not None:
        if   hist_v > 0 and macd_v and macd_v > 0:  macd_adj =  20; macd_note = "MACD柱正值且在零轴上方"
        elif hist_v > 0:                              macd_adj =  10; macd_note = "MACD柱正值，动能向上"
        elif hist_v < 0 and macd_v and macd_v < 0:  macd_adj = -20; macd_note = "MACD柱负值且在零轴下方"
        else:                                         macd_adj = -10; macd_note = "MACD柱负值，动能向下"

    # 背离修正（高优先级）
    div_adj = 0
    div_note = ""
    if   rsi_div  == "bearish_divergence" or macd_div == "bearish_divergence":
        div_adj = -40; div_note = "⚠️ 顶背离：价格新高但动量指标未创新高，警惕回调"
    elif rsi_div  == "bullish_divergence" or macd_div == "bullish_divergence":
        div_adj =  40; div_note = "底背离：价格新低但动量指标未创新低，反弹信号"

    score   = max(-100, min(100, rsi_score + macd_adj + div_adj))
    detail  = "；".join(x for x in [rsi_note, macd_note, div_note] if x)
    signal  = "bullish" if score > 20 else ("bearish" if score < -20 else "neutral")
    strength = "strong" if abs(score) >= 60 else ("moderate" if abs(score) >= 30 else "weak")

    raw = {"rsi": round(rsi_v, 2), "macd_line": round(macd_v, 6) if macd_v else None,
           "macd_hist": round(hist_v, 6) if hist_v else None,
           "rsi_divergence": rsi_div, "macd_divergence": macd_div}
    return FactorResult("momentum", score, 0.20, signal, strength, detail, raw)


def _score_volume_price(ohlcv: dict) -> FactorResult:
    """
    因子 3：量价结构  权重 20%
    指标：OBV 趋势方向 + VWAP 偏离度 + 量价背离
    """
    closes  = ohlcv["closes"]
    highs   = ohlcv["highs"]
    lows    = ohlcv["lows"]
    volumes = ohlcv["volumes"]

    obv_vals  = obv(closes, volumes)
    vwap_vals = vwap(highs, lows, closes, volumes)

    price    = closes[-1]
    vwap_now = vwap_vals[-1]
    vwap_dev = (price - vwap_now) / vwap_now * 100  # % 偏离

    # OBV 趋势：用 EMA(10) 平滑后判断斜率
    from indicators import ema as _ema
    obv_ema = _ema(obv_vals, 10)
    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not math.isnan(v): return v
        return None

    obv_now  = last_valid(obv_ema)
    obv_prev = None
    count = 0
    for v in reversed(obv_ema):
        if v is not None and not math.isnan(v):
            count += 1
            if count == 6: obv_prev = v; break

    obv_rising = (obv_now > obv_prev) if (obv_now and obv_prev) else None

    # 量价一致性：价格涨 + OBV 涨 = 量价齐升（健康）
    price_up = closes[-1] > closes[-5] if len(closes) >= 6 else None

    # 评分
    score = 0
    notes = []

    # VWAP 偏离评分
    if   vwap_dev >  3:   score += -30; notes.append(f"价格高于VWAP {vwap_dev:.2f}%，偏离过大有回归压力")
    elif vwap_dev >  1:   score +=  20; notes.append(f"价格高于VWAP {vwap_dev:.2f}%，机构成本上方运行")
    elif vwap_dev > -1:   score +=  10; notes.append(f"价格贴近VWAP({vwap_now:.4f})，多空争夺")
    elif vwap_dev > -3:   score += -20; notes.append(f"价格低于VWAP {abs(vwap_dev):.2f}%，空头占优")
    else:                 score +=  20; notes.append(f"价格大幅低于VWAP {abs(vwap_dev):.2f}%，超跌反弹机会")

    # OBV 趋势评分
    if obv_rising is True and price_up is True:
        score += 40; notes.append("OBV趋势向上+价格上涨：量价齐升，趋势健康")
    elif obv_rising is True and price_up is False:
        score += 30; notes.append("OBV趋势向上但价格下跌：量价正背离，看涨信号")
    elif obv_rising is False and price_up is True:
        score -= 40; notes.append("⚠️ 量价负背离：价格上涨但OBV下降，上涨缺乏量能支撑")
    elif obv_rising is False:
        score -= 20; notes.append("OBV趋势向下，资金持续流出")

    score   = max(-100, min(100, score))
    signal  = "bullish" if score > 20 else ("bearish" if score < -20 else "neutral")
    strength = "strong" if abs(score) >= 50 else ("moderate" if abs(score) >= 25 else "weak")

    raw = {"vwap": round(vwap_now, 4), "vwap_deviation_pct": round(vwap_dev, 2),
           "obv_trend": "rising" if obv_rising else ("falling" if obv_rising is False else "unknown"),
           "price_vs_5bar": "up" if price_up else "down"}
    return FactorResult("volume_price", score, 0.20, signal, strength,
                        "；".join(notes) if notes else "量价因子数据不足", raw)


def _score_capital(raw_data: dict) -> FactorResult:
    """
    因子 4：资金结构  权重 20%
    子因子权重：多空比 40% + 持仓变化 35% + 资金费率 25%
    """
    notes = []
    weighted_score = 0.0

    # ── 4.1 多空比（权重 40%）──────────────────────────────────────
    ls_score = 0
    if "get_buy_sell_ratio" in raw_data and raw_data["get_buy_sell_ratio"]:
        ratios = []
        for exch, exch_data in raw_data["get_buy_sell_ratio"].items():
            if not isinstance(exch_data, dict): continue
            ls = exch_data.get("longShortData", [])
            if isinstance(ls, list) and ls:
                try: ratios.append(float(ls[-1]))
                except (ValueError, TypeError): pass

        if ratios:
            avg_ls = sum(ratios) / len(ratios)
            if   avg_ls >= 1.5:  ls_score = -80; notes.append(f"多空比={avg_ls:.2f}，多头极度拥挤→反向风险")
            elif avg_ls >= 1.2:  ls_score =  40; notes.append(f"多空比={avg_ls:.2f}，多头占优")
            elif avg_ls >= 0.9:  ls_score =   0; notes.append(f"多空比={avg_ls:.2f}，多空平衡")
            elif avg_ls >= 0.7:  ls_score = -40; notes.append(f"多空比={avg_ls:.2f}，空头占优")
            else:                ls_score =  80; notes.append(f"多空比={avg_ls:.2f}，空头极度拥挤→反向机会")
        else:
            notes.append("多空比数据不足")

    weighted_score += ls_score * 0.40

    # ── 4.2 持仓变化（权重 35%）──────────────────────────────────────
    oi_score = 0
    if "get_open_interest" in raw_data and raw_data["get_open_interest"]:
        oi_raw = raw_data["get_open_interest"]
        changes = []
        raw_inner = oi_raw.get("data", {})
        if isinstance(raw_inner, dict):
            for exch, vals in raw_inner.items():
                if isinstance(vals, list) and len(vals) >= 2:
                    try:
                        prev, curr = float(vals[-2]), float(vals[-1])
                        if prev > 0: changes.append((curr - prev) / prev * 100)
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

        if changes:
            avg_oi = sum(changes) / len(changes)
            if   avg_oi >=  10: oi_score =  80; notes.append(f"持仓暴增+{avg_oi:.1f}%，大量新资金入场")
            elif avg_oi >=   5: oi_score =  50; notes.append(f"持仓增加+{avg_oi:.1f}%，资金持续流入")
            elif avg_oi >=   2: oi_score =  20; notes.append(f"持仓小幅增加+{avg_oi:.1f}%")
            elif avg_oi >= -2:  oi_score =   0; notes.append(f"持仓变化{avg_oi:.1f}%，资金平稳")
            elif avg_oi >= -5:  oi_score = -30; notes.append(f"持仓减少{avg_oi:.1f}%，资金流出")
            else:               oi_score = -70; notes.append(f"持仓大幅减少{avg_oi:.1f}%，平仓离场")
        else:
            notes.append("持仓量数据不足")

    weighted_score += oi_score * 0.35

    # ── 4.3 资金费率（权重 25%）──────────────────────────────────────
    fr_score = 0
    avg_fr = None
    if "get_funding_rate" in raw_data and raw_data["get_funding_rate"]:
        fr_data = raw_data["get_funding_rate"]
        exchanges = fr_data.get("exchanges", {})
        valid_rates = []
        for exch, rate_str in exchanges.items():
            try: valid_rates.append(float(str(rate_str).replace("%", "")))
            except (ValueError, AttributeError): pass

        if valid_rates:
            avg_fr = sum(valid_rates) / len(valid_rates)
            if   avg_fr >=  0.10: fr_score = -80; notes.append(f"资金费率={avg_fr:.4f}% 极端正值，多头严重拥挤→反转风险")
            elif avg_fr >=  0.05: fr_score = -30; notes.append(f"资金费率={avg_fr:.4f}% 偏高，多头成本上升")
            elif avg_fr >=  0.01: fr_score =  30; notes.append(f"资金费率={avg_fr:.4f}%，多头温和占优")
            elif avg_fr >= -0.01: fr_score =   0; notes.append(f"资金费率={avg_fr:.4f}%，多空均衡")
            elif avg_fr >= -0.05: fr_score = -30; notes.append(f"资金费率={avg_fr:.4f}% 负值，空头占优")
            else:                 fr_score =  80; notes.append(f"资金费率={avg_fr:.4f}% 极端负值，空头严重拥挤→反转机会")
        else:
            notes.append("资金费率数据不足")

    weighted_score += fr_score * 0.25

    score    = max(-100, min(100, weighted_score))
    signal   = "bullish" if score > 20 else ("bearish" if score < -20 else "neutral")
    strength = "strong" if abs(score) >= 50 else ("moderate" if abs(score) >= 25 else "weak")

    raw_out = {"ls_score": ls_score, "oi_score": oi_score, "fr_score": fr_score,
               "avg_funding_rate": round(avg_fr, 6) if avg_fr is not None else None,
               "weighted_score": round(score, 2)}
    return FactorResult("capital", score, 0.20, signal, strength,
                        "；".join(notes) if notes else "资金因子数据不足", raw_out)


def _score_volatility_risk(ohlcv: dict, current_price: float) -> FactorResult:
    """
    因子 5：波动风险  权重 10%
    输出：ATR、止损距离、建议仓位；分值反映当前波动环境适合程度
    """
    closes = ohlcv["closes"]
    highs  = ohlcv["highs"]
    lows   = ohlcv["lows"]

    atr_vals    = atr(highs, lows, closes, 14)
    bb_up, bb_mid, bb_low = bollinger_bands(closes, 20, 2.0)

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not math.isnan(v): return v
        return None

    atr_v  = last_valid(atr_vals)
    bb_u   = last_valid(bb_up)
    bb_l   = last_valid(bb_low)
    bb_m   = last_valid(bb_mid)

    if atr_v is None or atr_v == 0:
        return FactorResult("volatility_risk", 0, 0.10, "neutral", "weak", "波动率数据不足")

    atr_pct = atr_v / current_price * 100
    bb_width = (bb_u - bb_l) / bb_m * 100 if (bb_u and bb_l and bb_m and bb_m != 0) else None

    # 波动率适宜性评分（低波动 = 趋势更可靠）
    notes = []
    if   atr_pct >= 8:   score = -60; notes.append(f"ATR={atr_pct:.2f}% 极度波动，建议降低仓位")
    elif atr_pct >= 5:   score = -30; notes.append(f"ATR={atr_pct:.2f}% 高波动，止损需要更宽")
    elif atr_pct >= 2:   score =  20; notes.append(f"ATR={atr_pct:.2f}% 波动适中，趋势交易友好")
    else:                score =  50; notes.append(f"ATR={atr_pct:.2f}% 低波动，趋势稳定")

    # 布林带位置
    if bb_u and bb_l:
        bb_pos = (current_price - bb_l) / (bb_u - bb_l) * 100
        if   bb_pos >= 95: notes.append(f"价格触及布林上轨({bb_pos:.0f}%)，超买压力")
        elif bb_pos >= 80: notes.append(f"价格接近布林上轨({bb_pos:.0f}%)")
        elif bb_pos <= 5:  notes.append(f"价格触及布林下轨({bb_pos:.0f}%)，超卖支撑")
        elif bb_pos <= 20: notes.append(f"价格接近布林下轨({bb_pos:.0f}%)")
        else:              notes.append(f"价格在布林带中部({bb_pos:.0f}%)")

    signal   = "bullish" if score > 0 else ("bearish" if score < -30 else "neutral")
    strength = "strong" if abs(score) >= 50 else ("moderate" if abs(score) >= 25 else "weak")

    raw = {"atr": round(atr_v, 6), "atr_pct": round(atr_pct, 3),
           "bb_upper": round(bb_u, 4) if bb_u else None,
           "bb_lower": round(bb_l, 4) if bb_l else None,
           "bb_width_pct": round(bb_width, 2) if bb_width else None}
    return FactorResult("volatility_risk", score, 0.10, signal, strength,
                        "；".join(notes), raw)


def _score_market_structure(ohlcv: dict, current_price: float) -> FactorResult:
    """
    因子 6：市场结构  权重 5%
    指标：近期摆动高低点（支撑/阻力位）+ 价格与关键价位的相对关系
    """
    closes = ohlcv["closes"]
    highs  = ohlcv["highs"]
    lows   = ohlcv["lows"]

    sh, sl = swing_points(highs, lows, left=3, right=3)
    resistances, supports = key_levels(sh, sl, current_price, n_levels=3)

    notes = []
    score = 0

    if resistances:
        nearest_res = resistances[0]
        dist_pct    = (nearest_res - current_price) / current_price * 100
        if   dist_pct < 0.5: score -= 40; notes.append(f"紧贴阻力位{nearest_res:.4f}（距离{dist_pct:.2f}%），上方空间极小")
        elif dist_pct < 2.0: score -= 15; notes.append(f"接近阻力位{nearest_res:.4f}（距离{dist_pct:.2f}%）")
        else:                 score +=  0; notes.append(f"最近阻力位{nearest_res:.4f}（距离{dist_pct:.2f}%），空间充裕")
    else:
        notes.append("上方无明显阻力位")

    if supports:
        nearest_sup = supports[0]
        dist_pct    = (current_price - nearest_sup) / current_price * 100
        if   dist_pct < 0.5: score += 30; notes.append(f"紧靠支撑位{nearest_sup:.4f}（距离{dist_pct:.2f}%），下方风险低")
        elif dist_pct < 2.0: score += 10; notes.append(f"接近支撑位{nearest_sup:.4f}（距离{dist_pct:.2f}%）")
        else:                 score -=  0; notes.append(f"最近支撑位{nearest_sup:.4f}（距离{dist_pct:.2f}%）")
    else:
        notes.append("下方无明显支撑位，需关注")

    score    = max(-100, min(100, score))
    signal   = "bullish" if score > 15 else ("bearish" if score < -15 else "neutral")
    strength = "strong" if abs(score) >= 40 else ("moderate" if abs(score) >= 20 else "weak")

    raw = {"resistances": [round(r, 4) for r in resistances],
           "supports":    [round(s, 4) for s in supports]}
    return FactorResult("market_structure", score, 0.05, signal, strength,
                        "；".join(notes) if notes else "市场结构数据不足", raw)


# ─────────────────────────────────────────────────────────────────────────────
# 合成信号 → 可执行交易指令
# ─────────────────────────────────────────────────────────────────────────────

def _build_trade_signal(
    factors: list[FactorResult],
    ohlcv: dict,
    raw_data: dict,
    symbol: str,
) -> TradeSignal:
    price   = ohlcv["closes"][-1]
    highs   = ohlcv["highs"]
    lows    = ohlcv["lows"]
    closes  = ohlcv["closes"]
    volumes = ohlcv["volumes"]

    # 加权综合评分
    composite = sum(f.score * f.weight for f in factors)

    # 方向与强度
    bullish_count = sum(1 for f in factors if f.signal == "bullish")
    bearish_count = sum(1 for f in factors if f.signal == "bearish")

    if   composite >= 50  and bullish_count >= 4: direction = "long";  strength = "strong"
    elif composite >= 25  and bullish_count >= 3: direction = "long";  strength = "moderate"
    elif composite >= 10:                          direction = "long";  strength = "weak"
    elif composite <= -50 and bearish_count >= 4: direction = "short"; strength = "strong"
    elif composite <= -25 and bearish_count >= 3: direction = "short"; strength = "moderate"
    elif composite <= -10:                         direction = "short"; strength = "weak"
    else:                                          direction = "neutral"; strength = "none"

    # 仓位建议
    pos_map = {"strong": 0.8, "moderate": 0.5, "weak": 0.25, "none": 0.0}
    suggested_pos = pos_map[strength] if direction != "neutral" else 0.0

    # ATR 止损止盈计算
    from indicators import atr as _atr, vwap as _vwap, bollinger_bands as _bb, supertrend as _st
    atr_vals  = _atr(highs, lows, closes, 14)
    vwap_vals = _vwap(highs, lows, closes, volumes)
    bb_u, _, bb_l = _bb(closes, 20, 2.0)
    st_line, _ = _st(highs, lows, closes, 10, 3.0)

    def last_valid(lst):
        for v in reversed(lst):
            if v is not None and not (isinstance(v, float) and math.isnan(v)): return v
        return None

    atr_v     = last_valid(atr_vals)  or (price * 0.02)
    vwap_now  = last_valid(vwap_vals) or price
    bb_upper  = last_valid(bb_u) or price
    bb_lower  = last_valid(bb_l) or price
    st_now    = last_valid(st_line) or price
    atr_pct   = atr_v / price * 100

    # 入场区间：VWAP ± 0.5ATR（回踩入场优先）
    if direction == "long":
        entry_low    = max(vwap_now - 0.3 * atr_v, price * 0.99)
        entry_high   = vwap_now + 0.3 * atr_v
        stop_loss    = price - 1.5 * atr_v
        take_profit_1 = price + 2.0 * atr_v
        take_profit_2 = price + 3.5 * atr_v
        invalidation  = stop_loss
    elif direction == "short":
        entry_low    = vwap_now - 0.3 * atr_v
        entry_high   = min(vwap_now + 0.3 * atr_v, price * 1.01)
        stop_loss    = price + 1.5 * atr_v
        take_profit_1 = price - 2.0 * atr_v
        take_profit_2 = price - 3.5 * atr_v
        invalidation  = stop_loss
    else:
        entry_low = entry_high = stop_loss = take_profit_1 = take_profit_2 = price
        invalidation = price

    risk  = abs(price - stop_loss)
    rrr   = round(abs(take_profit_1 - price) / risk, 2) if risk > 0 else 0.0

    # 置信度映射
    def _confidence(score: float, strength: str) -> int:
        base_map = {"strong": 65, "moderate": 55, "weak": 50, "none": 48}
        base = base_map.get(strength, 48)
        adj  = int((abs(score) - 25) * 0.3) if abs(score) > 25 else 0
        return min(78, base + adj) if direction != "neutral" else 48

    confidence = _confidence(composite, strength)

    # 支撑阻力位
    sh, sl = swing_points(highs, lows, 3, 3)
    resistances, supports = key_levels(sh, sl, price, 3)

    # 主要风险描述
    vol_factor = next((f for f in factors if f.name == "volatility_risk"), None)
    mom_factor = next((f for f in factors if f.name == "momentum"), None)
    cap_factor = next((f for f in factors if f.name == "capital"), None)

    risks = []
    if vol_factor and vol_factor.score < -30:     risks.append(f"波动率过高(ATR={atr_pct:.2f}%)，仓位需控制")
    if mom_factor and "背离" in mom_factor.detail: risks.append(mom_factor.detail.split("；")[0])
    if cap_factor:
        for note in cap_factor.detail.split("；"):
            if "极端" in note or "拥挤" in note: risks.append(note)
    if not risks: risks.append("信号强度适中，注意多时间框架确认")

    return TradeSignal(
        direction=direction,
        strength=strength,
        composite_score=round(composite, 2),
        confidence_pct=confidence,
        entry_low=round(entry_low, 6),
        entry_high=round(entry_high, 6),
        stop_loss=round(stop_loss, 6),
        take_profit_1=round(take_profit_1, 6),
        take_profit_2=round(take_profit_2, 6),
        risk_reward_ratio=rrr,
        suggested_position_pct=suggested_pos,
        atr_value=round(atr_v, 6),
        atr_pct=round(atr_pct, 3),
        key_resistances=[round(r, 6) for r in resistances],
        key_supports=[round(s, 6) for s in supports],
        vwap_price=round(vwap_now, 6),
        bb_upper=round(bb_upper, 6),
        bb_lower=round(bb_lower, 6),
        supertrend_line=round(st_now, 6),
        invalidation_price=round(invalidation, 6),
        key_risk="；".join(risks[:2]),
        factors=factors,
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM 数据包构建
# ─────────────────────────────────────────────────────────────────────────────

def _build_llm_payload(symbol: str, signal: TradeSignal, raw_data: dict) -> dict:
    """
    把 TradeSignal 转成结构化的 LLM 提示数据包
    LLM 收到这个包后可以直接生成专业的操盘建议
    """
    direction_cn = {"long": "做多", "short": "做空", "neutral": "观望"}
    strength_cn  = {"strong": "强", "moderate": "中等", "weak": "弱", "none": "无"}

    factor_summary = []
    for f in signal.factors:
        factor_summary.append({
            "因子": f.name,
            "评分": round(f.score, 1),
            "信号": f.signal,
            "强度": f.strength,
            "说明": f.detail,
        })

    payload = {
        "symbol": symbol,
        "交易信号": {
            "方向": direction_cn.get(signal.direction, signal.direction),
            "强度": strength_cn.get(signal.strength, signal.strength),
            "综合评分": signal.composite_score,
            "胜率估计": f"{signal.confidence_pct}%",
        },
        "可执行操作": {
            "入场区间": {
                "低": signal.entry_low,
                "高": signal.entry_high,
                "说明": "建议在此区间挂单，等待价格回踩入场"
            },
            "止损位": signal.stop_loss,
            "止盈1": {
                "价格": signal.take_profit_1,
                "操作": "触及后减仓50%，止损上移至成本价"
            },
            "止盈2": {
                "价格": signal.take_profit_2,
                "操作": "触及后继续持有剩余仓位，启用移动止损"
            },
            "风险回报比": signal.risk_reward_ratio,
            "建议仓位": f"{int(signal.suggested_position_pct * 100)}%（相对于计划总仓位）",
            "信号失效条件": f"价格突破并收盘于 {signal.invalidation_price}，信号失效立即止损",
        },
        "关键价位": {
            "VWAP": signal.vwap_price,
            "Supertrend支撑/压力": signal.supertrend_line,
            "布林上轨": signal.bb_upper,
            "布林下轨": signal.bb_lower,
            "上方阻力位": signal.key_resistances,
            "下方支撑位": signal.key_supports,
        },
        "波动率": {
            "ATR(14)": signal.atr_value,
            "ATR占价格比": f"{signal.atr_pct:.3f}%",
            "说明": "止损 = 1.5×ATR，TP1 = 2×ATR，TP2 = 3.5×ATR"
        },
        "主要风险": signal.key_risk,
        "六因子明细": factor_summary,
    }

    # 附加新闻（最新3条）
    if "get_recent_news" in raw_data and raw_data["get_recent_news"]:
        news = raw_data["get_recent_news"]
        if isinstance(news, list):
            payload["近期新闻"] = news[:3]

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# Skill 主类
# ─────────────────────────────────────────────────────────────────────────────

class QuantitativeAnalysisSkill:
    """
    量化决策分析 Skill — 专业六因子重构版

    依赖的外部服务（与原版相同）：
        get_header_data / get_kline_data / get_recent_news
        get_buy_sell_ratio / get_open_interest / get_funding_rate

    输出：结构化 TradeSignal + LLM 数据包
    """

    name        = "quantitative_analysis"
    description = "量化决策分析（专业六因子重构版：趋势/动量/量价/资金/波动风险/市场结构）"

    # ── 如果你使用原有的 BaseSkill 框架，保留以下两个方法 ───────────────────

    def match(self, intent, mode="chat") -> bool:
        return getattr(intent, "intent_type", None) == "analyze_quantitative"

    async def execute_async(self, symbol: str, intent=None):
        """与原框架兼容的异步入口"""
        # 动态导入数据服务（与原项目结构对齐）
        try:
            from app.services.data_service import (
                get_header_data, get_kline_data, get_recent_news,
                get_buy_sell_ratio, get_open_interest, get_funding_rate,
            )
        except ImportError:
            raise RuntimeError("数据服务未找到，请确认 app.services.data_service 路径正确")

        tasks = [
            asyncio.to_thread(get_header_data,      symbol),
            asyncio.to_thread(get_kline_data,        symbol),
            asyncio.to_thread(get_recent_news,       symbol, limit=10),
            asyncio.to_thread(get_buy_sell_ratio,    symbol),
            asyncio.to_thread(get_open_interest,     symbol),
            asyncio.to_thread(get_funding_rate,      symbol),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        api_names = [
            "get_header_data", "get_kline_data", "get_recent_news",
            "get_buy_sell_ratio", "get_open_interest", "get_funding_rate",
        ]
        raw_data  = {}
        api_calls = []
        for name, result in zip(api_names, results):
            if not isinstance(result, Exception):
                raw_data[name] = result
                api_calls.append(name)
            else:
                print(f"  ⚠️ {name} 调用失败: {result}")

        llm_data = self.analyze(symbol, raw_data)

        # 与原 SkillResult 接口对齐
        try:
            from app.skills.base import SkillResult
            return SkillResult(
                skill_name=self.name,
                data=llm_data,
                timestamp=self._get_timestamp(),
                api_calls=api_calls,
            )
        except ImportError:
            return {"skill_name": self.name, "data": llm_data, "api_calls": api_calls}

    # ── 核心分析（可独立调用，方便测试）────────────────────────────────────

    def analyze(self, symbol: str, raw_data: dict) -> dict:
        """
        主分析入口
        raw_data: 各 API 返回值组成的 dict
        返回: LLM 数据包（dict）
        """
        ohlcv = _parse_kline(raw_data.get("get_kline_data"))
        if ohlcv is None:
            return {
                "error": "K线数据不足（需要至少60根），无法进行六因子分析",
                "symbol": symbol,
            }

        price = ohlcv["closes"][-1]

        # 计算六个因子
        factors = [
            _score_trend(ohlcv),
            _score_momentum(ohlcv),
            _score_volume_price(ohlcv),
            _score_capital(raw_data),
            _score_volatility_risk(ohlcv, price),
            _score_market_structure(ohlcv, price),
        ]

        # 合成可执行交易信号
        signal = _build_trade_signal(factors, ohlcv, raw_data, symbol)

        # 构建 LLM 数据包
        return _build_llm_payload(symbol, signal, raw_data)

    @staticmethod
    def _get_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()