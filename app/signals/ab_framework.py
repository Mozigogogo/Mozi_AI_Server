"""
A/B 实验框架（Phase 6）— 自动对照 + 自动停用。

设计原则：
- experiment_bucket 字段写到 math_json 里（control / treatment）
- 默认 control；只有显式传入 treatment 才走实验路径
- 每日自动比较两桶累计 sum_pnl：treatment 显著差于 control → 自动停用
- fail-open：DB / 解析异常不影响信号生成

bucket 分配规则：
- 不依赖随机数（同一张卡分到同一桶）
- 使用 hash(coin + created_at_minute) 取模，确保稳定 + 均匀
- 默认 control 占 80%，treatment 占 20%（可配置）
"""
import hashlib
import json
import os
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

TREATMENT_RATIO = float(os.getenv("AB_TREATMENT_RATIO", "0.20"))
# treatment 显著差于 control 的阈值（sum_pnl 差距）
TREATMENT_DEGRADE_THRESHOLD = float(os.getenv("AB_DEGRADE_THRESHOLD", "-5.0"))
TREATMENT_MIN_SAMPLE = int(os.getenv("AB_MIN_SAMPLE", "10"))


def assign_bucket(coin: str, salt: str = "") -> str:
    """
    根据 coin + salt 稳定哈希到 control / treatment。
    默认 80% control / 20% treatment。
    """
    h = hashlib.md5(f"{coin}|{salt}".encode()).hexdigest()
    n = int(h[:8], 16) / 0xFFFFFFFF  # 0-1
    return "treatment" if n < TREATMENT_RATIO else "control"


def should_force_treatment(coin: str) -> bool:
    """是否强制走 treatment（用于特定币种灰度）。"""
    force_list = os.getenv("AB_FORCE_TREATMENT_COINS", "").strip()
    if not force_list:
        return False
    return (coin or "").upper() in {c.strip().upper() for c in force_list.split(",") if c.strip()}


def get_experiment_bucket(coin: str, force_treatment: Optional[bool] = None) -> str:
    """获取实验桶（对外统一入口）。"""
    if force_treatment is None:
        force_treatment = should_force_treatment(coin)
    if force_treatment:
        return "treatment"
    return assign_bucket(coin)


def _get_conn():
    from app.signals.settlement import _get_conn
    return _get_conn()


def compare_buckets(days: int = 7) -> Dict[str, Any]:
    """
    比较近 N 天 control vs treatment 的表现。
    返回 {control: {...}, treatment: {...}, delta_sum_pnl, treatment_active}。

    treatment_active = True 表示 treatment 还在可用状态（未触发自动停用）。
    fail-open：任何异常都返回 treatment_active=True（不停用）。
    """
    try:
        import pymysql.cursors
        conn = _get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT
                    JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.experiment_bucket')) AS bucket,
                    COUNT(*) AS n,
                    SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins,
                    SUM(pnl_pct) AS sum_pnl,
                    AVG(pnl_pct) AS avg_pnl
                FROM signal_card_history
                WHERE status IN ('hit_tp','hit_sl','expired')
                  AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                  AND math_json LIKE '%%experiment_bucket%%'
                GROUP BY bucket
                """,
                (days,),
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"ab_framework 查询失败: {type(e).__name__}: {e}")
        return {"control": None, "treatment": None, "delta_sum_pnl": 0, "treatment_active": True, "error": str(e)}

    buckets = {}
    for r in rows:
        key = r.get("bucket") or "control"
        n = int(r.get("n") or 0)
        wins = int(r.get("wins") or 0)
        buckets[key] = {
            "n": n,
            "wins": wins,
            "wr": round(wins / n * 100, 1) if n else 0,
            "sum_pnl": float(r.get("sum_pnl") or 0),
            "avg_pnl": float(r.get("avg_pnl") or 0),
        }

    control = buckets.get("control")
    treatment = buckets.get("treatment")

    delta_sum_pnl = 0.0
    treatment_active = True
    if control and treatment and treatment["n"] >= TREATMENT_MIN_SAMPLE:
        delta_sum_pnl = round(treatment["sum_pnl"] - control["sum_pnl"], 2)
        if delta_sum_pnl < TREATMENT_DEGRADE_THRESHOLD:
            treatment_active = False
            logger.warning(
                f"[AB] treatment 自动停用：control sum_pnl={control['sum_pnl']:+.2f} "
                f"treatment sum_pnl={treatment['sum_pnl']:+.2f} delta={delta_sum_pnl:+.2f}"
            )

    return {
        "control": control,
        "treatment": treatment,
        "delta_sum_pnl": delta_sum_pnl,
        "treatment_active": treatment_active,
        "thresholds": {
            "min_sample": TREATMENT_MIN_SAMPLE,
            "degrade_threshold": TREATMENT_DEGRADE_THRESHOLD,
        },
    }


def is_treatment_active() -> bool:
    """快速查询 treatment 是否仍可用（被 fusion 在使用新阈值/新源时调用）。"""
    r = compare_buckets(days=7)
    return r.get("treatment_active", True)


if __name__ == "__main__":
    print(json.dumps(compare_buckets(days=7), indent=2, ensure_ascii=False, default=str))
