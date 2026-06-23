"""
全市场 Alpha 扫描引擎 — 动态币种 + 10路并行

架构：
1. 从 discovery API 动态获取全市场币种
2. 10路并发扫描（asyncio.Semaphore 控制）
3. 按置信度排序返回 Top-N 信号卡
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass

from app.services.data_service import get_discovery_coins
from app.signals.fusion import fuse_signals
from app.signals.backtest import backtest_signal
from app.signals.models import SignalSource, SignalDirection
from config.settings import settings as app_settings
import app.bigorder.deps as bigorder_deps


@dataclass
class ScanResult:
    coin: str
    signal_card: Any = None  # SignalCard or None
    backtest: Optional[dict] = None
    error: Optional[str] = None
    elapsed: float = 0.0


def get_bigorder_12h_signal(coin: str, weight: float) -> Optional[SignalSource]:
    """
    获取12小时大单聚合信号（Alpha扫描专用）

    与实时5分钟大单引擎不同，这里聚合12小时的历史大单tick，
    用于量化层面的方向判断。不修改现有大单侦测引擎。
    """
    consumer = bigorder_deps.consumer if hasattr(bigorder_deps, 'consumer') else None
    if not consumer:
        return None

    total_buy = 0.0
    total_sell = 0.0
    buy_count = 0
    sell_count = 0
    active_exchanges = []

    for exchange in app_settings.exchanges:
        try:
            buy_ticks, sell_ticks = consumer.fetch_ticks(exchange, coin, 43200)
            for t in buy_ticks:
                total_buy += t.amount
                buy_count += 1
            for t in sell_ticks:
                total_sell += t.amount
                sell_count += 1
            if buy_ticks or sell_ticks:
                active_exchanges.append(exchange)
        except Exception:
            continue

    total_ticks = buy_count + sell_count
    if total_ticks == 0:
        return None

    net_flow = total_buy - total_sell
    total_flow = total_buy + total_sell

    # 方向判断
    if net_flow > 0 and total_buy > total_sell * 1.2:
        direction = SignalDirection.LONG
    elif net_flow < 0 and total_sell > total_buy * 1.2:
        direction = SignalDirection.SHORT
    else:
        direction = SignalDirection.NEUTRAL

    # 分数计算
    flow_imbalance = abs(net_flow) / total_flow * 100 if total_flow > 0 else 0
    density_score = min(30, total_ticks * 0.5)
    score = min(100, flow_imbalance + density_score)

    if direction == SignalDirection.NEUTRAL:
        score *= 0.5

    # 金额格式化
    def _fmt_amt(v):
        if abs(v) >= 1e6:
            return f"${v / 1e6:.1f}M"
        elif abs(v) >= 1e3:
            return f"${v / 1e3:.0f}K"
        else:
            return f"${v:.0f}"

    detail = (
        f"12h聚合({'+'.join(active_exchanges[:3])}), "
        f"买{_fmt_amt(total_buy)}/卖{_fmt_amt(total_sell)}, "
        f"净流入{_fmt_amt(net_flow)}, {total_ticks}笔大单"
    )

    return SignalSource(
        name="bigorder_anomaly",
        score=score,
        direction=direction,
        weight=weight,
        detail=detail,
    )


# ── A1+A2: 时间衰减大单信号 ────────────────────────────────────────────────────
# 半衰期映射：vol_regime → T½ (分钟)
# quiet=180（横盘慢节奏）/ normal=90（默认）/ high=60（波动放大）/ extreme=45（暴拉暴跌）
_VOL_REGIME_HALF_LIFE_MIN: Dict[str, int] = {
    "quiet": 180,
    "normal": 90,
    "high": 60,
    "extreme": 45,
}

# 进程内 vol_regime 平滑切换缓存：coin → (current_half_life_min, last_update_ts, target_half_life_min)
_T_SMOOTH_CACHE: Dict[str, Tuple[float, float, int]] = {}
_T_SMOOTH_TRANSITION_SECONDS = 1800.0  # 30 分钟线性过渡，避免硬跳


def decay_weight(age_seconds: float, half_life_seconds: float) -> float:
    """指数衰减权重：weight = 2^(-age / T½)。age < 0 时返回 1.0（防止时钟漂移）。"""
    if age_seconds <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_seconds / half_life_seconds)


def _get_smoothed_half_life(coin: str, vol_regime: str) -> int:
    """vol_regime 切换时 30 分钟线性过渡，避免 T½ 硬跳导致打分跳变"""
    target = _VOL_REGIME_HALF_LIFE_MIN.get(vol_regime, 90)
    now = time.time()

    if coin not in _T_SMOOTH_CACHE:
        _T_SMOOTH_CACHE[coin] = (float(target), now, target)
        return target

    last_current, last_ts, last_target = _T_SMOOTH_CACHE[coin]

    # 完全稳定：目标和当前值都一致 → 仅刷新时间戳
    if target == last_target and last_current == target:
        _T_SMOOTH_CACHE[coin] = (float(target), now, target)
        return target

    # 否则可能处于过渡中（目标可能 == last_target 也可能 != last_target）
    elapsed = now - last_ts
    if elapsed >= _T_SMOOTH_TRANSITION_SECONDS:
        # 过渡完成
        _T_SMOOTH_CACHE[coin] = (float(target), now, target)
        return target

    # 过渡中：从 last_current 线性逼近 target
    progress = elapsed / _T_SMOOTH_TRANSITION_SECONDS
    smoothed = last_current + (target - last_current) * progress
    # 注意：过渡中不更新 last_current 和 last_ts，保持锚点直到过渡完成
    _T_SMOOTH_CACHE[coin] = (last_current, last_ts, target)
    return int(round(smoothed))


def get_bigorder_decay_signal(
    coin: str,
    weight: float,
    vol_regime: str = "normal",
    dual_window: bool = False,
) -> Optional[SignalSource]:
    """
    时间衰减加权的大单聚合信号（替代/补充 get_bigorder_12h_signal）

    - T½ 自适应 vol_regime：quiet=180 / normal=90 / high=60 / extreme=45 分钟
    - 切换 vol_regime 时 30 分钟平滑过渡
    - 拉 12h ticks 但用指数衰减权重，前 1h 占主导，4h 后剩 ~12%，12h 后约 1%
    - dual_window=True 时（极端波动）同时算 45min + 180min 双版本，
      方向冲突时 confidence *= 0.4

    Args:
        coin: 币种
        weight: 信号源权重
        vol_regime: quiet/normal/high/extreme
        dual_window: 是否启用双窗对比（仅 vol_regime=extreme 时推荐开启）
    """
    consumer = bigorder_deps.consumer if hasattr(bigorder_deps, "consumer") else None
    if not consumer:
        return None

    half_life_min = _get_smoothed_half_life(coin, vol_regime)
    half_life_sec_primary = half_life_min * 60.0

    # 双窗对比：极端波动时额外算一个长窗（180min）作为上下文锚
    half_life_sec_context = 180 * 60 if dual_window else None

    now_ms = int(time.time() * 1000)

    # 主窗（自适应 T½）累计
    primary_buy, primary_sell = 0.0, 0.0
    # 上下文窗（固定 180min，仅 dual_window 时）
    ctx_buy, ctx_sell = 0.0, 0.0

    buy_count = 0
    sell_count = 0
    active_exchanges: List[str] = []

    for exchange in app_settings.exchanges:
        try:
            buy_ticks, sell_ticks = consumer.fetch_ticks(exchange, coin, 43200)  # 12h 拉满
            for t in buy_ticks:
                age_sec = (now_ms - t.deal_timestamp) / 1000.0
                primary_buy += t.amount * decay_weight(age_sec, half_life_sec_primary)
                if half_life_sec_context:
                    ctx_buy += t.amount * decay_weight(age_sec, half_life_sec_context)
                buy_count += 1
            for t in sell_ticks:
                age_sec = (now_ms - t.deal_timestamp) / 1000.0
                primary_sell += t.amount * decay_weight(age_sec, half_life_sec_primary)
                if half_life_sec_context:
                    ctx_sell += t.amount * decay_weight(age_sec, half_life_sec_context)
                sell_count += 1
            if buy_ticks or sell_ticks:
                active_exchanges.append(exchange)
        except Exception:
            continue

    total_ticks = buy_count + sell_count
    if total_ticks == 0:
        return None

    # 主窗方向 + 分数
    primary_direction, primary_score = _score_decay_window(
        primary_buy, primary_sell, total_ticks
    )

    # 双窗 confidence gate
    confidence_modifier = 1.0
    dual_window_note = ""
    if dual_window and half_life_sec_context:
        ctx_direction, _ = _score_decay_window(ctx_buy, ctx_sell, total_ticks)
        if ctx_direction != primary_direction and primary_direction != SignalDirection.NEUTRAL:
            confidence_modifier = 0.4
            dual_window_note = f" [双窗冲突 T½{half_life_min}↔180min，置信×0.4]"

    score = primary_score * confidence_modifier

    def _fmt(v: float) -> str:
        if abs(v) >= 1e6:
            return f"${v / 1e6:.2f}M"
        if abs(v) >= 1e3:
            return f"${v / 1e3:.1f}K"
        return f"${v:.0f}"

    net = primary_buy - primary_sell
    detail = (
        f"衰减聚合({'+'.join(active_exchanges[:3])}, T½={half_life_min}min, vol={vol_regime}), "
        f"加权买{_fmt(primary_buy)}/卖{_fmt(primary_sell)}, "
        f"净{_fmt(net)}, {total_ticks}笔{dual_window_note}"
    )

    return SignalSource(
        name="bigorder_decay",
        score=score,
        direction=primary_direction,
        weight=weight,
        detail=detail,
    )


def _score_decay_window(
    buy_amount: float, sell_amount: float, total_ticks: int
) -> Tuple[Any, float]:
    """单窗口方向 + 分数计算（与 12h 版本口径一致，便于对比）"""
    net_flow = buy_amount - sell_amount
    total_flow = buy_amount + sell_amount

    if net_flow > 0 and buy_amount > sell_amount * 1.2:
        direction = SignalDirection.LONG
    elif net_flow < 0 and sell_amount > buy_amount * 1.2:
        direction = SignalDirection.SHORT
    else:
        direction = SignalDirection.NEUTRAL

    flow_imbalance = abs(net_flow) / total_flow * 100 if total_flow > 0 else 0
    density_score = min(30, total_ticks * 0.5)
    score = min(100, flow_imbalance + density_score)

    if direction == SignalDirection.NEUTRAL:
        score *= 0.5

    return direction, score



# ── A3: 吸筹 pattern 检测（独立 flag，与时间衰减正交）────────────────────────────
# 流动性门槛：避免低流动性币种（5 笔拆单就触发）误识别
_ACCUM_MIN_TOTAL_TICKS = 20         # 至少 20 笔大单
_ACCUM_MIN_TOTAL_AMOUNT_USD = 100_000  # 至少 $100K 总成交
_ACCUM_MAX_TOP5_CONCENTRATION = 0.6  # top5 集中度上限（>0.6 疑似单一账户拆单）
_ACCUM_TOP5_BUY_RATIO_THRESHOLD = 0.7  # top5 中买入金额占比 > 70%
_ACCUM_SMALL_SELL_RATIO_THRESHOLD = 0.5  # top5 之外的卖单占非-top5 总额 > 50%


def detect_accumulation_pattern(coin: str) -> Optional[Dict[str, Any]]:
    """
    识别「机构吸筹 + 散户出货」pattern。

    特征：
      - Top 5 大单以买入为主（大资金在吸货）
      - Top 5 之外的小单以卖出为主（散户在恐慌/获利了结）
      - 这种组合在简单 net_flow 计算下会被打成 SHORT（卖压金额大），
        但实际是底部反转信号。

    流动性 3 层过滤（防止低流动性币种或拆单误判）：
      1. total_ticks >= 20
      2. total_amount >= $100K
      3. top5 集中度 <= 60%（高度集中 = 疑似拆单）

    Returns:
        通过门槛且命中 pattern 时返回 dict，否则 None。
        dict 含 pattern/top5_buy_ratio/small_sell_ratio/concentration。
    """
    consumer = bigorder_deps.consumer if hasattr(bigorder_deps, "consumer") else None
    if not consumer:
        return None

    all_ticks: List[Any] = []
    active_exchanges: List[str] = []

    for exchange in app_settings.exchanges:
        try:
            buy_ticks, sell_ticks = consumer.fetch_ticks(exchange, coin, 43200)
            for t in buy_ticks:
                t._side = "buy"
                all_ticks.append(t)
            for t in sell_ticks:
                t._side = "sell"
                all_ticks.append(t)
            if buy_ticks or sell_ticks:
                active_exchanges.append(exchange)
        except Exception:
            continue

    total_ticks = len(all_ticks)
    if total_ticks < _ACCUM_MIN_TOTAL_TICKS:
        return None

    # 标记 side（安全：可能没有 _side 属性时通过其他方式判断）
    for t in all_ticks:
        if not hasattr(t, "_side"):
            # 回退到 TickData 的 side 字段（如果有）
            t._side = getattr(t, "side", "buy")

    total_amount = sum(t.amount for t in all_ticks)
    if total_amount < _ACCUM_MIN_TOTAL_AMOUNT_USD:
        return None

    # Top 5 大单
    sorted_ticks = sorted(all_ticks, key=lambda t: t.amount, reverse=True)
    top5 = sorted_ticks[:5]
    top5_amount = sum(t.amount for t in top5)

    concentration = top5_amount / total_amount if total_amount > 0 else 0
    if concentration > _ACCUM_MAX_TOP5_CONCENTRATION:
        # 高度集中，疑似拆单（单一账户分批）
        return None

    # 计算 top5 买卖金额比例
    top5_buy = sum(t.amount for t in top5 if t._side == "buy")
    top5_buy_ratio = top5_buy / top5_amount if top5_amount > 0 else 0

    # top5 之外的小单（散户成交）
    rest_ticks = sorted_ticks[5:]
    rest_amount = sum(t.amount for t in rest_ticks)
    rest_sell = sum(t.amount for t in rest_ticks if t._side == "sell")
    small_sell_ratio = rest_sell / rest_amount if rest_amount > 0 else 0

    if (
        top5_buy_ratio >= _ACCUM_TOP5_BUY_RATIO_THRESHOLD
        and small_sell_ratio >= _ACCUM_SMALL_SELL_RATIO_THRESHOLD
    ):
        return {
            "pattern": "accumulation",
            "top5_buy_ratio": round(top5_buy_ratio, 3),
            "small_sell_ratio": round(small_sell_ratio, 3),
            "concentration": round(concentration, 3),
            "total_ticks": total_ticks,
            "total_amount_usd": round(total_amount, 0),
            "exchanges": active_exchanges[:3],
        }
    return None


def _scan_single(coin: str) -> ScanResult:
    """扫描单个币种（同步，在线程池中执行）"""
    t0 = time.time()
    try:
        from app.services.data_service import (
            get_header_data,
            get_kline_data_for_period,
            get_trade_volume,
            get_derivatives_agg,
        )
        from app.skills.analysis_skills.quantitative import _parse_kline

        header_data = get_header_data(coin)
        kline_data = get_kline_data_for_period(coin, 2)
        hourly_data = get_kline_data_for_period(coin, 1)
        volume_data = get_trade_volume(coin)
        ohlcv = _parse_kline(kline_data, volume_data)
        if not ohlcv:
            return ScanResult(coin=coin, elapsed=time.time() - t0)

        # 单币种超 30s 直接返回（防止慢请求拖垮整体）
        if time.time() - t0 > 30:
            return ScanResult(coin=coin, error="slow data fetch", elapsed=time.time() - t0)

        entry_ohlcv = _parse_kline(hourly_data, min_bars=15)
        raw_data = {
            "header": header_data,
            "current_price": header_data.get("currentPrice") if isinstance(header_data, dict) else None,
            "entry_ohlcv": entry_ohlcv,
            "kline_periods": {
                "signal": "daily_30d",
                "entry": "hourly_72h" if entry_ohlcv else "daily_30d",
            },
        }
        try:
            derivatives = get_derivatives_agg(coin)
            if derivatives:
                raw_data["derivatives"] = derivatives
        except Exception:
            pass

        # 12小时大单聚合信号（Alpha扫描专用）
        try:
            from app.signals.adaptive_strategy import get_strategy_engine
            engine = get_strategy_engine()
            bo_weight = engine.get_adaptive_weights().get("bigorder_anomaly", 0.35)
            bo_12h = get_bigorder_12h_signal(coin, bo_weight)
            if bo_12h:
                raw_data["bigorder_12h"] = bo_12h
        except Exception:
            pass

        card = fuse_signals(coin, ohlcv, raw_data)
        if not card:
            return ScanResult(coin=coin, elapsed=time.time() - t0)

        bt = None
        try:
            # 只读本地 strategy_state.json（秒返回，避免远程 DB 连接拖慢扫描）
            from app.signals.adaptive_strategy import get_strategy_engine
            local_wr = get_strategy_engine().get_coin_winrate(coin)
            if local_wr and local_wr["sample_count"] >= 3:
                card.win_rate = local_wr["win_rate"]
                card.sample_count = local_wr["sample_count"]
                card.avg_profit_pct = local_wr["avg_profit_pct"]
                bt = local_wr
        except Exception:
            pass

        return ScanResult(coin=coin, signal_card=card, backtest=bt, elapsed=time.time() - t0)

    except Exception as e:
        return ScanResult(coin=coin, error=str(e), elapsed=time.time() - t0)


async def scan_all_coins(
    concurrency: int = 10,
    coins: List[str] = None,
) -> List[ScanResult]:
    """
    全市场并行扫描

    Args:
        concurrency: 并发路数（默认10）
        coins: 指定币种列表，None则动态获取全市场

    Returns:
        所有扫描结果（有信号的排前面，按置信度降序）
    """
    if coins is None:
        coins = get_discovery_coins()

    if not coins:
        return []

    semaphore = asyncio.Semaphore(concurrency)
    loop = asyncio.get_running_loop()
    results: List[ScanResult] = []

    async def _scan_with_limit(coin: str):
        async with semaphore:
            result = await loop.run_in_executor(None, _scan_single, coin)
            results.append(result)

    tasks = [asyncio.create_task(_scan_with_limit(coin)) for coin in coins]
    await asyncio.gather(*tasks, return_exceptions=True)

    # 有信号的排前面，按置信度降序
    results.sort(key=lambda r: (r.signal_card is not None, r.signal_card.confidence if r.signal_card else 0), reverse=True)
    return results


def get_scan_coins() -> List[str]:
    """获取当前扫描币种列表（同步接口）"""
    return get_discovery_coins()
