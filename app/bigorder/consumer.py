"""Redis 消费器 - 从 ZSET 读取成交数据"""
import json
import time
import re
import redis
from typing import Dict, List, Tuple, Optional
from app.bigorder.models import TickData
from config.settings import get_settings


# 匹配 key 中 _big_deal_ 之后的 {BASE}_{SIDE} 部分
_COIN_PATTERN = re.compile(r"_big_deal_([A-Za-z0-9]+)_(buy|sell)$")


class RedisConsumer:
    """从 Redis ZSET 消费各交易所的成交数据"""

    def __init__(self):
        settings = get_settings()
        self.client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=False
        )

    def _build_key(self, exchange: str, base: str, side: str) -> str:
        """构造 ZSET key: {Exchange}_big_deal_{base}_{side}"""
        return f"{exchange}_big_deal_{base}_{side}"

    def _parse_tick(self, member: str, score: float, side: str, exchange: str) -> Optional[TickData]:
        """解析单条 tick JSON，失败返回 None"""
        try:
            data = json.loads(member)
            tick = TickData(
                symbol=data.get("symbol", ""),
                deal_price=data.get("deal_price", "0"),
                deal_quantity=data.get("deal_quantity", "0"),
                deal_timestamp=int(data.get("deal_timestamp", score)),
                is_maker=data.get("is_maker", False),
                side=side,
                exchange=exchange
            )
            tick.calc_amount()
            return tick
        except (json.JSONDecodeError, ValueError, TypeError, Exception):
            return None

    def fetch_ticks(
        self,
        exchange: str,
        base: str,
        window_seconds: int = 300
    ) -> Tuple[List[TickData], List[TickData]]:
        """
        获取指定时间窗口内的 buy/sell 成交数据

        Returns:
            (buy_ticks, sell_ticks)
        """
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - window_seconds * 1000

        buy_ticks = self._fetch_side(exchange, base, "buy", from_ms, now_ms)
        sell_ticks = self._fetch_side(exchange, base, "sell", from_ms, now_ms)
        return buy_ticks, sell_ticks

    def _fetch_side(
        self,
        exchange: str,
        base: str,
        side: str,
        from_ms: int,
        to_ms: int
    ) -> List[TickData]:
        """从单个 side ZSET 按时间范围读取"""
        key = self._build_key(exchange, base, side)
        try:
            results = self.client.zrangebyscore(key, from_ms, to_ms, withscores=True)
        except Exception as e:
            print(f"读取 {key} 失败: {e}")
            return []

        ticks = []
        for member, score in results:
            tick = self._parse_tick(member, score, side, exchange)
            if tick:
                ticks.append(tick)
        return ticks

    def fetch_all_exchanges(
        self,
        base: str,
        window_seconds: int = 300
    ) -> Dict[str, Tuple[List[TickData], List[TickData]]]:
        """获取所有交易所的成交数据"""
        result = {}
        for exchange in settings.exchanges:
            buy, sell = self.fetch_ticks(exchange, base, window_seconds)
            if buy or sell:
                result[exchange] = (buy, sell)
        return result

    def get_top_orders(
        self,
        base: str,
        exchange: str = "Binance",
        top_n: int = 20,
        side: Optional[str] = None
    ) -> List[TickData]:
        """获取最大金额的 TopN 成交"""
        all_ticks = []
        sides = [side] if side else ["buy", "sell"]
        for s in sides:
            key = self._build_key(exchange, base, s)
            try:
                results = self.client.zrevrange(key, 0, 99, withscores=True)
                for member, score in results:
                    tick = self._parse_tick(member, score, s, exchange)
                    if tick:
                        all_ticks.append(tick)
            except Exception:
                continue

        all_ticks.sort(key=lambda t: t.amount, reverse=True)
        return all_ticks[:top_n]

    def get_watched_coins(self) -> List[str]:
        """扫描 Redis 获取当前有数据的币种列表"""
        coins = set()
        for exchange in settings.exchanges:
            pattern = self._build_key(exchange, "*", "buy")
            try:
                keys = self.client.keys(pattern)
                for key in keys:
                    match = _COIN_PATTERN.search(key)
                    if match:
                        coins.add(match.group(1))
            except Exception:
                continue
        return sorted(coins)

    def fetch_all_exchanges_pipeline(
        self,
        base: str,
        window_seconds: int = 300
    ) -> Dict[str, Tuple[List[TickData], List[TickData]]]:
        """用 pipeline 批量获取所有交易所数据（减少 RTT）"""
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - window_seconds * 1000

        pipe = self.client.pipeline()
        keys_map = []
        for exchange in settings.exchanges:
            for side in ("buy", "sell"):
                key = self._build_key(exchange, base, side)
                pipe.zrangebyscore(key, from_ms, now_ms, withscores=True)
                keys_map.append((exchange, side, key))

        try:
            results = pipe.execute()
        except Exception as e:
            print(f"pipeline 读取失败: {e}")
            return {}

        grouped: Dict[str, Tuple[List[TickData], List[TickData]]] = {}
        for i, (exchange, side, key) in enumerate(keys_map):
            ticks = []
            for member, score in results[i]:
                tick = self._parse_tick(member, score, side, exchange)
                if tick:
                    ticks.append(tick)

            if exchange not in grouped:
                grouped[exchange] = ([], [])
            if side == "buy":
                grouped[exchange][0].extend(ticks)
            else:
                grouped[exchange][1].extend(ticks)

        return {k: v for k, v in grouped.items() if v[0] or v[1]}

    def ping(self) -> bool:
        """检查 Redis 连接"""
        try:
            return self.client.ping()
        except Exception:
            return False
