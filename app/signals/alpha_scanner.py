"""
全市场 Alpha 扫描引擎 — 动态币种 + 10路并行

架构：
1. 从 discovery API 动态获取全市场币种
2. 10路并发扫描（asyncio.Semaphore 控制）
3. 按置信度排序返回 Top-N 信号卡
"""
from __future__ import annotations

import asyncio
import time
from typing import List, Dict, Any, Optional
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
                "entry": "hourly_24h" if entry_ohlcv else "daily_30d",
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
