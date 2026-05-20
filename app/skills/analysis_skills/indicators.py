"""
技术指标计算库 — 纯 Python 实现，无需 ta-lib
所有函数接收 list[float]，返回 list[float]，与 closes 等长
"""
from __future__ import annotations
import math
from typing import Optional


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _check_min_len(data: list, n: int, name: str) -> None:
    if len(data) < n:
        raise ValueError(f"{name} 需要至少 {n} 条数据，当前只有 {len(data)} 条")


# ── 移动平均 ─────────────────────────────────────────────────────────────────

def sma(data: list[float], period: int) -> list[float]:
    _check_min_len(data, period, "SMA")
    result = [float("nan")] * (period - 1)
    for i in range(period - 1, len(data)):
        result.append(sum(data[i - period + 1: i + 1]) / period)
    return result


def ema(data: list[float], period: int) -> list[float]:
    """指数移动平均 k=2/(period+1)"""
    _check_min_len(data, period, "EMA")
    k = 2.0 / (period + 1)
    result = [float("nan")] * (period - 1)
    result.append(sum(data[:period]) / period)
    for i in range(period, len(data)):
        result.append(data[i] * k + result[-1] * (1 - k))
    return result


def wilder_smooth(data: list[float], period: int) -> list[float]:
    """Wilder 平滑 k=1/period，用于 ATR/ADX/RSI"""
    _check_min_len(data, period, "WilderSmooth")
    k = 1.0 / period
    result = [float("nan")] * (period - 1)
    result.append(sum(data[:period]) / period)
    for i in range(period, len(data)):
        result.append(data[i] * k + result[-1] * (1 - k))
    return result


# ── 趋势 ──────────────────────────────────────────────────────────────────────

def ema_triple(closes, fast=9, mid=21, slow=55):
    return ema(closes, fast), ema(closes, mid), ema(closes, slow)


def adx(highs, lows, closes, period=14):
    """返回 (adx, plus_di, minus_di)，与 closes 等长"""
    n = len(closes)
    _check_min_len(closes, period * 2, "ADX")
    nan = float("nan")

    tr_list, pdm_list, mdm_list = [], [], []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up   = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        tr_list.append(tr)
        pdm_list.append(up   if up   > down and up   > 0 else 0.0)
        mdm_list.append(down if down > up   and down > 0 else 0.0)

    atr_w = wilder_smooth(tr_list,  period)
    pdm_w = wilder_smooth(pdm_list, period)
    mdm_w = wilder_smooth(mdm_list, period)

    plus_di, minus_di, dx_list = [], [], []
    for i in range(len(atr_w)):
        if math.isnan(atr_w[i]) or atr_w[i] == 0:
            plus_di.append(nan); minus_di.append(nan); dx_list.append(nan)
        else:
            pdi = 100 * _safe_div(pdm_w[i], atr_w[i])
            mdi = 100 * _safe_div(mdm_w[i], atr_w[i])
            plus_di.append(pdi); minus_di.append(mdi)
            denom = pdi + mdi
            dx_list.append(100 * _safe_div(abs(pdi - mdi), denom) if denom else nan)

    valid_dx = [x for x in dx_list if not math.isnan(x)]
    adx_vals = [nan] * len(dx_list)
    if len(valid_dx) >= period:
        start = next(i for i, v in enumerate(dx_list) if not math.isnan(v))
        adx_vals[start + period - 1] = sum(valid_dx[:period]) / period
        k = 1.0 / period
        for i in range(start + period, len(dx_list)):
            prev = adx_vals[i - 1]
            adx_vals[i] = dx_list[i] * k + prev * (1 - k) if not math.isnan(prev) else nan

    # dx 序列比 closes 少 1（从 i=1 开始），前补 [nan] 对齐
    pad = [nan]
    return pad + adx_vals, pad + plus_di, pad + minus_di


def supertrend(highs, lows, closes, period=10, multiplier=3.0):
    """返回 (st_line, direction)，direction: 1=多头 -1=空头"""
    atr_vals = atr(highs, lows, closes, period)
    nan = float("nan")
    n = len(closes)
    ub = [nan] * n
    lb = [nan] * n
    st = [nan] * n
    direction = [0] * n

    for i in range(period, n):
        if math.isnan(atr_vals[i]):
            continue
        hl2 = (highs[i] + lows[i]) / 2
        bu  = hl2 + multiplier * atr_vals[i]
        bl  = hl2 - multiplier * atr_vals[i]

        ub[i] = bu if (math.isnan(ub[i-1]) or bu < ub[i-1] or closes[i-1] > ub[i-1]) else ub[i-1]
        lb[i] = bl if (math.isnan(lb[i-1]) or bl > lb[i-1] or closes[i-1] < lb[i-1]) else lb[i-1]

        if   math.isnan(st[i-1]):       direction[i] = 1
        elif st[i-1] == ub[i-1]:        direction[i] = -1 if closes[i] > ub[i] else 1
        else:                            direction[i] =  1 if closes[i] < lb[i] else -1

        st[i] = lb[i] if direction[i] == 1 else ub[i]

    return st, direction


# ── 动量 ──────────────────────────────────────────────────────────────────────

def rsi(closes, period=14):
    _check_min_len(closes, period + 1, "RSI")
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0)); losses.append(max(-d, 0.0))

    ag = sum(gains[:period])  / period
    al = sum(losses[:period]) / period
    result = [float("nan")] * period
    result.append(100 - 100 / (1 + _safe_div(ag, al, 100.0)))

    for i in range(period, len(gains)):
        ag = (ag * (period-1) + gains[i])  / period
        al = (al * (period-1) + losses[i]) / period
        result.append(100 - 100 / (1 + _safe_div(ag, al, 100.0)))
    return result


def macd(closes, fast=12, slow=26, signal=9):
    """返回 (macd_line, signal_line, histogram)"""
    nan = float("nan")
    ml = [
        (f - s) if not (math.isnan(f) or math.isnan(s)) else nan
        for f, s in zip(ema(closes, fast), ema(closes, slow))
    ]
    valid = [(i, v) for i, v in enumerate(ml) if not math.isnan(v)]
    sl = [nan] * len(ml)
    hist = [nan] * len(ml)
    if len(valid) >= signal:
        sig_vals = ema([v for _, v in valid], signal)
        for j, (orig_i, _) in enumerate(valid):
            if j < len(sig_vals) and not math.isnan(sig_vals[j]):
                sl[orig_i]   = sig_vals[j]
                hist[orig_i] = ml[orig_i] - sig_vals[j]
    return ml, sl, hist


def detect_divergence(closes, indicator, lookback=20):
    """返回 'bullish_divergence' | 'bearish_divergence' | 'none'"""
    valid = [(c, v) for c, v in zip(closes, indicator) if not math.isnan(v)]
    if len(valid) < lookback:
        return "none"
    rc = [x[0] for x in valid[-lookback:]]
    ri = [x[1] for x in valid[-lookback:]]
    if rc[-1] >= max(rc[:-1]) and ri[-1] < max(ri[:-1]):
        return "bearish_divergence"
    if rc[-1] <= min(rc[:-1]) and ri[-1] > min(ri[:-1]):
        return "bullish_divergence"
    return "none"


# ── 量价 ──────────────────────────────────────────────────────────────────────

def obv(closes, volumes):
    result = [volumes[0]]
    for i in range(1, len(closes)):
        if   closes[i] > closes[i-1]: result.append(result[-1] + volumes[i])
        elif closes[i] < closes[i-1]: result.append(result[-1] - volumes[i])
        else:                          result.append(result[-1])
    return result


def vwap(highs, lows, closes, volumes):
    """滚动 VWAP（非当日重置）"""
    result, cum_pv, cum_vol = [], 0.0, 0.0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        tp      = (h + l + c) / 3
        cum_pv  += tp * v
        cum_vol += v
        result.append(_safe_div(cum_pv, cum_vol, c))
    return result


# ── 波动率 ────────────────────────────────────────────────────────────────────

def atr(highs, lows, closes, period=14):
    """与 closes 等长"""
    nan = float("nan")
    tr_list = [nan]
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
    valid_tr = [v for v in tr_list if not math.isnan(v)]  # len = n-1
    atr_raw  = wilder_smooth(valid_tr, period)             # len = n-1
    # 补齐到 n：第 0 位 nan，后接 atr_raw
    return [nan] + atr_raw


def bollinger_bands(closes, period=20, std_dev=2.0):
    """返回 (upper, middle, lower)"""
    nan = float("nan")
    mid = sma(closes, period)
    upper, lower = [], []
    for i in range(len(closes)):
        if math.isnan(mid[i]):
            upper.append(nan); lower.append(nan)
        else:
            w  = closes[i - period + 1: i + 1]
            sd = math.sqrt(sum((x - mid[i])**2 for x in w) / period)
            upper.append(mid[i] + std_dev * sd)
            lower.append(mid[i] - std_dev * sd)
    return upper, mid, lower


# ── 市场结构 ──────────────────────────────────────────────────────────────────

def swing_points(highs, lows, left=3, right=3):
    """返回 (swing_highs, swing_lows)，非摆动位为 None"""
    n = len(highs)
    sh: list[Optional[float]] = [None] * n
    sl: list[Optional[float]] = [None] * n
    for i in range(left, n - right):
        if highs[i] == max(highs[i-left: i+right+1]): sh[i] = highs[i]
        if lows[i]  == min(lows[i-left:  i+right+1]): sl[i] = lows[i]
    return sh, sl


def key_levels(swing_highs, swing_lows, current_price, n_levels=3):
    """返回 (resistances, supports) 各最多 n_levels 个"""
    res = sorted(v for v in swing_highs if v is not None and v > current_price)
    sup = sorted((v for v in swing_lows if v is not None and v < current_price), reverse=True)
    return res[:n_levels], sup[:n_levels]