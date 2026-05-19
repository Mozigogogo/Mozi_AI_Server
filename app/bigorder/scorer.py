"""四维打分引擎 - 异动信号检测与存储"""
import json
import time
import math
from typing import Dict, List, Tuple, Optional
from datetime import datetime

from app.bigorder.models import (
    TickData, DimensionScore, SignalScore,
    AnomalySignal, SignalLevel, OrderFlowStats, ExchangeCompare
)
from config.settings import settings


class AnomalyScorer:
    """四维打分 + 信号生成 + 结果写入 Redis"""

    def __init__(self, consumer, history_tracker):
        self.consumer = consumer
        self.history = history_tracker
        self.redis = consumer.client

    # ================================================================
    # 四维计算
    # ================================================================

    def calc_net_flow(self, buy_ticks: List[TickData], sell_ticks: List[TickData]) -> float:
        """净资金流向 = buy成交额 - sell成交额"""
        buy_amount = sum(t.amount for t in buy_ticks)
        sell_amount = sum(t.amount for t in sell_ticks)
        return buy_amount - sell_amount

    def calc_density(self, ticks: List[TickData]) -> int:
        """大单密度 = 5min 内成交条数"""
        return len(ticks)

    def calc_ratio(self, buy_ticks: List[TickData], sell_ticks: List[TickData]) -> float:
        """买卖比 = buy_vol / (buy_vol + sell_vol)"""
        buy_vol = sum(t.amount for t in buy_ticks)
        sell_vol = sum(t.amount for t in sell_ticks)
        total = buy_vol + sell_vol
        return buy_vol / total if total > 0 else 0.5

    def calc_price_change(self, ticks: List[TickData]) -> Tuple[float, float, float]:
        """价格变化率 = (latest - earliest) / earliest * 100"""
        if not ticks:
            return 0.0, 0.0, 0.0
        sorted_ticks = sorted(ticks, key=lambda t: t.deal_timestamp)
        try:
            first_price = float(sorted_ticks[0].deal_price)
            last_price = float(sorted_ticks[-1].deal_price)
        except (ValueError, TypeError):
            return 0.0, 0.0, 0.0
        change_pct = ((last_price - first_price) / first_price) if first_price > 0 else 0.0
        return change_pct, first_price, last_price

    # ================================================================
    # 打分逻辑
    # ================================================================

    def _score_sigma(self, value: float, mean: float, std: float, sigma_threshold: float) -> float:
        """
        基于 sigma 偏离度打分 (0~100)
        value 超过 mean + sigma_threshold * std -> 满分
        """
        if std <= 0:
            return 50.0 if abs(value) > 0 else 0.0
        deviation = abs(value - mean) / std
        score = min(100.0, (deviation / (sigma_threshold * 2)) * 100)
        if abs(value - mean) >= sigma_threshold * std:
            score = max(score, 60.0)
        return round(score, 1)

    def _score_ratio(self, ratio: float) -> float:
        """买卖比打分：偏离 0.5 越远分数越高"""
        deviation = abs(ratio - 0.5)
        threshold = max(settings.ratio_upper - 0.5, 0.5 - settings.ratio_lower)
        if deviation < threshold:
            return 0.0
        score = min(100.0, 60.0 + (deviation - threshold) / threshold * 40)
        return round(score, 1)

    def _score_price(self, change_pct: float) -> float:
        """价格变化打分"""
        abs_change = abs(change_pct)
        if abs_change < settings.price_change_pct:
            return 0.0
        score = min(100.0, 60.0 + (abs_change - settings.price_change_pct) / settings.price_change_pct * 40)
        return round(score, 1)

    # ================================================================
    # 综合评分
    # ================================================================

    def score_exchange(self, exchange: str, coin: str) -> Optional[AnomalySignal]:
        """对单个交易所的单个币种进行四维打分"""
        buy_ticks, sell_ticks = self.consumer.fetch_ticks(
            exchange, coin, settings.flow_window_seconds
        )
        all_ticks = buy_ticks + sell_ticks
        if not all_ticks:
            return None

        net_flow = self.calc_net_flow(buy_ticks, sell_ticks)
        density = self.calc_density(all_ticks)
        ratio = self.calc_ratio(buy_ticks, sell_ticks)
        price_change, price_start, price_end = self.calc_price_change(all_ticks)

        nf_mean, nf_std = self.history.get_baseline(exchange, coin, "net_flow")
        den_mean, den_std = self.history.get_baseline(exchange, coin, "density")

        nf_score = self._score_sigma(net_flow, nf_mean, nf_std, settings.sigma_net_flow)
        den_score = self._score_sigma(density, den_mean, den_std, settings.sigma_density)
        ratio_score = self._score_ratio(ratio)
        price_score = self._score_price(price_change)

        total = (
            nf_score * settings.weight_net_flow +
            den_score * settings.weight_density +
            ratio_score * settings.weight_ratio +
            price_score * settings.weight_price
        )
        total = round(total, 1)

        if total >= settings.score_threshold_strong:
            level = SignalLevel.STRONG
        elif total >= settings.score_threshold_medium:
            level = SignalLevel.MEDIUM
        else:
            level = SignalLevel.NONE

        self.history.update_baseline(exchange, coin, "net_flow", net_flow)
        self.history.update_baseline(exchange, coin, "density", density)

        buy_amount = sum(t.amount for t in buy_ticks)
        sell_amount = sum(t.amount for t in sell_ticks)

        now_ms = int(time.time() * 1000)
        signal = AnomalySignal(
            coin=coin,
            exchange=exchange,
            score=SignalScore(
                net_flow=DimensionScore(raw_value=net_flow, history_mean=nf_mean, history_std=nf_std, score=nf_score),
                density=DimensionScore(raw_value=density, history_mean=den_mean, history_std=den_std, score=den_score),
                ratio=DimensionScore(raw_value=ratio, history_mean=0.5, history_std=0.1, score=ratio_score),
                price_change=DimensionScore(raw_value=price_change * 100, history_mean=0, history_std=0, score=price_score),
                total_score=total,
                level=level
            ),
            buy_amount=round(buy_amount, 2),
            sell_amount=round(sell_amount, 2),
            net_flow=round(net_flow, 2),
            buy_count=len(buy_ticks),
            sell_count=len(sell_ticks),
            price_start=price_start,
            price_end=price_end,
            price_change_pct=round(price_change * 100, 2),
            top_orders=sorted(all_ticks, key=lambda t: t.amount, reverse=True)[:5],
            timestamp=now_ms,
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

        if level != SignalLevel.NONE:
            self._save_signal(signal)

        return signal

    def score_all(self, coins: List[str] = None) -> List[AnomalySignal]:
        """全量扫描所有币种"""
        if not coins:
            coins = self.consumer.get_watched_coins()
        signals = []
        for coin in coins:
            for exchange in settings.exchanges:
                try:
                    signal = self.score_exchange(exchange, coin)
                    if signal and signal.score.level != SignalLevel.NONE:
                        signals.append(signal)
                except Exception as e:
                    print(f"打分失败 {exchange}/{coin}: {e}")
        signals.sort(key=lambda s: s.score.total_score, reverse=True)
        return signals

    # ================================================================
    # Redis 存储
    # ================================================================

    def _save_signal(self, signal: AnomalySignal):
        """将信号写入 Redis"""
        now_ms = int(time.time() * 1000)
        data = signal.model_dump()
        data.pop("top_orders", None)
        data.pop("llm_analysis", None)

        self.redis.zadd(
            "signal:anomaly",
            {json.dumps(data, ensure_ascii=False): now_ms}
        )
        self.redis.zremrangebyrank("signal:anomaly", 0, -(1000 + 1))

        coin_key = f"signal:coin:{signal.coin}"
        pipe = self.redis.pipeline()
        pipe.hset(coin_key, "score", json.dumps(data["score"], ensure_ascii=False))
        pipe.hset(coin_key, "exchange", str(signal.exchange))
        pipe.hset(coin_key, "buy_amount", str(signal.buy_amount))
        pipe.hset(coin_key, "sell_amount", str(signal.sell_amount))
        pipe.hset(coin_key, "net_flow", str(signal.net_flow))
        pipe.hset(coin_key, "buy_count", str(signal.buy_count))
        pipe.hset(coin_key, "sell_count", str(signal.sell_count))
        pipe.hset(coin_key, "price_change_pct", str(signal.price_change_pct))
        pipe.hset(coin_key, "total_score", str(signal.score.total_score))
        pipe.hset(coin_key, "level", str(signal.score.level.value))
        pipe.hset(coin_key, "timestamp", str(now_ms))
        pipe.hset(coin_key, "created_at", str(signal.created_at))
        pipe.execute()

        window = settings.flow_window_seconds // 60
        stats_key = f"stats:{signal.coin}:{window}"
        buy_total = signal.buy_amount + signal.sell_amount
        pipe = self.redis.pipeline()
        pipe.hset(stats_key, "buy_amount", str(signal.buy_amount))
        pipe.hset(stats_key, "sell_amount", str(signal.sell_amount))
        pipe.hset(stats_key, "net_flow", str(signal.net_flow))
        pipe.hset(stats_key, "buy_count", str(signal.buy_count))
        pipe.hset(stats_key, "sell_count", str(signal.sell_count))
        pipe.hset(stats_key, "buy_ratio", str(round(signal.buy_amount / buy_total, 4) if buy_total > 0 else 0.5))
        pipe.hset(stats_key, "updated_at", str(now_ms))
        pipe.execute()

        orders_key = f"orders:large:{signal.coin}"
        for tick in signal.top_orders:
            tick_data = tick.model_dump()
            tick_data.pop("amount", None)
            self.redis.zadd(
                orders_key,
                {json.dumps(tick_data, ensure_ascii=False): tick.amount}
            )
        self.redis.zremrangebyrank(orders_key, 0, -(100 + 1))

    # ================================================================
    # 查询方法
    # ================================================================

    def get_anomaly_list(
        self,
        exchange: Optional[str] = None,
        min_score: Optional[int] = None,
        limit: int = 50
    ) -> List[dict]:
        """获取最新异动列表"""
        try:
            results = self.redis.zrevrange("signal:anomaly", 0, limit - 1, withscores=True)
        except Exception:
            return []

        signals = []
        for member, score in results:
            try:
                data = json.loads(member)
                if exchange and data.get("exchange") != exchange:
                    continue
                if min_score and data.get("score", {}).get("total_score", 0) < min_score:
                    continue
                signals.append(data)
            except Exception:
                continue
        return signals

    def get_coin_signal(self, coin: str) -> Optional[dict]:
        """获取币种异动详情"""
        try:
            data = self.redis.hgetall(f"signal:coin:{coin}")
            if data:
                data["coin"] = coin
                if "score" in data:
                    data["score"] = json.loads(data["score"])
                return data
        except Exception:
            pass
        return None

    def get_order_flow(self, coin: str, window_minutes: int = 5) -> Optional[dict]:
        """获取资金流向统计"""
        try:
            data = self.redis.hgetall(f"stats:{coin}:{window_minutes}")
            if data:
                data["coin"] = coin
                data["window_minutes"] = window_minutes
                for k in ("buy_amount", "sell_amount", "net_flow", "buy_ratio"):
                    if k in data:
                        data[k] = float(data[k])
                for k in ("buy_count", "sell_count", "updated_at"):
                    if k in data:
                        data[k] = int(data[k])
                return data
        except Exception:
            pass
        return None

    def get_large_orders(self, coin: str, top_n: int = 20) -> List[dict]:
        """获取 TopN 大单"""
        try:
            results = self.redis.zrevrange(f"orders:large:{coin}", 0, top_n - 1, withscores=True)
            orders = []
            for member, score in results:
                try:
                    data = json.loads(member)
                    data["amount"] = score
                    orders.append(data)
                except Exception:
                    continue
            return orders
        except Exception:
            return []

    def get_exchange_compare(self, coin: str) -> dict:
        """对比同一币种在不同交易所的买卖分布（pipeline 批量查询）"""
        result = {"coin": coin, "exchanges": {}}
        all_data = self.consumer.fetch_all_exchanges_pipeline(coin, settings.flow_window_seconds)
        for exchange, (buy_ticks, sell_ticks) in all_data.items():
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
