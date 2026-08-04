"""
Regime 漂移监控（Phase 6）— 自动检测市场结构变化。

设计原则：
- 每周跑：比较近 30d vs 90d vs 180d 的 regime 分布
- 漂移 > 20% → 报警（提示 guardrail 可能需要重审）
- fail-open：任何异常都不阻塞主流程

regime 数据来源：
- signal_card_history 表的 math_json -> market_regime 字段（已落库）
- 或者重算最近 K 线（成本高，不推荐）

输出格式：
{
  "regimes_30d": {"trending_up": 0.45, "trending_down": 0.10, ...},
  "regimes_90d": {...},
  "regimes_180d": {...},
  "drift_30_vs_90": 0.32,  # 总变差距离 (TV distance)
  "drift_30_vs_180": 0.45,
  "alert": true,  # drift > 0.2
  "drifted_regimes": ["trending_up_extended", ...]
}
"""
import json
import os
from collections import Counter
from datetime import datetime
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

DRIFT_ALERT_THRESHOLD = float(os.getenv("REGIME_DRIFT_THRESHOLD", "0.20"))


def _get_conn():
    from app.signals.settlement import _get_conn
    return _get_conn()


def _fetch_regimes(days: int) -> Counter:
    """从 signal_card_history 拉 N 天内的 regime 分布。"""
    try:
        import pymysql.cursors
        conn = _get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT
                    JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.market_regime')) AS regime
                FROM signal_card_history
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                  AND math_json LIKE '%%market_regime%%'
                """,
                (days,),
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"regime_drift 查询失败 (days={days}): {type(e).__name__}: {e}")
        return Counter()

    c = Counter()
    for r in rows:
        regime = r.get("regime") or "unknown"
        c[regime] += 1
    return c


def _to_distribution(c: Counter) -> Dict[str, float]:
    """Counter → 概率分布字典（和为 1）。"""
    total = sum(c.values())
    if total == 0:
        return {}
    return {k: round(v / total, 3) for k, v in c.items()}


def _total_variation(d1: Dict[str, float], d2: Dict[str, float]) -> float:
    """两分布的总变差距离（0-1，越大越不像）。"""
    keys = set(d1.keys()) | set(d2.keys())
    return round(sum(abs(d1.get(k, 0) - d2.get(k, 0)) for k in keys) / 2, 3)


def detect_drift() -> Dict[str, Any]:
    """
    检测近 30d 与 90d / 180d 的 regime 分布漂移。
    """
    c30 = _fetch_regimes(days=30)
    c90 = _fetch_regimes(days=90)
    c180 = _fetch_regimes(days=180)

    d30 = _to_distribution(c30)
    d90 = _to_distribution(c90)
    d180 = _to_distribution(c180)

    drift_30_vs_90 = _total_variation(d30, d90)
    drift_30_vs_180 = _total_variation(d30, d180)

    alert = drift_30_vs_90 > DRIFT_ALERT_THRESHOLD or drift_30_vs_180 > DRIFT_ALERT_THRESHOLD

    # 找漂移最大的 regime
    drifted = []
    for regime in set(d30.keys()) | set(d90.keys()):
        delta = d30.get(regime, 0) - d90.get(regime, 0)
        if abs(delta) >= 0.10:
            drifted.append({"regime": regime, "delta_30_vs_90": round(delta, 3)})
    drifted.sort(key=lambda x: abs(x["delta_30_vs_90"]), reverse=True)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "counts": {"n_30d": sum(c30.values()), "n_90d": sum(c90.values()), "n_180d": sum(c180.values())},
        "regimes_30d": d30,
        "regimes_90d": d90,
        "regimes_180d": d180,
        "drift_30_vs_90": drift_30_vs_90,
        "drift_30_vs_180": drift_30_vs_180,
        "alert": alert,
        "drifted_regimes": drifted[:5],
        "threshold": DRIFT_ALERT_THRESHOLD,
    }


def run_weekly_check() -> None:
    """周任务入口。漂移报警写日志。"""
    try:
        r = detect_drift()
        if r["alert"]:
            logger.warning(
                f"[RegimeDrift][ALERT] drift_30_vs_90={r['drift_30_vs_90']} "
                f"drift_30_vs_180={r['drift_30_vs_180']} "
                f"top_drifted={[x['regime'] for x in r['drifted_regimes']]}"
            )
        else:
            logger.info(
                f"[RegimeDrift][OK] drift_30_vs_90={r['drift_30_vs_90']} "
                f"drift_30_vs_180={r['drift_30_vs_180']}"
            )
        logger.info(f"[RegimeDrift][JSON] {json.dumps(r, ensure_ascii=False, default=str)}")
        return r
    except Exception as e:
        logger.warning(f"regime_drift_monitor 异常: {type(e).__name__}: {e}")
        return None


if __name__ == "__main__":
    r = detect_drift()
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))
