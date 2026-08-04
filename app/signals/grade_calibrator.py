"""
自适应 grade 阈值校准器（Phase 3）。

设计原则：
1. 方向无关：所有 grade 的 wr 评估不分 long/short（A 级 wr 统一算）
2. 自动重审：每日跑，next_review_at 字段强制周期性重算
3. 边界保护：单次偏移最多 ±5，累积偏移有上下限（避免漂移到极端值）
4. 通胀熔断：A 级占比 >35% 强制收紧（即使 wr 看起来 OK）
5. fail-open：DB 异常不清空已有校准结果

校准规则（plan 里定的）：
- A 级 wr < 40%  → A 级 conf 阈值 +5（最多 +15）— 太松了，收紧
- A 级 wr > 65%  → A 级 conf 阈值 -5（最多 -10）— 太严了，放松
- A 级占比 >35% → 强制 +5 — 通胀熔断
"""
from typing import Any, Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

LOOKBACK_DAYS = 7
MIN_SAMPLE_FOR_CALIBRATION = 30
WR_LOW = 0.40
WR_HIGH = 0.65
SHARE_INFLATION = 0.35
STEP = 5
MAX_TIGHTEN = 15
MAX_LOOSEN = 10


def _query_grade_stats(days: int) -> Dict[str, Dict[str, Any]]:
    """查近 days 天各 grade 的 n / wins / wr。fail-open。"""
    try:
        import pymysql.cursors
        from app.signals.settlement import _get_conn
        conn = _get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT grade,
                       COUNT(*) AS n,
                       SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins
                FROM signal_card_history
                WHERE status IN ('hit_tp','hit_sl','expired')
                  AND grade IN ('S','A','B')
                  AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                GROUP BY grade
                """,
                (days,),
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f"grade_stats 查询失败: {type(e).__name__}: {e}")
        return {}

    stats: Dict[str, Dict[str, Any]] = {}
    total = 0
    for r in rows:
        n = int(r.get("n") or 0)
        wins = int(r.get("wins") or 0)
        grade = r.get("grade") or "?"
        stats[grade] = {
            "n": n,
            "wins": wins,
            "wr": round(wins / n, 3) if n else 0,
        }
        total += n
    stats["_total"] = {"n": total}
    return stats


def _query_total_with_grade(days: int) -> int:
    """查近 days 天所有已结算卡总数（用于算 A 级占比）。"""
    try:
        import pymysql.cursors
        from app.signals.settlement import _get_conn
        conn = _get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS n
                FROM signal_card_history
                WHERE status IN ('hit_tp','hit_sl','expired')
                  AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                """,
                (days,),
            )
            row = cur.fetchone() or {}
        conn.close()
        return int(row.get("n") or 0)
    except Exception as e:
        logger.warning(f"total_with_grade 查询失败: {type(e).__name__}: {e}")
        return 0


def calibrate_grade_thresholds() -> Dict[str, Any]:
    """
    每日跑：根据近 7d wr 调整 A/S grade 的 conf 阈值偏移。
    返回新的 calibrated_thresholds dict 并写入 strategy_state.json。
    """
    from datetime import datetime, timedelta
    from app.signals.adaptive_strategy import get_strategy_engine

    stats = _query_grade_stats(LOOKBACK_DAYS)
    total = _query_total_with_grade(LOOKBACK_DAYS)

    now_iso = datetime.utcnow().isoformat()
    next_review = (datetime.utcnow() + timedelta(days=1)).isoformat()

    # 从 engine 读旧偏移（累积调整，但限制上下限）
    engine = get_strategy_engine()
    old = engine.state.calibrated_thresholds or {}
    old_a_offset = int(old.get("A_conf_offset", 0))
    old_s_offset = int(old.get("S_conf_offset", 0))

    a_stats = stats.get("A", {})
    a_n = a_stats.get("n", 0)
    a_wr = a_stats.get("wr", 0)
    a_share = a_n / total if total > 0 else 0

    s_stats = stats.get("S", {})
    s_n = s_stats.get("n", 0)
    s_wr = s_stats.get("wr", 0)

    new_a_offset = old_a_offset
    new_s_offset = old_s_offset
    reasons = []

    # 规则 1: A 级 wr 太低 → 收紧
    if a_n >= MIN_SAMPLE_FOR_CALIBRATION and a_wr < WR_LOW:
        new_a_offset = min(MAX_TIGHTEN, old_a_offset + STEP)
        reasons.append(f"A级 wr={a_wr:.2f}<{WR_LOW} 收紧 +{STEP} (累计 {new_a_offset})")

    # 规则 2: A 级 wr 很高 → 放松
    elif a_n >= MIN_SAMPLE_FOR_CALIBRATION and a_wr > WR_HIGH:
        new_a_offset = max(-MAX_LOOSEN, old_a_offset - STEP)
        reasons.append(f"A级 wr={a_wr:.2f}>{WR_HIGH} 放松 -{STEP} (累计 {new_a_offset})")

    # 规则 3: A 级占比 >35% → 通胀熔断（即使 wr OK 也收紧）
    if a_share > SHARE_INFLATION and a_n >= MIN_SAMPLE_FOR_CALIBRATION:
        new_a_offset = min(MAX_TIGHTEN, max(new_a_offset, old_a_offset + STEP))
        reasons.append(f"A级占比 {a_share:.0%}>{SHARE_INFLATION:.0%} 通胀熔断 +{STEP}")

    # S 级同步：S 跟 A 走（保持相对关系），但 S 偏移幅度减半
    if new_a_offset != old_a_offset:
        new_s_offset = max(-MAX_LOOSEN // 2, min(MAX_TIGHTEN // 2, new_a_offset // 2))

    result = {
        "A_conf_offset": new_a_offset,
        "S_conf_offset": new_s_offset,
        "B_conf_offset": 0,  # B 不校准（让 B 真正出现）
        "calibrated_at": now_iso,
        "next_review_at": next_review,
        "lookback_days": LOOKBACK_DAYS,
        "stats": {
            "A": {"n": a_n, "wr": a_wr},
            "S": {"n": s_n, "wr": s_wr},
            "total": total,
            "A_share": round(a_share, 3),
        },
        "reasons": reasons,
    }

    # 写回 engine.state（持久化到 strategy_state.json）
    engine.state.calibrated_thresholds = result
    try:
        engine._save_state()
    except Exception as e:
        logger.warning(f"calibrator 持久化失败: {e}")

    if reasons:
        logger.info(f"[GradeCalibrator] {' | '.join(reasons)} → A_offset={new_a_offset} S_offset={new_s_offset}")
    else:
        logger.info(
            f"[GradeCalibrator] 无调整（A n={a_n} wr={a_wr} share={a_share:.0%}，"
            f"维持 A_offset={new_a_offset} S_offset={new_s_offset}）"
        )

    return result


def get_effective_thresholds() -> Dict[str, int]:
    """默认 v7 阈值 + 当前校准偏移 = 实际生效阈值。供 fusion.py 使用。"""
    from app.signals.adaptive_strategy import get_strategy_engine
    engine = get_strategy_engine()
    offsets = engine.state.calibrated_thresholds or {}
    a_off = int(offsets.get("A_conf_offset", 0))
    s_off = int(offsets.get("S_conf_offset", 0))
    return {
        "S_min_conf": 70 + s_off,            # 默认 70
        "A_3src_min_conf": 60 + a_off,       # 默认 60
        "A_2src_min_conf": 75 + a_off,       # 默认 75
        "B_min_conf": 35,                     # B 不校准
        "A_conf_offset": a_off,
        "S_conf_offset": s_off,
    }


def list_thresholds() -> Dict[str, Any]:
    """调试端点用：当前阈值 + 校准元数据 + 7d wr 统计"""
    from app.signals.adaptive_strategy import get_strategy_engine
    engine = get_strategy_engine()
    return {
        "effective": get_effective_thresholds(),
        "calibration": engine.state.calibrated_thresholds or {},
        "defaults": {
            "S": 70, "A_3src": 60, "A_2src": 75, "B": 35,
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(list_thresholds(), ensure_ascii=False, indent=2, default=str))
