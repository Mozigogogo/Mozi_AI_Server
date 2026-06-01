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
from app.services.data_service import get_kline_data, get_derivatives_agg, get_trade_volume
from app.skills.analysis_skills.quantitative import _parse_kline
from config.settings import settings

router = APIRouter()

# 推送扫描用的主流币列表
SCAN_COINS = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX",
    "DOT", "LINK", "UNI", "ATOM", "LTC", "NEAR", "APT", "ARB",
    "OP", "MATIC", "FIL", "TON",
]

# 已推送的信号去重缓存 {coin: (direction, grade, timestamp)}
_pushed_signals: dict = {}


@router.get("/generate/{coin}")
async def generate_signal(
    coin: str,
    kline_type: int = Query(2, description="K线类型: 1=小时 2=天 3=周"),
):
    """为指定币种生成交易信号卡（含数学推导 + 自适应策略）"""
    coin = coin.upper()

    # 获取 K 线数据 + 成交量
    try:
        kline_data, volume_data = await asyncio.gather(
            asyncio.get_running_loop().run_in_executor(None, get_kline_data, coin, kline_type),
            asyncio.get_running_loop().run_in_executor(None, get_trade_volume, coin),
        )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"获取K线数据失败: {str(e)}"})

    ohlcv = _parse_kline(kline_data, volume_data)
    if not ohlcv:
        return JSONResponse(status_code=404, content={"error": f"{coin} K线数据不足（至少需要55根）"})

    # 获取衍生品数据
    raw_data = {}
    try:
        derivatives = await asyncio.get_running_loop().run_in_executor(
            None, get_derivatives_agg, coin
        )
        if derivatives:
            raw_data["derivatives"] = derivatives
    except Exception:
        pass

    # 融合生成信号卡
    signal_card = await asyncio.get_running_loop().run_in_executor(
        None, fuse_signals, coin, ohlcv, raw_data
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

    # 持久化到数据库（后验结算用）
    record_id = await asyncio.get_running_loop().run_in_executor(
        None, save_signal_card, signal_card
    )

    return {
        "status": "success",
        "signal": signal_card.model_dump(),
        "display": signal_card.format_card(),
        "backtest": bt_result,
    }


@router.get("/scan")
async def scan_top_coins(
    limit: int = Query(10, le=50, description="扫描币种数量"),
):
    """扫描热门币种，返回所有符合条件的信号卡"""
    major_coins = [
        "BTC", "ETH", "BNB", "SOL", "XRP", "DOGE", "ADA", "AVAX",
        "DOT", "LINK", "UNI", "ATOM", "LTC", "NEAR", "APT", "ARB",
        "OP", "MATIC", "FIL", "TON",
    ][:limit]

    results = []

    for coin in major_coins:
        try:
            kline_data = get_kline_data(coin)
            ohlcv = _parse_kline(kline_data)
            if not ohlcv:
                continue

            signal_card = fuse_signals(coin, ohlcv, {})
            if signal_card:
                bt = backtest_signal(coin, signal_card.direction, signal_card.grade)
                if bt:
                    signal_card.win_rate = bt["win_rate"]
                    signal_card.sample_count = bt["sample_count"]
                    signal_card.avg_profit_pct = bt["avg_profit_pct"]
                results.append(signal_card)

        except Exception:
            continue

    results.sort(key=lambda s: s.confidence, reverse=True)

    return {
        "count": len(results),
        "signals": [s.model_dump() for s in results],
        "display": [s.format_card() for s in results],
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


@router.get("/backtest/{coin}")
async def detailed_backtest(
    coin: str,
    direction: str = Query("long", description="方向: long/short"),
    walk_forward: bool = Query(True, description="是否执行Walk-Forward验证"),
):
    """
    详细回测报告

    包含：胜率、夏普比率、索提诺比率、最大回撤、统计显著性、Walk-Forward验证
    """
    coin = coin.upper()

    # 基础回测
    bt_result = backtest_signal(coin, direction, "A")

    result = {
        "coin": coin,
        "direction": direction,
        "backtest": bt_result,
    }

    # Walk-Forward 验证
    if walk_forward:
        wf_result = walk_forward_validation(coin, direction)
        result["walk_forward"] = wf_result

    return {"status": "success", "data": result}


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
                for coin in SCAN_COINS:
                    if await request.is_disconnected():
                        break

                    try:
                        kline_data = await asyncio.get_running_loop().run_in_executor(
                            None, get_kline_data, coin, 2
                        )
                        ohlcv = _parse_kline(kline_data)
                        if not ohlcv:
                            continue

                        signal_card = await asyncio.get_running_loop().run_in_executor(
                            None, fuse_signals, coin, ohlcv, {}
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
