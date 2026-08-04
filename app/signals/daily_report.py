"""
每日信号卡表现报表 — 自动化验证框架。

每日生成结构化报表，包含：
- 昨日 direction×grade 矩阵（wr / sum_pnl）
- 7d 滚动 wr 趋势
- guardrail / reward 表当前状态
- market_breadth 各档分布
- Phase 0+1+5 各机制的触发次数
- Top 5 印钞机 / 失血机（按 sum_pnl）

设计原则：
1. 失败 fail-open：DB 异常不阻塞其他任务
2. 结构化 JSON 输出：logger.info 一行 JSON，便于后续 grep/解析
3. 可扩展：返回 dict，后续可以接 webhook / 邮件 / 社区推送
"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _get_conn():
    """复用 settlement 的 DB 连接"""
    from app.signals.settlement import _get_conn
    return _get_conn()


def _query_df(query: str, args: tuple = ()) -> List[Dict[str, Any]]:
    """便捷查询，返回 dict 列表。失败返回空列表。"""
    try:
        import pymysql.cursors
        conn = _get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(query, args)
            rows = cur.fetchall()
        conn.close()
        return list(rows)
    except Exception as e:
        logger.warning(f"daily_report 查询失败: {type(e).__name__}: {e}")
        return []


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def _safe_int(v, default=0) -> int:
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _direction_grade_matrix(days: int = 1) -> List[Dict[str, Any]]:
    """direction × grade 矩阵：n / wins / wr / sum_pnl"""
    rows = _query_df(
        """
        SELECT direction, grade,
               COUNT(*) AS n,
               SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins,
               SUM(status='hit_sl') AS sl,
               AVG(pnl_pct) AS avg_pnl,
               SUM(pnl_pct) AS sum_pnl
        FROM signal_card_history
        WHERE status IN ('hit_tp','hit_sl','expired')
          AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY direction, grade
        ORDER BY direction, grade
        """,
        (days,),
    )
    out = []
    for r in rows:
        n = _safe_int(r.get("n"))
        wins = _safe_int(r.get("wins"))
        out.append({
            "direction": r.get("direction") or "?",
            "grade": r.get("grade") or "?",
            "n": n,
            "wins": wins,
            "sl": _safe_int(r.get("sl")),
            "wr": round(wins / n * 100, 1) if n else 0,
            "avg_pnl": round(_safe_float(r.get("avg_pnl")), 3),
            "sum_pnl": round(_safe_float(r.get("sum_pnl")), 2),
        })
    return out


def _rolling_wr_trend(days: int = 7) -> List[Dict[str, Any]]:
    """每日 wr 滚动趋势"""
    rows = _query_df(
        """
        SELECT DATE(settled_at) AS d,
               COUNT(*) AS n,
               SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins,
               SUM(pnl_pct) AS sum_pnl
        FROM signal_card_history
        WHERE status IN ('hit_tp','hit_sl','expired')
          AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY DATE(settled_at)
        ORDER BY d DESC
        """,
        (days,),
    )
    out = []
    for r in rows:
        n = _safe_int(r.get("n"))
        wins = _safe_int(r.get("wins"))
        out.append({
            "date": str(r.get("d")),
            "n": n,
            "wins": wins,
            "wr": round(wins / n * 100, 1) if n else 0,
            "sum_pnl": round(_safe_float(r.get("sum_pnl")), 2),
        })
    return out


def _breadth_distribution(days: int = 7) -> Dict[str, int]:
    """market_breadth 各档分布（从 math_json 提取）"""
    rows = _query_df(
        """
        SELECT JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.market_breadth.breadth')) AS breadth,
               COUNT(*) AS n
        FROM signal_card_history
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
          AND JSON_EXTRACT(math_json, '$.market_breadth.breadth') IS NOT NULL
        GROUP BY breadth
        """,
        (days,),
    )
    return {str(r.get("breadth") or "unknown"): _safe_int(r.get("n")) for r in rows}


def _guardrail_hits(days: int = 1) -> Dict[str, int]:
    """昨日 ev_guardrail 触发次数（block / reward）"""
    rows = _query_df(
        """
        SELECT JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.ev_guardrail.action')) AS action,
               COUNT(*) AS n
        FROM signal_card_history
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
          AND JSON_EXTRACT(math_json, '$.ev_guardrail.action') IS NOT NULL
        GROUP BY action
        """,
        (days,),
    )
    return {str(r.get("action") or "unknown"): _safe_int(r.get("n")) for r in rows}


def _alpha_breakdown(days: int = 7) -> List[Dict[str, Any]]:
    """Phase 4 各 alpha 的独立 wr / sum_pnl（哪个 alpha 在印钞/失血）。

    sources_json 形如 [{"name":"alpha_breakout_retest",...}]，
    用 LIKE 粗筛 + JSON 解析细筛（兼容 MySQL 5.7 / 8.0）。
    """
    rows = _query_df(
        """
        SELECT direction, status, pnl_pct, sources_json
        FROM signal_card_history
        WHERE status IN ('hit_tp','hit_sl','expired')
          AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
          AND sources_json LIKE '%%alpha_%%'
        """,
        (days,),
    )
    if not rows:
        return []

    # 客户端按 source name 聚合（避免依赖 JSON_TABLE）
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        try:
            sources = json.loads(r.get("sources_json") or "[]")
        except Exception:
            continue
        names = {s.get("name") for s in sources if isinstance(s, dict)}
        alpha_names = {n for n in names if n and n.startswith("alpha_")}
        if not alpha_names:
            continue
        won = r.get("status") == "hit_tp" or (r.get("status") == "expired" and _safe_float(r.get("pnl_pct")) > 0)
        pnl = _safe_float(r.get("pnl_pct"))
        for name in alpha_names:
            d = agg.setdefault(name, {"alpha": name, "n": 0, "wins": 0, "sum_pnl": 0.0})
            d["n"] += 1
            if won:
                d["wins"] += 1
            d["sum_pnl"] += pnl

    out = []
    for d in agg.values():
        n = d["n"]
        out.append({
            "alpha": d["alpha"],
            "n": n,
            "wr": round(d["wins"] / n * 100, 1) if n else 0,
            "sum_pnl": round(d["sum_pnl"], 2),
        })
    out.sort(key=lambda x: x["sum_pnl"], reverse=True)
    return out


def _top_coins(days: int = 7, limit: int = 5, order_desc: bool = True) -> List[Dict[str, Any]]:
    """Top N 印钞/失血机（按 sum_pnl）"""
    direction = "DESC" if order_desc else "ASC"
    rows = _query_df(
        f"""
        SELECT coin,
               COUNT(*) AS n,
               SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins,
               SUM(pnl_pct) AS sum_pnl,
               AVG(pnl_pct) AS avg_pnl
        FROM signal_card_history
        WHERE status IN ('hit_tp','hit_sl','expired')
          AND settled_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
        GROUP BY coin
        HAVING n >= 3
        ORDER BY sum_pnl {direction}
        LIMIT %s
        """,
        (days, limit),
    )
    out = []
    for r in rows:
        n = _safe_int(r.get("n"))
        wins = _safe_int(r.get("wins"))
        out.append({
            "coin": r.get("coin") or "?",
            "n": n,
            "wr": round(wins / n * 100, 1) if n else 0,
            "sum_pnl": round(_safe_float(r.get("sum_pnl")), 2),
            "avg_pnl": round(_safe_float(r.get("avg_pnl")), 3),
        })
    return out


def _summary_24h() -> Dict[str, Any]:
    """昨日整体汇总"""
    rows = _query_df(
        """
        SELECT
            COUNT(*) AS n,
            SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins,
            SUM(status='hit_sl') AS sl,
            SUM(pnl_pct) AS sum_pnl,
            AVG(pnl_pct) AS avg_pnl
        FROM signal_card_history
        WHERE status IN ('hit_tp','hit_sl','expired')
          AND settled_at >= DATE_SUB(NOW(), INTERVAL 1 DAY)
        """
    )
    if not rows:
        return {"n": 0, "wins": 0, "wr": 0, "sum_pnl": 0, "avg_pnl": 0}
    r = rows[0]
    n = _safe_int(r.get("n"))
    wins = _safe_int(r.get("wins"))
    return {
        "n": n,
        "wins": wins,
        "sl": _safe_int(r.get("sl")),
        "wr": round(wins / n * 100, 1) if n else 0,
        "sum_pnl": round(_safe_float(r.get("sum_pnl")), 2),
        "avg_pnl": round(_safe_float(r.get("avg_pnl")), 3),
    }


def generate_daily_report() -> Dict[str, Any]:
    """
    生成每日报表。fail-open：任何子查询失败都不阻塞其他部分。
    """
    now = datetime.utcnow()
    report = {
        "generated_at": now.isoformat(),
        "timezone": "UTC",
        "summary_24h": _summary_24h(),
        "direction_grade_matrix_24h": _direction_grade_matrix(days=1),
        "rolling_7d_trend": _rolling_wr_trend(days=7),
        "breadth_distribution_7d": _breadth_distribution(days=7),
        "guardrail_hits_24h": _guardrail_hits(days=1),
        "top5_printers_7d": _top_coins(days=7, limit=5, order_desc=True),
        "top5_bleeders_7d": _top_coins(days=7, limit=5, order_desc=False),
        "alpha_breakdown_7d": _alpha_breakdown(days=7),
    }

    # 加 guardrail 表当前状态
    try:
        from app.signals.ev_guardrail import list_rules
        report["guardrail_status"] = list_rules()
    except Exception as e:
        logger.warning(f"读取 guardrail 状态失败: {e}")
        report["guardrail_status"] = None

    # 关键 KPI 单行摘要（便于日志 grep）
    s = report["summary_24h"]
    kpi = (
        f"[DailyReport] n={s['n']} wr={s['wr']}% sum_pnl={s['sum_pnl']:+.2f} "
        f"avg_pnl={s['avg_pnl']:+.3f}"
    )
    logger.info(kpi)

    # 完整 JSON 报表（一行便于解析）
    logger.info(f"[DailyReport][JSON] {json.dumps(report, ensure_ascii=False, default=str)}")

    return report


if __name__ == "__main__":
    # 手动触发：python -m app.signals.daily_report
    r = generate_daily_report()
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
