"""BigOrder API 接口"""
import json
import time
import asyncio
from typing import Optional, List
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

import app.bigorder.deps as bigorder_deps
from app.bigorder.models import AnomalySignal, SignalLevel
from config.settings import settings
from app.utils.logger import get_logger

logger = get_logger("app.bigorder.endpoints")

router = APIRouter()


def _get_bigorder_mysql_config():
    """获取 bigorder 专属 MySQL 配置（fallback 到主配置）"""
    from app.signals.settlement import _env_get
    return {
        "host": _env_get("BIGORDER_MYSQL_HOST") or settings.bigorder_mysql_host or settings.mysql_host,
        "port": int(_env_get("BIGORDER_MYSQL_PORT") or 0) or settings.bigorder_mysql_port or settings.mysql_port,
        "user": _env_get("BIGORDER_MYSQL_USER") or settings.bigorder_mysql_user or settings.mysql_user,
        "password": _env_get("BIGORDER_MYSQL_PASSWORD") or settings.bigorder_mysql_password or settings.mysql_password,
        "database": _env_get("BIGORDER_MYSQL_DATABASE") or settings.bigorder_mysql_database or settings.mysql_database,
    }


# ----------------------------------------------------------------
# 1. get_anomaly_list: 获取最新异动列表
# ----------------------------------------------------------------
@router.get("/anomalies")
async def get_anomaly_list(
    exchange: Optional[str] = Query(None, description="过滤交易所"),
    min_score: Optional[int] = Query(None, description="最低分数"),
    limit: int = Query(50, le=200)
):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})
    signals = await asyncio.get_running_loop().run_in_executor(
        None, bigorder_deps.scorer.get_anomaly_list, exchange, min_score, limit
    )
    return {"count": len(signals), "data": signals}


# ----------------------------------------------------------------
# 2. get_coin_signal: 指定币种异动详情
# ----------------------------------------------------------------
@router.get("/coin/{coin}/signal")
async def get_coin_signal(coin: str):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})
    cached = bigorder_deps.scorer.get_coin_signal(coin.upper())
    if cached:
        return cached
    return {"coin": coin.upper(), "message": "暂无数据，请通过 POST /scan 触发扫描或等待后台自动扫描"}


# ----------------------------------------------------------------
# 3. get_order_flow: 资金流向统计
# ----------------------------------------------------------------
@router.get("/coin/{coin}/flow")
async def get_order_flow(
    coin: str,
    window: int = Query(5, description="时间窗口(分钟)", ge=1, le=60)
):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})
    cached = bigorder_deps.scorer.get_order_flow(coin.upper(), window)
    if cached:
        return cached

    def _compute_flow():
        result = {"coin": coin.upper(), "window_minutes": window, "exchanges": {}}
        for exchange in settings.exchanges:
            buy_ticks, sell_ticks = bigorder_deps.consumer.fetch_ticks(exchange, coin.upper(), window * 60)
            if buy_ticks or sell_ticks:
                buy_amount = sum(t.amount for t in buy_ticks)
                sell_amount = sum(t.amount for t in sell_ticks)
                total = buy_amount + sell_amount
                result["exchanges"][exchange] = {
                    "buy_amount": round(buy_amount, 2),
                    "sell_amount": round(sell_amount, 2),
                    "net_flow": round(buy_amount - sell_amount, 2),
                    "buy_count": len(buy_ticks),
                    "sell_count": len(sell_ticks),
                    "buy_ratio": round(buy_amount / total, 4) if total > 0 else 0.5,
                }
        return result

    return await asyncio.get_running_loop().run_in_executor(None, _compute_flow)


# ----------------------------------------------------------------
# 4. get_large_orders: TopN 大单明细
# ----------------------------------------------------------------
@router.get("/coin/{coin}/orders")
async def get_large_orders(
    coin: str,
    top: int = Query(20, le=100),
    exchange: Optional[str] = Query(None)
):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})

    def _get_orders():
        cached = bigorder_deps.scorer.get_large_orders(coin.upper(), top)
        if cached:
            if exchange:
                cached = [o for o in cached if o.get("exchange") == exchange]
            return {"coin": coin.upper(), "count": len(cached), "orders": cached}
        orders = bigorder_deps.consumer.get_top_orders(coin.upper(), exchange=exchange or "Binance", top_n=top)
        return {"coin": coin.upper(), "count": len(orders), "orders": [o.model_dump() for o in orders]}

    return await asyncio.get_running_loop().run_in_executor(None, _get_orders)


# ----------------------------------------------------------------
# 5. search_history: 历史异动记录
# ----------------------------------------------------------------
@router.get("/history")
async def search_history(
    coin: Optional[str] = Query(None),
    days: int = Query(7, le=30),
    level: Optional[str] = Query(None),
    limit: int = Query(100, le=500)
):
    """查询历史异动记录（使用 bigorder 专属 MySQL 配置）"""
    import pymysql
    conn = None
    try:
        mysql_cfg = _get_bigorder_mysql_config()
        conn = pymysql.connect(
            host=mysql_cfg["host"],
            port=mysql_cfg["port"],
            user=mysql_cfg["user"],
            password=mysql_cfg["password"],
            database=mysql_cfg["database"],
            charset="utf8mb4",
            connect_timeout=5,
            read_timeout=10
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        sql = "SELECT * FROM anomaly_history WHERE 1=1"
        params = []

        if coin:
            sql += " AND coin = %s"
            params.append(coin.upper())
        if level:
            sql += " AND level = %s"
            params.append(level)
        if days:
            sql += " AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"
            params.append(days)

        sql += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)

        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return {"count": len(rows), "data": rows}
    except Exception as e:
        return JSONResponse(status_code=500, content={"count": 0, "data": [], "error": str(e)})
    finally:
        if conn:
            conn.close()


# ----------------------------------------------------------------
# 6. get_exchange_compare: 多交易所对比
# ----------------------------------------------------------------
@router.get("/coin/{coin}/compare")
async def get_exchange_compare(coin: str):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})
    return await asyncio.get_running_loop().run_in_executor(
        None, bigorder_deps.scorer.get_exchange_compare, coin.upper()
    )


# ----------------------------------------------------------------
# 手动触发全量扫描
# ----------------------------------------------------------------
@router.post("/scan")
async def manual_scan(coins: Optional[List[str]] = None):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})
    signals = await asyncio.get_running_loop().run_in_executor(
        None, bigorder_deps.scorer.score_all, coins
    )
    enriched = []
    for signal in signals:
        if signal.score.level != SignalLevel.NONE:
            try:
                signal = await bigorder_deps.llm_analyzer.analyze_and_enrich(signal)
            except Exception:
                pass
            enriched.append(signal.model_dump())

    await _save_to_mysql([s for s in signals if s.score.level == SignalLevel.STRONG])

    return {
        "total": len(signals),
        "signals": enriched,
        "strong_count": sum(1 for s in signals if s.score.level == SignalLevel.STRONG),
        "medium_count": sum(1 for s in signals if s.score.level == SignalLevel.MEDIUM),
    }


# ----------------------------------------------------------------
# SSE 实时推送
# ----------------------------------------------------------------
@router.get("/stream")
async def signal_stream(request: Request):
    if not bigorder_deps.is_redis_available():
        return JSONResponse(status_code=503, content={"error": "BigOrder 功能需要 Redis"})
    async def event_generator():
        last_ts = int(time.time() * 1000)
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(5)
            try:
                results = bigorder_deps.consumer.client.zrangebyscore(
                    "signal:anomaly", last_ts, "+inf", withscores=True
                )
                for member, score in results:
                    data = json.loads(member)
                    yield {"event": "signal", "data": json.dumps(data, ensure_ascii=False)}
                if results:
                    last_ts = int(results[-1][1])
            except Exception:
                pass

    return EventSourceResponse(event_generator())


# ----------------------------------------------------------------
# 健康检查
# ----------------------------------------------------------------
@router.get("/health")
async def health():
    if not bigorder_deps.is_redis_available():
        return {
            "status": "disabled",
            "redis": "not configured",
            "watched_coins": [],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
    redis_ok = bigorder_deps.consumer.ping()
    coins = bigorder_deps.consumer.get_watched_coins() if redis_ok else []
    return {
        "status": "healthy" if redis_ok else "degraded",
        "redis": "connected" if redis_ok else "disconnected",
        "watched_coins": coins,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }


# ----------------------------------------------------------------
# MySQL 写入（使用 bigorder 专属 MySQL 配置）
# ----------------------------------------------------------------
async def _save_to_mysql(signals: List[AnomalySignal]):
    """强信号持久化到 MySQL"""
    if not signals:
        return
    import pymysql
    conn = None
    try:
        mysql_cfg = _get_bigorder_mysql_config()
        conn = pymysql.connect(
            host=mysql_cfg["host"],
            port=mysql_cfg["port"],
            user=mysql_cfg["user"],
            password=mysql_cfg["password"],
            database=mysql_cfg["database"],
            charset="utf8mb4",
            connect_timeout=5
        )
        cursor = conn.cursor()
        for s in signals:
            cursor.execute(
                """INSERT INTO anomaly_history
                (coin, exchange, total_score, level, net_flow_score, density_score,
                 ratio_score, price_score, buy_amount, sell_amount, net_flow,
                 price_change_pct, llm_analysis, timestamp)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    s.coin, s.exchange, s.score.total_score, s.score.level.value,
                    s.score.net_flow.score, s.score.density.score,
                    s.score.ratio.score, s.score.price_change.score,
                    s.buy_amount, s.sell_amount, s.net_flow,
                    s.price_change_pct, s.llm_analysis, s.timestamp
                )
            )
        conn.commit()
        cursor.close()
    except Exception as e:
        logger.error(f"MySQL 写入失败: {e}")
    finally:
        if conn:
            conn.close()
