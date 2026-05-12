"""历史基线计算 - 维护均值/标准差"""
import json
import time
import threading
from typing import Dict, Tuple, Optional
from config.settings import settings


class HistoryTracker:
    """
    按币种+交易所维护历史基线。
    使用 Redis HASH 存储，启动时从已有数据初始化。
    """

    HISTORY_KEY = "bigorder:history"  # Redis HASH

    def __init__(self, redis_client):
        self.client = redis_client
        self._lock = threading.Lock()

    def get_baseline(self, exchange: str, coin: str, dimension: str) -> Tuple[float, float]:
        """
        获取历史基线 (mean, std)

        Args:
            dimension: "net_flow" / "density" / "ratio" / "price_change"
        Returns:
            (mean, std)
        """
        field = f"{exchange}:{coin}:{dimension}"
        try:
            raw = self.client.hget(self.HISTORY_KEY, field)
            if raw:
                data = json.loads(raw)
                return data.get("mean", 0.0), data.get("std", 1.0)
        except:
            pass
        return 0.0, 1.0  # 默认 std=1 避免除零

    def update_baseline(self, exchange: str, coin: str, dimension: str, value: float):
        """更新历史基线（滑动窗口）"""
        field = f"{exchange}:{coin}:{dimension}"
        try:
            raw = self.client.hget(self.HISTORY_KEY, field)
            if raw:
                data = json.loads(raw)
                values = data.get("values", [])
                values.append(value)
                # 保留最近 N 个窗口
                max_count = settings.history_window_count
                if len(values) > max_count:
                    values = values[-max_count:]
                mean = sum(values) / len(values)
                variance = sum((v - mean) ** 2 for v in values) / len(values)
                std = variance ** 0.5 if variance > 0 else 1.0
                self.client.hset(self.HISTORY_KEY, field, json.dumps({
                    "mean": mean, "std": std, "count": len(values), "values": values[-50:]
                }))
            else:
                self.client.hset(self.HISTORY_KEY, field, json.dumps({
                    "mean": value, "std": 1.0, "count": 1, "values": [value]
                }))
        except Exception as e:
            print(f"更新基线失败 {field}: {e}")

    def get_all_baselines(self, coin: str) -> Dict[str, Dict[str, Tuple[float, float]]]:
        """获取某币种所有交易所所有维度的基线"""
        result = {}
        try:
            all_data = self.client.hgetall(self.HISTORY_KEY)
            for field, raw in all_data.items():
                parts = field.split(":")
                if len(parts) == 3 and parts[1] == coin:
                    exchange, _, dimension = parts[0], parts[1], parts[2]
                    data = json.loads(raw)
                    if exchange not in result:
                        result[exchange] = {}
                    result[exchange][dimension] = (data.get("mean", 0.0), data.get("std", 1.0))
        except:
            pass
        return result
