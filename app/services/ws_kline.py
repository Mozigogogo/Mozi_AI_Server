"""主站 WebSocket K线/行情旁路刷新

订阅 wss://{主站}/ws 的 kline 频道（1h + 1d，discovery top N 币种，limit=1 最小负载），
收到 500ms 综合推送即失效 data_service 的对应 K 线与行情头缓存（5s 节流），
让信号卡扫描 / bigorder 评分 / 问答拿到 ≤5s 新鲜度。

设计：
- 旁路失效：只清缓存不改取数路径，WS 挂掉自动回退原有 30s TTL 轮询，零影响
- 失效节流：同一 (币种, 数据) 最短 5s 失效一次，失效速率 ≤ 请求速率，幂等不打爆源站
- 断线重连：外层循环 + 延迟重连；币种列表每小时刷新（重连时带新订阅）
- limit=1：推送里的 hisKlineData 只作初始化用，agent 只要推送当失效触发器
"""
import asyncio
import json
import time
from typing import Dict, List, Optional

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger("app.services.ws_kline")
settings = get_settings()

_RECONNECT_DELAY = 10            # 断线重连间隔（秒）
_SYMBOL_REFRESH_INTERVAL = 3600  # 币种列表刷新间隔（秒）
_SYMBOLS_PER_CHANNEL = 50        # 单订阅频道币种数（分块防单帧过大）
_INVALIDATE_MIN_GAP = 5.0        # 同一 (币种, 数据) 失效节流（秒）
_HEARTBEAT_INTERVAL = 300        # 存活日志间隔（秒）

# WS period → data_service kline_type（15m/30m/4h 无对应 REST 缓存，跳过）
_PERIOD_KLINE_TYPE = {"1h": 1, "1d": 2, "1w": 3, "1M": 4}

_last_invalidate: Dict[str, float] = {}
_stats = {"msgs": 0, "invalidations": 0, "started_at": 0.0}


def _invalidate(url: str) -> None:
    from app.services.data_service import invalidate_cache
    invalidate_cache(url)


def _on_push(raw: str) -> None:
    """处理服务端推送。kline 综合包（500ms，无 channelId）→ 失效对应 K线+行情头缓存。"""
    try:
        msg = json.loads(raw)
        if msg.get("event") != "kline":
            return
        data = msg.get("data") or {}
        symbol = (data.get("headerData") or {}).get("symbol")
        if not symbol:
            return
        _stats["msgs"] += 1

        period = ((data.get("klineData") or {}).get("realKlineData") or {}).get("period")
        kline_type = _PERIOD_KLINE_TYPE.get(period)
        if kline_type is not None:
            _throttled_invalidate(
                f"{settings.kline_api_base}/detail/kline?symbol={symbol}&type={kline_type}",
                f"{symbol}:k{kline_type}")
        # 行情头（现价/24h 涨跌幅）同帧刷新
        _throttled_invalidate(
            f"{settings.kline_api_base}/detail/header?symbol={symbol}",
            f"{symbol}:hdr")

    except Exception:
        pass


def _throttled_invalidate(url: str, throttle_key: str) -> None:
    """5s 节流的缓存失效（K线与行情头各自独立节流）"""
    now = time.monotonic()
    if now - _last_invalidate.get(throttle_key, 0.0) < _INVALIDATE_MIN_GAP:
        return
    _last_invalidate[throttle_key] = now
    _invalidate(url)
    _stats["invalidations"] += 1


def _build_channels(symbols: List[str]) -> List[dict]:
    """每个 (周期, 币种块) 一个频道条目；limit=1 最小化 hisKlineData 负载"""
    channels = []
    for period in ("1h", "1d"):
        for i in range(0, len(symbols), _SYMBOLS_PER_CHANNEL):
            chunk = symbols[i:i + _SYMBOLS_PER_CHANNEL]
            channels.append({
                "type": "kline",
                "symbols": chunk,
                "params": {"period": period, "limit": 1},
            })
    return channels


async def _refresh_symbols(max_symbols: int) -> List[str]:
    from app.services.data_service import get_discovery_coins
    coins = await asyncio.to_thread(get_discovery_coins)
    return coins[:max_symbols]


class WsKlineManager:
    """单实例管理器；main.py lifespan 里 create_task(ws_kline_manager.run())"""

    def __init__(self):
        self._stop = False
        self._symbols: List[str] = []
        self._last_refresh = 0.0

    async def run(self):
        import websockets

        _stats["started_at"] = time.time()
        logger.info(f"ws_kline 启动: {settings.ws_kline_base}/ws max_symbols={settings.ws_kline_max_symbols}")
        while not self._stop:
            conn = None
            try:
                if time.time() - self._last_refresh > _SYMBOL_REFRESH_INTERVAL or not self._symbols:
                    self._symbols = await _refresh_symbols(settings.ws_kline_max_symbols)
                    self._last_refresh = time.time()
                    if not self._symbols:
                        logger.warning("ws_kline: discovery 币种列表为空，稍后重试")
                        await asyncio.sleep(_RECONNECT_DELAY)
                        continue
                    logger.info(f"ws_kline: 订阅 {len(self._symbols)} 币种（1h+1d）")

                conn = await websockets.connect(
                    f"{settings.ws_kline_base}/ws", ping_interval=20, close_timeout=5)
                await self._subscribe(conn)

                heartbeat_task = asyncio.create_task(self._heartbeat())
                try:
                    async for raw in conn:
                        _on_push(raw)
                finally:
                    heartbeat_task.cancel()
            except asyncio.CancelledError:
                await self._close(conn)
                logger.info("ws_kline: 已停止")
                return
            except Exception as e:
                logger.warning(f"ws_kline 连接异常: {type(e).__name__}: {e}，{_RECONNECT_DELAY}s 后重连")
            await self._close(conn)
            await asyncio.sleep(_RECONNECT_DELAY)

    async def _subscribe(self, conn):
        """发送订阅帧并核对响应；limit=1 被拒则去掉 limit 只留 period 重试一次"""
        channels = _build_channels(self._symbols)
        variants = (
            channels,
            [{**ch, "params": {"period": ch["params"]["period"]}} for ch in channels],
        )
        for attempt, chans in enumerate(variants):
            req_id = f"agent-{int(time.time())}"
            frame = json.dumps({
                "event": "subscribe",
                "requestId": req_id,
                "data": {"channels": chans},
            })
            await conn.send(frame)
            resp = json.loads(await asyncio.wait_for(conn.recv(), timeout=15))
            if resp.get("event") != "subscribe_response":
                raise RuntimeError(f"订阅响应异常: {str(resp)[:200]}")
            ok = resp.get("data", {}).get("channels") or []
            failed = resp.get("data", {}).get("failedChannels") or []
            if not failed:
                logger.info(f"ws_kline: {len(ok)} 个频道订阅成功")
                return
            logger.warning(f"ws_kline: {len(failed)} 个频道订阅失败: {str(failed)[:200]}")
            if attempt == 0:
                logger.info("ws_kline: 去掉 limit 参数重试订阅")

    @staticmethod
    async def _close(conn):
        if conn is None:
            return
        try:
            await conn.close()
        except Exception:
            pass

    async def _heartbeat(self):
        """存活与流量观测日志"""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            uptime = int(time.time() - _stats["started_at"])
            logger.info(
                f"ws_kline alive: uptime={uptime}s symbols={len(self._symbols)} "
                f"msgs={_stats['msgs']} invalidations={_stats['invalidations']}"
            )


ws_kline_manager = WsKlineManager()
