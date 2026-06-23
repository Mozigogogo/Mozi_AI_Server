"""
量化决策分析 Skill — 专业六因子重构版（优化版）
因子体系：趋势强度 / 动量质量 / 量价结构 / 资金结构 / 波动风险 / 市场结构
输出：可执行交易指令（入场区间、止损、止盈、仓位建议）

本次优化要点：
1. 去除冗余的局部 import，统一使用文件头部已导入的指标函数
2. 新增"市场状态自适应权重"：用 ADX 判断趋势市/盘整市，动态调整六因子权重
3. 新增轻量回测函数 backtest_trend_momentum_rule，用历史K线滚动验证简化规则
   的历史胜率，便于校准阈值（不影响交易信号本身）
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from .indicators import (
    ema, ema_triple, adx, supertrend,
    rsi, macd, detect_divergence,
    obv, vwap,
    atr, bollinger_bands,
    swing_points, key_levels,
)
from app.utils.logger import get_logger

logger = get_logger("app.skills.analysis_skills.quantitative")

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

    # 双周期融合诊断（日线 + 1h）
    daily_composite: Optional[float] = None      # 日线 composite
    hourly_composite: Optional[float] = None     # 1h composite（None=数据不足）
    tf_agreement: Optional[str] = None           # agreement/disagreement/neutral/insufficient_1h_data


# ─────────────────────────────────────────────────────────────────────────────
# K 线解析
# ─────────────────────────────────────────────────────────────────────────────

def _parse_kline(kline_data: dict, volume_data: list = None, min_bars: int = 25) -> Optional[dict]:
    """
    从 get_kline_data 返回值中解析 OHLCV 列表
    支持两种格式:
      - [open, high, low, close]（4字段，需 volume_data 补充成交量）
      - [timestamp, open, high, low, close, volume]（6字段，已含成交量）
    返回 {"opens", "highs", "lows", "closes", "volumes"} 或 None
    """
    if not kline_data or not isinstance(kline_data, dict):
        return None
    values = kline_data.get("values", [])
    dates  = kline_data.get("categoryData", [])
    if not values or len(values) < min_bars:
        return None

    # 构建日期→成交量的映射
    vol_map = {}
    if volume_data and isinstance(volume_data, list):
        for item in volume_data:
            if isinstance(item, dict):
                dt = item.get("dt", "").replace("-", "/")
                vol_map[dt] = item.get("usd", 0.0)

    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i, bar in enumerate(values):
        if not isinstance(bar, (list, tuple)) or len(bar) < 4:
            continue
        try:
            if len(bar) >= 6:
                # [timestamp, open, high, low, close, volume]
                opens.append(float(bar[1]))
                highs.append(float(bar[2]))
                lows.append(float(bar[3]))
                closes.append(float(bar[4]))
                volumes.append(float(bar[5]))
            elif len(bar) >= 4:
                # [open, high, low, close]
                opens.append(float(bar[0]))
                highs.append(float(bar[1]))
                lows.append(float(bar[2]))
                closes.append(float(bar[3]))
                # 按日期匹配成交量（usd 成交额）
                dt = dates[i] if i < len(dates) else ""
                vol = vol_map.get(dt, 1.0)
                volumes.append(vol)
        except (ValueError, TypeError):
            continue

    if len(closes) < min_bars:
        return None
    return dict(opens=opens, highs=highs, lows=lows, closes=closes, volumes=volumes)


def _extract_realtime_price(raw_data: dict) -> Optional[float]:
    """从 header 数据中提取统一实时价。"""
    header = raw_data.get("get_header_data") or raw_data.get("header_data") or raw_data.get("header")
    if not isinstance(header, dict):
        return None

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
    """
    用 header 实时价替换最后一根 close，保持展示价格和指标/价位计算一致。
    high/low 同步扩展，避免实时价越过日线高低点后 ATR/结构计算失真。
    """
    if not ohlcv or not current_price or current_price <= 0:
        return ohlcv

    for key in ("opens", "highs", "lows", "closes"):
        if key not in ohlcv or not ohlcv[key]:
            return ohlcv

    ohlcv["closes"][-1] = float(current_price)
    ohlcv["highs"][-1] = max(float(ohlcv["highs"][-1]), float(current_price))
    ohlcv["lows"][-1] = min(float(ohlcv["lows"][-1]), float(current_price))
    ohlcv["realtime_price"] = float(current_price)
    ohlcv["price_source"] = "header.currentPrice"
    return ohlcv


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
    obv_ema = ema(obv_vals, 10)
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
# 市场状态自适应：用 ADX 调整六因子权重
# ─────────────────────────────────────────────────────────────────────────────

# 不同 regime 下的六因子权重（基准权重见各 _score_xxx 函数）
# - trending：趋势因子主导，动量辅助
# - extreme_trend：趋势权重进一步放大
# - weak_trend：均衡分布，市场结构权重大幅提升
# - ranging：动量/趋势衰减，结构/资金相对抬升
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "trending":       {"trend": 0.35, "momentum": 0.25, "volume_price": 0.15, "capital": 0.15, "volatility_risk": 0.05, "market_structure": 0.05},
    "extreme_trend":  {"trend": 0.40, "momentum": 0.25, "volume_price": 0.15, "capital": 0.10, "volatility_risk": 0.05, "market_structure": 0.05},
    "weak_trend":     {"trend": 0.20, "momentum": 0.20, "volume_price": 0.20, "capital": 0.20, "volatility_risk": 0.10, "market_structure": 0.10},
    "ranging":        {"trend": 0.10, "momentum": 0.15, "volume_price": 0.20, "capital": 0.25, "volatility_risk": 0.15, "market_structure": 0.15},
}


def _determine_market_regime(ohlcv: dict) -> str:
    """用 ADX 判断市场状态：extreme_trend / trending / weak_trend / ranging。"""
    try:
        # adx 返回 (adx_vals, plus_di, minus_di) 三元组，只取 adx 序列
        adx_vals, _, _ = adx(ohlcv["highs"], ohlcv["lows"], ohlcv["closes"], 14)
        cur_adx = None
        for v in reversed(adx_vals or []):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                cur_adx = v
                break
        if cur_adx is None:
            return "weak_trend"
        if cur_adx >= 35: return "extreme_trend"
        if cur_adx >= 25: return "trending"
        if cur_adx >= 18: return "weak_trend"
        return "ranging"
    except Exception:
        return "weak_trend"


def _apply_adaptive_weights(factors: list[FactorResult], regime: str) -> None:
    """根据 regime 覆写六因子权重（原地修改 FactorResult.weight）。"""
    weights = REGIME_WEIGHTS.get(regime)
    if not weights:
        return
    for f in factors:
        if f.name in weights:
            f.weight = weights[f.name]


# ─────────────────────────────────────────────────────────────────────────────
# 双周期融合：日线 + 1h
# ─────────────────────────────────────────────────────────────────────────────

# 双周期融合权重（日线 vs 1h），按 regime 调整
# - trending：日线主导，1h 提供时机
# - extreme_trend：日线进一步主导（强趋势不轻易反转）
# - weak_trend：均衡（短期信号此时更有价值）
# - ranging：1h 反而主导（震荡市短期反转是主要 alpha）
DUAL_TF_WEIGHTS: dict[str, tuple[float, float]] = {
    "trending":       (0.65, 0.35),
    "extreme_trend":  (0.75, 0.25),
    "weak_trend":     (0.55, 0.45),
    "ranging":        (0.45, 0.55),
}

# 共振/分歧调整系数（应用到加权平均后的 composite）
AGREEMENT_BONUS = 0.20        # 日线 vs 1h 同向 → composite 绝对值 +20%
DISAGREEMENT_PENALTY = 0.30   # 日线 vs 1h 反向 → composite 绝对值 -30%
MIN_1H_SIGNAL = 15            # 1h composite 绝对值阈值，低于此视为噪声不参与共振判定


def _compute_dual_tf_composite(
    factors_daily: list[FactorResult],
    factors_1h: Optional[list[FactorResult]],
    regime: str,
) -> tuple[float, dict]:
    """计算双周期融合 composite。

    返回 (composite_final -100~+100, diagnostic_dict)。
    diagnostic 含每日 composite、1h composite、共振状态、应用的权重。
    1h 数据不足时退化到纯日线 composite。
    """
    composite_daily = sum(f.score * f.weight for f in factors_daily)

    if not factors_1h or len(factors_1h) < 6:
        return composite_daily, {
            "daily_composite": round(composite_daily, 2),
            "hourly_composite": None,
            "agreement": "insufficient_1h_data",
            "weights": (1.0, 0.0),
            "fused_composite": round(composite_daily, 2),
        }

    composite_1h = sum(f.score * f.weight for f in factors_1h)
    w_daily, w_1h = DUAL_TF_WEIGHTS.get(regime, (0.65, 0.35))

    composite_base = composite_daily * w_daily + composite_1h * w_1h

    sign_daily = 1 if composite_daily > 0 else (-1 if composite_daily < 0 else 0)
    sign_1h = 1 if composite_1h > 0 else (-1 if composite_1h < 0 else 0)

    if sign_daily != 0 and sign_daily == sign_1h and abs(composite_1h) >= MIN_1H_SIGNAL:
        composite = composite_base * (1 + AGREEMENT_BONUS)
        agreement = "agreement"
    elif sign_daily != 0 and sign_1h != 0 and sign_daily != sign_1h and abs(composite_1h) >= MIN_1H_SIGNAL:
        composite = composite_base * (1 - DISAGREEMENT_PENALTY)
        agreement = "disagreement"
    else:
        composite = composite_base
        agreement = "neutral"

    composite = max(-100, min(100, composite))
    return composite, {
        "daily_composite": round(composite_daily, 2),
        "hourly_composite": round(composite_1h, 2),
        "agreement": agreement,
        "weights": (w_daily, w_1h),
        "fused_composite": round(composite, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 合成信号 → 可执行交易指令
# ─────────────────────────────────────────────────────────────────────────────

def _build_trade_signal(
    factors: list[FactorResult],
    ohlcv: dict,
    raw_data: dict,
    symbol: str,
    override_composite: Optional[float] = None,
    dual_tf_info: Optional[dict] = None,
) -> TradeSignal:
    price   = ohlcv["closes"][-1]
    highs   = ohlcv["highs"]
    lows    = ohlcv["lows"]
    closes  = ohlcv["closes"]
    volumes = ohlcv["volumes"]

    # 加权综合评分（双周期融合时由外部传入 override_composite）
    composite = override_composite if override_composite is not None else sum(f.score * f.weight for f in factors)

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
    atr_vals  = atr(highs, lows, closes, 14)
    vwap_vals = vwap(highs, lows, closes, volumes)
    bb_u, _, bb_l = bollinger_bands(closes, 20, 2.0)
    st_line, _ = supertrend(highs, lows, closes, 10, 3.0)

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

    # 入场区间：优先用VWAP；若实时价已偏离VWAP过远，改用实时价锚定，避免区间失真。
    anchor_price = vwap_now
    if price > 0 and abs(vwap_now - price) / price > 0.03:
        anchor_price = price

    if direction == "long":
        entry_low    = max(anchor_price - 0.3 * atr_v, price * 0.99)
        entry_high   = min(anchor_price + 0.3 * atr_v, price * 1.02)
        stop_loss    = price - 1.5 * atr_v
        take_profit_1 = price + 2.0 * atr_v
        take_profit_2 = price + 3.5 * atr_v
        invalidation  = stop_loss
    elif direction == "short":
        entry_low    = max(anchor_price - 0.3 * atr_v, price * 0.98)
        entry_high   = min(anchor_price + 0.3 * atr_v, price * 1.01)
        stop_loss    = price + 1.5 * atr_v
        take_profit_1 = price - 2.0 * atr_v
        take_profit_2 = price - 3.5 * atr_v
        invalidation  = stop_loss
    else:
        entry_low = entry_high = stop_loss = take_profit_1 = take_profit_2 = price
        invalidation = price

    if entry_low > entry_high:
        entry_low, entry_high = entry_high, entry_low

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
        daily_composite=(dual_tf_info or {}).get("daily_composite"),
        hourly_composite=(dual_tf_info or {}).get("hourly_composite"),
        tf_agreement=(dual_tf_info or {}).get("agreement"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# LLM 数据包构建
# ─────────────────────────────────────────────────────────────────────────────

_AGREEMENT_CN = {
    "agreement": "同向共振",
    "disagreement": "周期分歧",
    "neutral": "中性",
    "insufficient_1h_data": "1h数据不足",
}


def _build_multi_tf_payload(signal: TradeSignal) -> dict:
    """构建"多周期共振"section：展示日线 vs 1h 的 composite 和共振状态。"""
    agreement = signal.tf_agreement or "insufficient_1h_data"
    daily = signal.daily_composite
    hourly = signal.hourly_composite

    if agreement == "insufficient_1h_data" or hourly is None:
        explanation = "1h K线数据不足，仅按日线综合评分出信号"
    elif agreement == "agreement":
        explanation = f"日线({daily:.0f})与1h({hourly:.0f})同向，共振加成 +20%"
    elif agreement == "disagreement":
        explanation = f"日线({daily:.0f})与1h({hourly:.0f})反向，分歧降级 -30%"
    else:
        explanation = f"日线({daily:.0f})与1h({hourly:.0f})综合判定"

    return {
        "日线综合评分": daily,
        "1h综合评分": hourly,
        "共振状态": _AGREEMENT_CN.get(agreement, "未知"),
        "融合后评分": signal.composite_score,
        "说明": explanation,
    }


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
        "实时数据": _build_realtime_payload(raw_data),
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
        "多周期共振": _build_multi_tf_payload(signal),
    }

    # 附加新闻（最新3条）
    if "get_recent_news" in raw_data and raw_data["get_recent_news"]:
        news = raw_data["get_recent_news"]
        if isinstance(news, list):
            payload["近期新闻"] = news[:3]

    return payload


def _build_realtime_payload(raw_data: dict) -> dict:
    """构建给 LLM 的实时价格口径说明。"""
    header = raw_data.get("get_header_data") or raw_data.get("header_data") or raw_data.get("header")
    if not isinstance(header, dict):
        return {"价格来源": "K线最后收盘价"}

    return {
        "当前价格": header.get("currentPrice"),
        "24h涨跌幅": header.get("priceChangePercentage_24h"),
        "24h最高": header.get("high_24h"),
        "24h最低": header.get("low_24h"),
        "价格来源": "header.currentPrice",
    }


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
        return getattr(intent, "intent_type", None) in ("analyze_quantitative", "analyze_signal")

    async def execute_async(self, symbol: str, intent=None):
        """与原框架兼容的异步入口"""
        # 动态导入数据服务（与原项目结构对齐）
        try:
            from app.services.data_service import (
                get_header_data, get_kline_data, get_recent_news,
                get_buy_sell_ratio, get_open_interest, get_funding_rate,
                get_trade_volume,
            )
        except ImportError:
            raise RuntimeError("数据服务未找到，请确认 app.services.data_service 路径正确")

        tasks = [
            asyncio.to_thread(get_header_data,      symbol),
            asyncio.to_thread(get_kline_data,        symbol),       # 日线60条
            asyncio.to_thread(get_trade_volume,      symbol),       # 每日成交量
            asyncio.to_thread(get_recent_news,       symbol, limit=10),
            asyncio.to_thread(get_buy_sell_ratio,    symbol),
            asyncio.to_thread(get_open_interest,     symbol),
            asyncio.to_thread(get_funding_rate,      symbol),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        api_names = [
            "get_header_data", "get_kline_data", "get_trade_volume", "get_recent_news",
            "get_buy_sell_ratio", "get_open_interest", "get_funding_rate",
        ]
        raw_data  = {}
        api_calls = []
        for name, result in zip(api_names, results):
            if not isinstance(result, Exception):
                raw_data[name] = result
                api_calls.append(name)
            else:
                logger.info(f"  ⚠️ {name} 调用失败: {result}")

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
        ohlcv = _parse_kline(
            raw_data.get("get_kline_data"),
            raw_data.get("get_trade_volume"),
        )
        if ohlcv is None:
            return {
                "error": "K线数据不足（需要至少60根），无法进行六因子分析",
                "symbol": symbol,
            }

        realtime_price = _extract_realtime_price(raw_data)
        ohlcv = _apply_realtime_price(ohlcv, realtime_price)
        price = ohlcv["closes"][-1]

        # 日线六因子
        factors = [
            _score_trend(ohlcv),
            _score_momentum(ohlcv),
            _score_volume_price(ohlcv),
            _score_capital(raw_data),
            _score_volatility_risk(ohlcv, price),
            _score_market_structure(ohlcv, price),
        ]

        # 根据市场状态自适应调整六因子权重
        regime = _determine_market_regime(ohlcv)
        _apply_adaptive_weights(factors, regime)

        # 1h 六因子（双周期融合核心）
        ohlcv_1h = raw_data.get("hourly_ohlcv")
        factors_1h = None
        if ohlcv_1h and isinstance(ohlcv_1h, dict) and len(ohlcv_1h.get("closes", [])) >= 55:
            try:
                price_1h = ohlcv_1h["closes"][-1]
                factors_1h = [
                    _score_trend(ohlcv_1h),
                    _score_momentum(ohlcv_1h),
                    _score_volume_price(ohlcv_1h),
                    _score_capital(raw_data),  # 资金因子不分周期，共用同一份
                    _score_volatility_risk(ohlcv_1h, price_1h),
                    _score_market_structure(ohlcv_1h, price_1h),
                ]
                _apply_adaptive_weights(factors_1h, regime)
            except Exception:
                factors_1h = None  # 1h 指标计算失败时降级到纯日线

        # 双周期融合 composite
        fused_composite, dual_tf_info = _compute_dual_tf_composite(factors, factors_1h, regime)

        # 合成可执行交易信号
        signal = _build_trade_signal(
            factors, ohlcv, raw_data, symbol,
            override_composite=fused_composite,
            dual_tf_info=dual_tf_info,
        )

        # 构建 LLM 数据包
        return _build_llm_payload(symbol, signal, raw_data)

    @staticmethod
    def _get_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 历史回测辅助函数（独立于交易信号，用于校准阈值）
# ─────────────────────────────────────────────────────────────────────────────

def backtest_trend_momentum_rule(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    rsi_period: int = 14,
    rsi_buy: float = 50.0,
    rsi_sell: float = 50.0,
    horizon: int = 5,
    fee_pct: float = 0.04,
) -> dict:
    """用 EMA(fast/slow) 金叉死叉 + RSI 过滤做滚动规则回测。

    本函数独立于六因子信号，目的：
      - 用极简规则在历史 K 线上滚动回测，得到"裸趋势规则"的胜率/平均收益
      - 作为阈值校准的基准线，方便后续调整六因子内部阈值时对照

    Args:
        closes: 收盘价序列（需 >= slow + horizon + 1）
        fast/slow: EMA 快慢线周期
        rsi_period: RSI 周期
        rsi_buy/rsi_sell: RSI 过滤阈值
        horizon: 每次入场后的持有 N 根 K 线再平仓
        fee_pct: 单边手续费百分比

    Returns:
        dict: {
            "trades": int, "wins": int, "win_rate": float,
            "avg_pnl_pct": float, "total_pnl_pct": float,
            "long_trades": int, "short_trades": int,
        }
    """
    n = len(closes)
    if n < slow + horizon + 1:
        return {"trades": 0, "wins": 0, "win_rate": 0.0,
                "avg_pnl_pct": 0.0, "total_pnl_pct": 0.0,
                "long_trades": 0, "short_trades": 0}

    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)
    rsi_vals = rsi(closes, rsi_period)

    def _last_valid(arr, idx):
        for v in reversed(arr[: idx + 1]):
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return None

    trades = 0
    wins = 0
    total_pnl = 0.0
    long_trades = 0
    short_trades = 0

    i = slow  # 从 slow 开始确保 EMA 稳定
    while i < n - horizon:
        f_v = _last_valid(fast_ema, i)
        s_v = _last_valid(slow_ema, i)
        r_v = _last_valid(rsi_vals, i)
        f_prev = _last_valid(fast_ema, i - 1)
        s_prev = _last_valid(slow_ema, i - 1)
        if not (f_v and s_v and f_prev and s_prev and r_v is not None):
            i += 1
            continue

        # 金叉 + RSI 在多头区间 → 做多
        golden_cross = f_prev <= s_prev and f_v > s_v and r_v > rsi_buy
        # 死叉 + RSI 在空头区间 → 做空
        death_cross = f_prev >= s_prev and f_v < s_v and r_v < rsi_sell

        if golden_cross:
            direction = "long"; long_trades += 1
        elif death_cross:
            direction = "short"; short_trades += 1
        else:
            i += 1
            continue

        entry = closes[i]
        exit_ = closes[i + horizon]
        gross = (exit_ - entry) / entry * 100 if direction == "long" else (entry - exit_) / entry * 100
        net = gross - 2 * fee_pct  # 开仓 + 平仓
        trades += 1
        total_pnl += net
        if net > 0:
            wins += 1
        i += horizon  # 进入持有期，跳过到下一次可能入场点

    return {
        "trades": trades,
        "wins": wins,
        "win_rate": round(wins / trades, 4) if trades else 0.0,
        "avg_pnl_pct": round(total_pnl / trades, 4) if trades else 0.0,
        "total_pnl_pct": round(total_pnl, 4),
        "long_trades": long_trades,
        "short_trades": short_trades,
    }
