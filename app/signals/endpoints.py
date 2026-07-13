"""交易信号卡 - API 端点（数学推导 + 自适应策略版）"""
import json
import time
import asyncio
from typing import Optional, List
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.signals.models import SignalCard, SignalStatus, SignalGrade
from app.signals.fusion import fuse_signals, _build_card_event
from app.signals.backtest import backtest_signal, walk_forward_validation
from app.signals.adaptive_strategy import get_strategy_engine
from app.signals.settlement import save_signal_card
from app.signals.review import weekly_review, get_review_summary
from app.signals.alpha_scanner import scan_all_coins, get_scan_coins
from app.services.data_service import (
    get_header_data,
    get_kline_data,
    get_kline_data_for_period,
    get_derivatives_agg,
    get_trade_volume,
    get_discovery_coins,
)
from app.skills.analysis_skills.quantitative import _parse_kline
from app.utils.logger import get_logger
from config.settings import settings

router = APIRouter()
logger = get_logger("app.signals.endpoints")

# 已推送的信号去重缓存 {coin: (direction, grade, timestamp)}
_pushed_signals: dict = {}


@router.get("/generate/{coin}")
async def generate_signal(
    coin: str,
    kline_type: int = Query(2, description="K线类型: 1=小时 2=天 3=周"),
    relaxed: bool = Query(False, description="宽松模式：3 源不一致时仍出 C 级卡（chat 路径默认开启）"),
):
    """为指定币种生成交易信号卡（含数学推导 + 自适应策略）"""
    coin = coin.upper()

    # 获取实时价格 + K 线数据 + 成交量（1h 可选，失败降级为纯日线）
    loop = asyncio.get_running_loop()
    try:
        header_data, kline_data, volume_data = await asyncio.gather(
            loop.run_in_executor(None, get_header_data, coin),
            loop.run_in_executor(None, get_kline_data_for_period, coin, kline_type),
            loop.run_in_executor(None, get_trade_volume, coin),
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"获取K线数据失败: {str(e)}"})

    hourly_data = None
    try:
        hourly_data = await loop.run_in_executor(None, get_kline_data_for_period, coin, 1)
    except Exception as e:
        logger.warning(f"{coin} 1h K线拉取失败，降级为纯日线: {e}")

    min_bars = 15 if kline_type == 1 else 25
    ohlcv = _parse_kline(kline_data, volume_data, min_bars=min_bars)
    if not ohlcv:
        return JSONResponse(status_code=404, content={"error": f"{coin} K线数据不足（至少需要55根）"})

    # 获取衍生品数据
    raw_data = {}
    try:
        derivatives = await loop.run_in_executor(None, get_derivatives_agg, coin)
        if derivatives:
            raw_data["derivatives"] = derivatives
    except Exception:
        pass

    # entry_ohlcv 同时给入场 ATR 和 quantitative 双周期融合用，min_bars=55 满足 ema_triple
    entry_ohlcv = _parse_kline(hourly_data, min_bars=55) if hourly_data else None
    raw_data["header"] = header_data
    raw_data["current_price"] = header_data.get("currentPrice") if isinstance(header_data, dict) else None
    raw_data["entry_ohlcv"] = entry_ohlcv
    raw_data["kline_periods"] = {
        "signal": kline_data.get("periodName") if isinstance(kline_data, dict) else None,
        "entry": "hourly_72h" if entry_ohlcv else kline_data.get("periodName") if isinstance(kline_data, dict) else None,
    }

    # 融合生成信号卡
    signal_card = await asyncio.get_running_loop().run_in_executor(
        None, fuse_signals, coin, ohlcv, raw_data, relaxed, "zh"
    )

    if not signal_card:
        return {
            "coin": coin,
            "status": "no_signal",
            "message": "当前信号不足，未达到生成信号卡的条件（需要至少 2 个信号源方向一致）",
        }

    # 回测历史胜率
    bt_result = await asyncio.get_running_loop().run_in_executor(
        None, backtest_signal, coin, signal_card.direction, signal_card.grade
    )
    if bt_result:
        signal_card.win_rate = bt_result["win_rate"]
        signal_card.sample_count = bt_result["sample_count"]
        signal_card.avg_profit_pct = bt_result["avg_profit_pct"]

    # 持久化到数据库（后验结算用）— 用户询问路径：C 级也存，origin=query 便于策略迭代切片
    if signal_card.math:
        signal_card.math.origin = "query"
    record_id = await asyncio.get_running_loop().run_in_executor(
        None, lambda: save_signal_card(signal_card, force=True)
    )

    return {
        "status": "success",
        "signal": signal_card.model_dump_display(),
        "display": signal_card.format_card(),
        "backtest": bt_result,
        "price_source": "header.currentPrice",
        "kline_periods": raw_data["kline_periods"],
    }


@router.get("/scan")
async def scan_top_coins(
    limit: int = Query(10, le=50, description="返回信号数量上限"),
    refresh: bool = Query(False, description="强制刷新（忽略缓存）"),
):
    """
    扫描全市场信号卡（缓存优先）

    - 缓存 < 30min → 直接返回
    - 缓存 > 30min → 返回旧数据，后台异步刷新
    - refresh=True → 强制重新扫描
    """
    from app.signals.settlement import get_latest_scan, save_scan_batch

    if not refresh:
        cached = await asyncio.get_event_loop().run_in_executor(None, get_latest_scan)
        if cached and not cached["is_stale"]:
            signals = cached["signals"][:limit]
            displays = cached["displays"][:limit]
            return {
                "source": "cache",
                "total_coins_scanned": cached["total_coins"],
                "count": len(signals),
                "signals": signals,
                "display": displays,
                "scan_time": cached["scan_time"],
                "cached_at": cached["cached_at"],
            }

        # 缓存过期，先返回旧数据，后台触发刷新
        if cached:
            signals = cached["signals"][:limit]
            displays = cached["displays"][:limit]

            async def _bg_refresh():
                t0 = time.time()
                results = await scan_all_coins(concurrency=10)
                save_scan_batch(results, time.time() - t0)

            asyncio.create_task(_bg_refresh())

            return {
                "source": "cache_stale",
                "total_coins_scanned": cached["total_coins"],
                "count": len(signals),
                "signals": signals,
                "display": displays,
                "scan_time": cached["scan_time"],
                "cached_at": cached["cached_at"],
                "note": "缓存已过期，后台正在刷新",
            }

    # 无缓存或强制刷新：现场扫描（5分钟超时保护）
    t0 = time.time()
    try:
        results = await asyncio.wait_for(scan_all_coins(concurrency=10), timeout=300)
    except asyncio.TimeoutError:
        return JSONResponse(status_code=504, content={"error": "扫描超时(5min)，请稍后重试"})
    elapsed = time.time() - t0

    # 信号卡写入 signal_card_history（供结算和复盘）
    signal_results = [r for r in results if r.signal_card is not None]
    saved = 0
    for r in signal_results:
        try:
            record_id = save_signal_card(r.signal_card)
            if record_id:
                saved += 1
        except Exception:
            pass

    # 扫描结果写入 scan_cache（30秒超时）
    try:
        await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(None, save_scan_batch, results, elapsed),
            timeout=30,
        )
    except asyncio.TimeoutError:
        pass

    signals = [r for r in results if r.signal_card is not None][:limit]

    return {
        "source": "fresh",
        "total_coins_scanned": len(results),
        "count": len(signals),
        "signals": [s.signal_card.model_dump_display() for s in signals],
        "display": [s.signal_card.format_card() for s in signals],
        "scan_time": round(elapsed, 1),
    }


@router.get("/scan/coins")
async def list_scan_coins():
    """查看当前动态获取的扫描币种列表"""
    coins = get_scan_coins()
    return {
        "count": len(coins),
        "coins": coins,
    }


# ── 策略管理接口 ──────────────────────────────────────────────────────────────

@router.get("/strategy/performance")
async def strategy_performance():
    """
    查看策略性能报告

    返回：策略版本、自适应权重、各因子胜率、市场状态、演化历史
    """
    engine = get_strategy_engine()
    report = engine.get_performance_report()
    return {"status": "success", "data": report}


@router.post("/strategy/evolve")
async def strategy_evolve(coin: str = Query("BTC", description="用于市场状态检测的币种")):
    """
    触发策略演化

    自动检测：
    1. 各因子表现退化/提升
    2. 市场状态变化
    3. 权重优化
    """
    ohlcv = None
    try:
        kline_data = get_kline_data(coin)
        ohlcv = _parse_kline(kline_data)
    except Exception:
        pass

    engine = get_strategy_engine()
    report = engine.evolve(ohlcv)
    return {"status": "success", "data": report}


@router.post("/strategy/record")
async def record_signal_result(
    source_name: str = Query(..., description="信号源名称"),
    pnl_pct: float = Query(..., description="盈亏百分比"),
    direction_correct: bool = Query(..., description="方向是否正确"),
):
    """
    记录信号结算结果，触发贝叶斯权重更新

    在信号被止损/止盈后调用此接口，系统会自动学习并调整权重
    """
    engine = get_strategy_engine()
    engine.record_signal_result(source_name, pnl_pct, direction_correct)
    return {"status": "success", "message": "结果已记录，权重已更新"}


@router.get("/debug/v2/{coin}")
async def debug_v2_signal(coin: str):
    """
    用 v2 新逻辑（时间衰减大单 + 吸筹 pattern）算单币种信号，**仅供调试对比**，
    不写 history，不影响生产。返回新旧两版结果对照。
    """
    def _compute():
        from app.signals.alpha_scanner import (
            get_bigorder_12h_signal, get_bigorder_decay_signal,
            detect_accumulation_pattern,
        )
        from app.signals.fusion import _bigorder_source, _quantitative_source, _technical_source, _extract_realtime_price
        from app.skills.analysis_skills.quantitative import _parse_kline
        from app.skills.analysis_skills.math_derivation import run_math_derivation
        from app.services.data_service import (
            get_header_data, get_kline_data_for_period, get_multi_timeframe_klines, get_trade_volume,
        )

        coin_u = coin.upper()
        header = get_header_data(coin_u)
        timeframes = get_multi_timeframe_klines(coin_u, (1, 2, 3, 4))
        volume_data = get_trade_volume(coin_u)
        daily_data = timeframes.get("daily_60d") or timeframes.get("daily_30d") or {}
        ohlcv = _parse_kline(daily_data, volume_data)
        closes = ohlcv.get("closes", []) if ohlcv else []

        # vol_regime 计算
        vol_regime = "normal"
        if len(closes) >= 40:
            mr = run_math_derivation(closes, direction="long")
            if mr and mr.regime:
                vol_regime = mr.regime.regime

        dual = (vol_regime == "extreme")
        current_price = _extract_realtime_price({"header": header})

        # 三个大单版本同时算
        legacy = _bigorder_source(coin_u, 0.35, "zh")
        legacy_12h = get_bigorder_12h_signal(coin_u, 0.35)
        decay = get_bigorder_decay_signal(coin_u, 0.35, vol_regime, dual_window=dual)
        accumulation = detect_accumulation_pattern(coin_u)

        quant = _quantitative_source(coin_u, 0.35, "zh")
        tech = _technical_source(ohlcv, 0.30, "zh") if ohlcv else None

        def _src_dict(s):
            if not s:
                return None
            return {
                "name": s.name, "score": round(s.score, 1),
                "direction": s.direction.value if hasattr(s.direction, "value") else s.direction,
                "weight": s.weight, "detail": s.detail,
            }

        return {
            "coin": coin_u,
            "current_price": current_price,
            "vol_regime": vol_regime,
            "dual_window_enabled": dual,
            "bigorder_legacy": _src_dict(legacy),
            "bigorder_legacy_12h": _src_dict(legacy_12h),
            "bigorder_v2_decay": _src_dict(decay),
            "accumulation_v2": accumulation,
            "quantitative": _src_dict(quant),
            "technical": _src_dict(tech),
        }

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, _compute)
        return {"status": "success", "data": result}
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "trace": traceback.format_exc().split("\n")[-5:],
        })


@router.get("/backtest/{coin}")
async def detailed_backtest(
    coin: str,
    direction: str = Query("long", description="方向: long/short"),
    grade: str = Query("A", description="等级: S/A/B/C，默认 A"),
    walk_forward: bool = Query(True, description="是否执行Walk-Forward验证"),
):
    """
    详细回测报告

    包含：胜率、夏普比率、索提诺比率、最大回撤、统计显著性、Walk-Forward验证

    数据源：signal_card_history 真实结算结果（同币种+同方向+同等级）
    """
    coin = coin.upper()
    grade = grade.upper()

    # 基础回测
    bt_result = backtest_signal(coin, direction, grade)

    result = {
        "coin": coin,
        "direction": direction,
        "grade": grade,
        "backtest": bt_result,
    }

    # Walk-Forward 验证
    if walk_forward:
        wf_result = walk_forward_validation(coin, direction, grade)
        result["walk_forward"] = wf_result

    return {"status": "success", "data": result}


@router.get("/simple/best")
async def get_best_signal(
    refresh: bool = Query(False, description="强制重新扫描全市场（耗时长，慎用）"),
    mode: str = Query("accuracy", description="selection mode: accuracy=历史胜率优先 / confidence=置信度+等级"),
):
    """返回当前最优的一个币种信号（响应格式同 /simple/{coin}）

    mode=accuracy（默认，推荐）：优先按历史胜率排序
        - trusted (sample≥10 AND win_rate≥40%): 按 win_rate × grade_mult 排序
        - unknown (无足够历史): 回退到 (grade, confidence)
        - bad (win_rate<40%): 永远不优先（赔率负期望）
    mode=confidence: 旧行为，纯按 (grade, confidence) 排序
    """
    from app.signals.settlement import get_latest_scan
    loop = asyncio.get_running_loop()

    signals: list = []
    if not refresh:
        cached = await loop.run_in_executor(None, get_latest_scan)
        if cached and cached.get("signals"):
            signals = list(cached["signals"])

    if not signals:
        try:
            results = await asyncio.wait_for(scan_all_coins(concurrency=10), timeout=300)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=504, content={"error": "扫描超时(5min)，请稍后重试"})
        signals = [r.signal_card.model_dump_display() for r in results if r.signal_card is not None]

    if not signals:
        return {"status": "no_signal", "message": "当前无信号卡数据，请稍后重试"}

    grade_priority = {"S": 3, "A": 2, "B": 1, "C": 0}
    # accuracy 模式下 S 级轻微加权（共振质量 bonus）
    grade_mult = {"S": 1.05, "A": 1.0, "B": 0.95, "C": 0.85}
    MIN_HISTORY = 10    # 至少 10 张历史卡才信胜率
    MIN_WIN_RATE = 40.0 # rr=1:2 平衡线 33.3%，留 6.7% 安全垫

    def _score(s):
        """
        3 档评分：
          bucket 2 (trusted): 有 ≥10 样本 AND win_rate ≥40% → 信历史，按 win_rate×grade_mult 排
          bucket 1 (unknown): 无足够历史 → 回退到 (grade, confidence)
          bucket 0 (bad):    有历史但胜率 <40% → 降级（赔率负期望，永远不优先）
        """
        grade = s.get("grade", "C")
        confidence = float(s.get("confidence") or 0)
        wr = s.get("win_rate")
        sc = int(s.get("sample_count") or 0)
        gp = grade_priority.get(grade, 0)
        if mode == "accuracy" and wr is not None and sc >= MIN_HISTORY:
            try:
                wr_f = float(wr)
                if wr_f >= MIN_WIN_RATE:
                    return (2, wr_f * grade_mult.get(grade, 1.0), sc, gp, confidence)
                # 历史差 → bucket 0（即使 confidence 高也压下去）
                return (0, wr_f, sc, gp, confidence)
            except (TypeError, ValueError):
                pass
        # 无历史回退（mode=confidence 也走这）
        return (1, confidence, gp, sc, 0.0)

    best = max(signals, key=_score)

    # 选卡原因 — 便于前端展示
    wr = best.get("win_rate")
    sc = int(best.get("sample_count") or 0)
    best_bucket = _score(best)[0]
    if mode == "accuracy" and best_bucket == 2:
        reason = f"历史胜率 {wr}%（{sc} 张样本）× grade {best.get('grade')}（trusted）"
    elif mode == "accuracy" and best_bucket == 0:
        reason = f"⚠️ 历史胜率仅 {wr}%，所有候选均不达标（无 trusted / 无 unknown）"
    elif mode == "accuracy":
        reason = f"无足够历史（sample={sc}），回退到 grade+confidence"
    else:
        reason = f"grade {best.get('grade')} + confidence {best.get('confidence')}"

    cp = best.get("current_price")
    try:
        cp = float(cp) if cp is not None else None
    except (ValueError, TypeError):
        pass

    return {
        "status": "success",
        "coin": best.get("coin"),
        "direction": best.get("direction"),
        "confidence": best.get("confidence"),
        "current_price": cp,
        "created_at": best.get("created_at"),
        "win_rate": best.get("win_rate"),
        "sample_count": best.get("sample_count"),
        "selection_reason": reason,
    }


@router.get("/simple/{coin}")
async def generate_simple_signal(
    coin: str,
    kline_type: int = Query(2, description="K线类型: 1=小时 2=天 3=周"),
    relaxed: bool = Query(True, description="宽松模式：3 源不一致时也出信号（默认开启）"),
):
    """轻量信号卡接口 — 只返回 coin/direction/confidence/历史胜率（不落库）"""
    coin = coin.upper()
    loop = asyncio.get_running_loop()

    try:
        header_data, kline_data, volume_data = await asyncio.gather(
            loop.run_in_executor(None, get_header_data, coin),
            loop.run_in_executor(None, get_kline_data_for_period, coin, kline_type),
            loop.run_in_executor(None, get_trade_volume, coin),
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"获取K线数据失败: {str(e)}"})

    hourly_data = None
    try:
        hourly_data = await loop.run_in_executor(None, get_kline_data_for_period, coin, 1)
    except Exception:
        pass

    min_bars = 15 if kline_type == 1 else 25
    ohlcv = _parse_kline(kline_data, volume_data, min_bars=min_bars)
    if not ohlcv:
        return JSONResponse(status_code=404, content={"error": f"{coin} K线数据不足"})

    raw_data: dict = {}
    try:
        derivatives = await loop.run_in_executor(None, get_derivatives_agg, coin)
        if derivatives:
            raw_data["derivatives"] = derivatives
    except Exception:
        pass

    entry_ohlcv = _parse_kline(hourly_data, min_bars=55) if hourly_data else None
    raw_data["header"] = header_data
    raw_data["current_price"] = header_data.get("currentPrice") if isinstance(header_data, dict) else None
    raw_data["entry_ohlcv"] = entry_ohlcv

    signal_card = await loop.run_in_executor(
        None, fuse_signals, coin, ohlcv, raw_data, relaxed, "zh"
    )

    if not signal_card:
        return {
            "status": "no_signal",
            "coin": coin,
            "message": "当前信号不足，未达到生成条件",
        }

    bt = await loop.run_in_executor(
        None, backtest_signal, coin, signal_card.direction, signal_card.grade
    )

    return {
        "status": "success",
        "coin": coin,
        "direction": signal_card.direction.value,
        "confidence": signal_card.confidence,
        "current_price": signal_card.current_price,
        "created_at": signal_card.created_at,
        "win_rate": bt["win_rate"] if bt else None,
        "sample_count": bt["sample_count"] if bt else 0,
    }


# ── SSE 实时推送 ──────────────────────────────────────────────────────────────

@router.get("/stream")
async def signal_card_stream(
    request: Request,
    tier: str = Query("lite", description="会员等级: lite/pro"),
    interval: int = Query(60, description="扫描间隔(秒)", ge=30, le=300),
    min_grade: str = Query("A", description="最低推送等级: S/A/B"),
):
    """
    SSE 实时信号卡推送

    前端建立连接后，后台每隔 interval 秒扫描主流币，
    发现 S/A 级信号立即推送 signal_card 事件。

    Lite 用户：只推送 A 级以上，不含完整数学推导
    Pro 用户：推送所有等级，含完整推导 + 回测 + 策略信息
    """
    # 等级过滤
    grade_priority = {"S": 3, "A": 2, "B": 1}
    min_priority = grade_priority.get(min_grade, 2)

    async def event_generator():
        # 先推送一个心跳，确认连接建立
        yield {
            "event": "message",
            "data": json.dumps({"type": "connected", "tier": tier, "interval": interval}),
        }

        while True:
            if await request.is_disconnected():
                break

            await asyncio.sleep(interval)

            try:
                scan_coins = get_scan_coins()
                for coin in scan_coins:
                    if await request.is_disconnected():
                        break

                    try:
                        header_data, kline_data, hourly_data = await asyncio.gather(
                            asyncio.get_running_loop().run_in_executor(None, get_header_data, coin),
                            asyncio.get_running_loop().run_in_executor(None, get_kline_data_for_period, coin, 2),
                            asyncio.get_running_loop().run_in_executor(None, get_kline_data_for_period, coin, 1),
                        )
                        ohlcv = _parse_kline(kline_data)
                        if not ohlcv:
                            continue

                        entry_ohlcv = _parse_kline(hourly_data, min_bars=15)
                        raw_data = {
                            "header": header_data,
                            "current_price": header_data.get("currentPrice") if isinstance(header_data, dict) else None,
                            "entry_ohlcv": entry_ohlcv,
                        }

                        signal_card = await asyncio.get_running_loop().run_in_executor(
                            None, fuse_signals, coin, ohlcv, raw_data
                        )
                        if not signal_card:
                            continue

                        # 等级过滤
                        sig_priority = grade_priority.get(signal_card.grade.value, 0)
                        if sig_priority < min_priority:
                            continue

                        # 去重：同币种同方向同等级，5分钟内不重复推送
                        sig_key = f"{coin}:{signal_card.direction.value}:{signal_card.grade.value}"
                        now = time.time()
                        if sig_key in _pushed_signals:
                            _, _, last_ts = _pushed_signals[sig_key]
                            if now - last_ts < 300:
                                continue

                        # 回测
                        bt = await asyncio.get_running_loop().run_in_executor(
                            None, backtest_signal, coin, signal_card.direction, signal_card.grade
                        )
                        if bt:
                            signal_card.win_rate = bt["win_rate"]
                            signal_card.sample_count = bt["sample_count"]
                            signal_card.avg_profit_pct = bt["avg_profit_pct"]

                        # 构建事件
                        event_data = _build_card_event(signal_card, bt, tier)

                        # 记录已推送
                        _pushed_signals[sig_key] = (signal_card.direction.value, signal_card.grade.value, now)

                        yield {
                            "event": "signal_card",
                            "data": json.dumps(event_data, ensure_ascii=False),
                        }

                    except Exception:
                        continue

                # 心跳
                yield {
                    "event": "heartbeat",
                    "data": json.dumps({"ts": int(time.time())}),
                }

            except Exception:
                pass

    return EventSourceResponse(event_generator())


# ── generate_card_for_chat 已移至 fusion.py（避免循环导入）───────────────────


# ── 周期复盘接口 ──────────────────────────────────────────────────────────────

@router.get("/review")
async def review_summary():
    """查看信号卡历史表现摘要"""
    summary = get_review_summary()
    return {"status": "success", "data": summary}


@router.post("/review/trigger")
async def trigger_review():
    """
    手动触发周复盘（测试用）

    正常流程中由后台任务每周自动触发
    """
    report = await asyncio.get_running_loop().run_in_executor(None, weekly_review)
    return {"status": "success", "data": report}


@router.post("/settle")
async def trigger_settle():
    """
    手动触发结算（测试用）

    正常流程中由后台任务每10分钟自动执行
    """
    from app.signals.settlement import settle_pending_cards
    result = await asyncio.get_running_loop().run_in_executor(None, settle_pending_cards)
    return {"status": "success", "data": result}


@router.post("/settle/reset")
async def reset_and_settle(
    hours: float = Query(2.0, description="重置该小时数内所有 expired/pending 卡，强制重跑 settle"),
):
    """诊断用：把 N 小时内的卡重置为 pending，立刻调用 settle 重跑

    用于验证 settle 任务是否真的能用 K 线正确处理（修复 bug 后验证）
    """
    from app.signals.settlement import _get_conn, _settle_one_direct, _USE_PROXY
    import pymysql.cursors
    if _USE_PROXY:
        return {"status": "error", "message": "本地代理模式不支持 reset，请在 Railway 调用"}

    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            """UPDATE signal_card_history
               SET status='pending', settled_price=NULL, pnl_pct=NULL, settled_at=NULL
               WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s HOUR)
                 AND status IN ('expired', 'hit_tp', 'hit_sl')""",
            (hours,),
        )
        reset_count = cursor.rowcount
        cursor.close()
        conn.commit()

        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            """SELECT id, coin, direction, stop_loss, take_profit, current_price,
                      confidence, created_at
               FROM signal_card_history
               WHERE status = 'pending' AND created_at <= DATE_SUB(NOW(), INTERVAL 1 HOUR)
               ORDER BY created_at ASC LIMIT 50""",
        )
        cards = cursor.fetchall()
        cursor.close()

        stats = {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0, "skipped": 0}
        samples = []
        for card in cards:
            result = _settle_one_direct(conn, card)
            if result:
                status, pnl, coin = result
                stats[status] = stats.get(status, 0) + 1
                stats["settled"] += 1
                if len(samples) < 5:
                    samples.append({"id": card["id"], "coin": coin, "status": status, "pnl": pnl,
                                    "entry": float(card["current_price"]),
                                    "sl": float(card["stop_loss"]),
                                    "tp": float(card["take_profit"])})
            else:
                stats["skipped"] += 1

        return {"status": "success", "reset_count": reset_count, "stats": stats, "samples": samples}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()
    return {"status": "success", "data": result}


# ── 胜率 & 历史接口 ──────────────────────────────────────────────────────────

@router.get("/winrate")
async def query_winrate(
    coin: Optional[str] = Query(None, description="币种，如 BTC。不传则统计全部"),
    grade: Optional[str] = Query(None, description="等级过滤: S/A/B。不传则统计全部"),
    days: int = Query(30, ge=1, le=365, description="回看天数"),
):
    """
    查询历史胜率

    上线后数据随时间自动累积，越久越准。
    - coin + grade 都不传 → 全局胜率
    - 只传 coin → 该币种胜率
    - 只传 grade → 该等级胜率
    """
    from app.signals.settlement import get_accumulated_winrate
    result = await asyncio.get_running_loop().run_in_executor(
        None, get_accumulated_winrate,
        coin, grade, days,
    )
    if not result:
        return {
            "status": "no_data",
            "message": "暂无历史胜率数据（需上线积累结算记录）",
            "filters": {"coin": coin, "grade": grade, "days": days},
        }
    return {
        "status": "success",
        "filters": {"coin": coin, "grade": grade, "days": days},
        "data": result,
    }


@router.get("/history")
async def query_history(
    coin: Optional[str] = Query(None, description="币种，如 BTC"),
    grade: Optional[str] = Query(None, description="等级: S/A/B"),
    status: Optional[str] = Query(None, description="状态: pending/hit_tp/hit_sl/expired"),
    direction: Optional[str] = Query(None, description="方向: long/short"),
    days: int = Query(7, ge=1, le=90, description="回看天数"),
    limit: int = Query(50, ge=1, le=200, description="返回条数"),
):
    """查询信号卡历史记录（含结算结果，自动选择直连/代理模式）"""
    def _query():
        from app.signals.settlement import _USE_PROXY, _proxy_get, _get_conn
        import pymysql.cursors

        if _USE_PROXY:
            result = _proxy_get("/api/history",
                {"coin": coin, "grade": grade, "status": status, "direction": direction, "days": days, "limit": limit})
            if not result.get("ok"):
                return {"error": result.get("error", "query failed")}
            return {"total": result.get("total", 0), "count": result.get("count", 0), "cards": result.get("cards", [])}

        conn = None
        try:
            conn = _get_conn()
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            where_parts = ["created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"]
            params: list = [days]
            if coin:
                where_parts.append("coin = %s"); params.append(coin.upper())
            if grade:
                where_parts.append("grade = %s"); params.append(grade.upper())
            if status:
                where_parts.append("status = %s"); params.append(status)
            if direction:
                where_parts.append("direction = %s"); params.append(direction)
            where = " AND ".join(where_parts)

            cursor.execute(f"SELECT COUNT(*) as cnt FROM signal_card_history WHERE {where}", params)
            total = cursor.fetchone()["cnt"]

            cursor.execute(
                f"""SELECT id, coin, direction, grade,
                           entry_low, entry_high, stop_loss, take_profit,
                           current_price, confidence, risk_reward_ratio,
                           status, settled_price, pnl_pct, created_at, settled_at
                    FROM signal_card_history WHERE {where}
                    ORDER BY created_at DESC LIMIT %s""",
                params + [limit])
            rows = cursor.fetchall()
            cursor.close()

            cards = []
            for r in rows:
                cards.append({
                    "id": r["id"], "coin": r["coin"], "direction": r["direction"], "grade": r["grade"],
                    "entry_zone": [float(r["entry_low"] or 0), float(r["entry_high"] or 0)],
                    "stop_loss": float(r["stop_loss"] or 0), "take_profit": float(r["take_profit"] or 0),
                    "price": float(r["current_price"] or 0), "confidence": float(r["confidence"] or 0),
                    "risk_reward": float(r["risk_reward_ratio"] or 0),
                    "status": r["status"],
                    "settled_price": float(r["settled_price"] or 0) if r["settled_price"] else None,
                    "pnl_pct": float(r["pnl_pct"] or 0) if r["pnl_pct"] else None,
                    "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else None,
                    "settled_at": r["settled_at"].strftime("%Y-%m-%d %H:%M") if r["settled_at"] else None,
                })
            return {"total": total, "count": len(cards), "cards": cards}
        except Exception as e:
            return {"error": str(e)}
        finally:
            if conn:
                conn.close()

    try:
        result = await asyncio.get_running_loop().run_in_executor(None, _query)
        if "error" in result:
            return JSONResponse(status_code=500, content={"error": result["error"]})
        return {
            "status": "success",
            "filters": {"coin": coin, "grade": grade, "status": status, "direction": direction, "days": days},
            "total": result["total"], "count": result["count"], "cards": result["cards"],
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── 策略评估：多维切片回测 ────────────────────────────────────────────────────

_ALLOWED_GROUP_BY = {
    "direction": "direction",
    "grade": "grade",
    "coin": "coin",
    "strategy_version": "strategy_version",
    "tf_agreement": "JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.tf_agreement'))",
    "regime": "JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.market_regime'))",
    "origin": "JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.origin'))",
}


def _strategy_review_query(
    days: int,
    group_by: Optional[str],
    coin: Optional[str],
    direction: Optional[str],
    grade: Optional[str],
    strategy_version: Optional[int],
    tf_agreement: Optional[str],
) -> dict:
    """聚合查询 signal_card_history，按指定维度切片。直连 MySQL。"""
    from app.signals.settlement import _USE_PROXY, _proxy_get, _get_conn
    import pymysql.cursors

    if _USE_PROXY:
        result = _proxy_get("/api/strategy-review", {
            "days": days, "group_by": group_by,
            "coin": coin, "direction": direction, "grade": grade,
            "strategy_version": strategy_version, "tf_agreement": tf_agreement,
        })
        if not result.get("ok"):
            return {"error": result.get("error", "proxy query failed")}
        return result.get("data", {})

    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        where_parts = [
            "status IN ('hit_tp', 'hit_sl', 'expired')",
            "created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)",
        ]
        params: list = [days]
        if coin:
            where_parts.append("coin = %s"); params.append(coin.upper())
        if direction:
            where_parts.append("direction = %s"); params.append(direction)
        if grade:
            where_parts.append("grade = %s"); params.append(grade.upper())
        if strategy_version:
            where_parts.append("strategy_version = %s"); params.append(strategy_version)
        if tf_agreement:
            where_parts.append("JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.tf_agreement')) = %s")
            params.append(tf_agreement)
        where = " AND ".join(where_parts)

        # totals（不分组）
        cursor.execute(
            f"""SELECT
                   COUNT(*) AS n,
                   SUM(status = 'hit_tp') AS tp,
                   SUM(status = 'hit_sl') AS sl,
                   SUM(status = 'expired') AS exp,
                   SUM(status = 'hit_tp' OR (status = 'expired' AND pnl_pct > 0)) AS wins,
                   COALESCE(AVG(pnl_pct), 0) AS avg_pnl,
                   COALESCE(SUM(pnl_pct), 0) AS sum_pnl
                FROM signal_card_history
                WHERE {where}""",
            params,
        )
        t = cursor.fetchone()
        totals = {
            "sample_count": int(t["n"] or 0),
            "win_rate": round((t["wins"] or 0) / t["n"] * 100, 1) if t["n"] else 0.0,
            "avg_pnl_pct": round(float(t["avg_pnl"] or 0), 2),
            "sum_pnl_pct": round(float(t["sum_pnl"] or 0), 2),
            "hit_tp": int(t["tp"] or 0), "hit_sl": int(t["sl"] or 0), "expired": int(t["exp"] or 0),
        }

        groups = []
        if group_by:
            if group_by not in _ALLOWED_GROUP_BY:
                return {"error": f"invalid group_by: {group_by}"}
            group_expr = _ALLOWED_GROUP_BY[group_by]
            cursor.execute(
                f"""SELECT
                       {group_expr} AS group_key,
                       COUNT(*) AS n,
                       SUM(status = 'hit_tp') AS tp,
                       SUM(status = 'hit_sl') AS sl,
                       SUM(status = 'expired') AS exp,
                       SUM(status = 'hit_tp' OR (status = 'expired' AND pnl_pct > 0)) AS wins,
                       COALESCE(AVG(pnl_pct), 0) AS avg_pnl,
                       COALESCE(SUM(pnl_pct), 0) AS sum_pnl
                    FROM signal_card_history
                    WHERE {where}
                    GROUP BY group_key
                    ORDER BY n DESC""",
                params,
            )
            for r in cursor.fetchall():
                key = r["group_key"]
                if key is None:
                    note = "math_json 缺该字段（历史卡或非 fusion 路径）" if group_by in ("tf_agreement", "regime") else None
                    groups.append({
                        "key": None, "note": note,
                        "sample_count": int(r["n"] or 0),
                        "win_rate": round((r["wins"] or 0) / r["n"] * 100, 1) if r["n"] else 0.0,
                        "avg_pnl_pct": round(float(r["avg_pnl"] or 0), 2),
                        "sum_pnl_pct": round(float(r["sum_pnl"] or 0), 2),
                        "hit_tp": int(r["tp"] or 0), "hit_sl": int(r["sl"] or 0), "expired": int(r["exp"] or 0),
                    })
                else:
                    groups.append({
                        "key": key,
                        "sample_count": int(r["n"] or 0),
                        "win_rate": round((r["wins"] or 0) / r["n"] * 100, 1) if r["n"] else 0.0,
                        "avg_pnl_pct": round(float(r["avg_pnl"] or 0), 2),
                        "sum_pnl_pct": round(float(r["sum_pnl"] or 0), 2),
                        "hit_tp": int(r["tp"] or 0), "hit_sl": int(r["sl"] or 0), "expired": int(r["exp"] or 0),
                    })

        cursor.close()
        return {"totals": totals, "groups": groups}
    except Exception as e:
        logger.exception(f"strategy-review 查询失败: {e}")
        return {"error": str(e)}
    finally:
        if conn:
            conn.close()


@router.get("/strategy-review")
async def strategy_review(
    days: int = Query(14, ge=1, le=90, description="回看天数"),
    group_by: Optional[str] = Query(
        None,
        description="分组维度：direction / grade / coin / strategy_version / tf_agreement / regime",
    ),
    coin: Optional[str] = Query(None, description="过滤：币种，如 BTC"),
    direction: Optional[str] = Query(None, description="过滤：long / short"),
    grade: Optional[str] = Query(None, description="过滤：S / A / B"),
    strategy_version: Optional[int] = Query(None, description="过滤：策略版本（v4=双周期融合）"),
    tf_agreement: Optional[str] = Query(
        None,
        description="过滤：agreement / disagreement / neutral / insufficient_1h_data",
    ),
):
    """
    策略评估：多维切片回测（解决 /winrate 不能按方向/共振切片 + /backtest 样本门槛过高的问题）

    典型用法：
      - 双周期融合 alpha 检验：?days=14&strategy_version=4&group_by=tf_agreement
      - long vs short：?days=14&group_by=direction
      - 新旧策略对比：?days=14&group_by=strategy_version
      - 单币种 grade 分布：?coin=BTC&days=14&group_by=grade
    """
    result = await asyncio.get_running_loop().run_in_executor(
        None, _strategy_review_query,
        days, group_by, coin, direction, grade, strategy_version, tf_agreement,
    )
    if "error" in result:
        return JSONResponse(status_code=500, content={"error": result["error"]})

    return {
        "status": "success",
        "filters": {
            "days": days, "group_by": group_by,
            "coin": coin, "direction": direction, "grade": grade,
            "strategy_version": strategy_version, "tf_agreement": tf_agreement,
        },
        "totals": result["totals"],
        "groups": result["groups"],
    }
