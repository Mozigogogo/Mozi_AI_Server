"""
信号卡周期复盘引擎 — 每周固定时间复盘

流程：
1. 取上周所有已结算的信号卡
2. 按信号源分组统计胜率/PnL
3. 批量喂给自适应策略引擎（只记录不调权）
4. 触发一次 evolve() 统一调整权重
5. 生成复盘报告
"""
import json
import math
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any

from config.settings import settings


def _get_conn():
    import pymysql
    return pymysql.connect(
        host=settings.bigorder_mysql_host or settings.mysql_host,
        port=settings.bigorder_mysql_port or settings.mysql_port,
        user=settings.bigorder_mysql_user or settings.mysql_user,
        password=settings.bigorder_mysql_password or settings.mysql_password,
        database=settings.bigorder_mysql_database or settings.mysql_database,
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=15,
    )


def weekly_review() -> Dict[str, Any]:
    """
    每周复盘

    步骤：
    1. 查询过去 7 天所有已结算的信号卡
    2. 汇总统计
    3. 批量喂给策略引擎
    4. 触发策略演化
    5. 生成报告
    """
    conn = None
    try:
        conn = _get_conn()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 查过去 7 天已结算的卡
        cursor.execute(
            """
            SELECT id, coin, direction, grade, stop_loss, take_profit,
                   current_price, confidence, sources_json, status,
                   settled_price, pnl_pct, created_at, settled_at,
                   strategy_version, regime
            FROM signal_card_history
            WHERE status IN ('hit_tp', 'hit_sl', 'expired')
              AND settled_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            ORDER BY settled_at ASC
            """
        )
        cards = cursor.fetchall()
        cursor.close()

        if not cards:
            return {"status": "no_data", "message": "过去7天无已结算的信号卡"}

        # ── 2. 汇总统计 ──────────────────────────────────────────
        total = len(cards)
        hit_tp = [c for c in cards if c["status"] == "hit_tp"]
        hit_sl = [c for c in cards if c["status"] == "hit_sl"]
        expired = [c for c in cards if c["status"] == "expired"]

        # 胜率：hit_tp / (hit_tp + hit_sl)
        decisive = hit_tp + hit_sl
        win_rate = len(hit_tp) / len(decisive) * 100 if decisive else 0

        # 平均盈亏
        pnls = [float(c["pnl_pct"] or 0) for c in cards]
        avg_pnl = sum(pnls) / len(pnls) if pnls else 0

        # 按等级分组
        grade_stats = {}
        for grade in ["S", "A", "B"]:
            grade_cards = [c for c in cards if c["grade"] == grade]
            if grade_cards:
                grade_tp = sum(1 for c in grade_cards if c["status"] == "hit_tp")
                grade_sl = sum(1 for c in grade_cards if c["status"] == "hit_sl")
                grade_decisive = grade_tp + grade_sl
                grade_pnls = [float(c["pnl_pct"] or 0) for c in grade_cards]
                grade_stats[grade] = {
                    "total": len(grade_cards),
                    "win_rate": round(grade_tp / grade_decisive * 100, 1) if grade_decisive else 0,
                    "avg_pnl": round(sum(grade_pnls) / len(grade_pnls), 2),
                }

        # 按方向分组
        long_cards = [c for c in cards if c["direction"] == "long"]
        short_cards = [c for c in cards if c["direction"] == "short"]

        direction_stats = {}
        for d, d_cards in [("long", long_cards), ("short", short_cards)]:
            if d_cards:
                d_tp = sum(1 for c in d_cards if c["status"] == "hit_tp")
                d_sl = sum(1 for c in d_cards if c["status"] == "hit_sl")
                d_decisive = d_tp + d_sl
                d_pnls = [float(c["pnl_pct"] or 0) for c in d_cards]
                direction_stats[d] = {
                    "total": len(d_cards),
                    "win_rate": round(d_tp / d_decisive * 100, 1) if d_decisive else 0,
                    "avg_pnl": round(sum(d_pnls) / len(d_pnls), 2),
                }

        # 夏普比率
        sharpe = _sharpe(pnls)

        # ── 3. 按信号源拆分 → 批量喂给策略引擎 ──────────────────
        from app.signals.adaptive_strategy import get_strategy_engine
        engine = get_strategy_engine()

        # 拆解每张卡的信号源，分别记录
        source_results: Dict[str, List[float]] = {}
        for card in cards:
            sources_json = card.get("sources_json")
            if not sources_json:
                continue
            try:
                sources = json.loads(sources_json)
            except (json.JSONDecodeError, TypeError):
                continue

            pnl = float(card["pnl_pct"] or 0)
            direction_correct = card["status"] == "hit_tp" or (
                card["status"] == "expired" and pnl > 0
            )

            for src in sources:
                name = src.get("name", "unknown")
                if name not in source_results:
                    source_results[name] = []
                source_results[name].append((pnl, direction_correct))

        # 批量写入（batch=True 只记录不调权）
        for src_name, results in source_results.items():
            for pnl, correct in results:
                engine.record_signal_result(src_name, pnl, correct, batch=True)

        # ── 4. 触发策略演化（统一调权）──────────────────────────
        evolve_report = engine.evolve()

        # ── 5. 生成复盘报告 ──────────────────────────────────────
        report = {
            "period": "7d",
            "reviewed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_cards": total,
            "hit_tp": len(hit_tp),
            "hit_sl": len(hit_sl),
            "expired": len(expired),
            "win_rate": round(win_rate, 1),
            "avg_pnl_pct": round(avg_pnl, 2),
            "sharpe_ratio": round(sharpe, 2),
            "by_grade": grade_stats,
            "by_direction": direction_stats,
            "by_source": {
                name: {
                    "total": len(results),
                    "win_rate": round(sum(1 for _, c in results if c) / len(results) * 100, 1),
                    "avg_pnl": round(sum(p for p, _ in results) / len(results), 2),
                }
                for name, results in source_results.items()
            },
            "strategy_evolution": evolve_report,
        }

        return report

    except Exception as e:
        print(f"周期复盘异常: {e}")
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


def get_review_summary() -> Dict[str, Any]:
    """获取最近复盘摘要（供前端展示）"""
    conn = None
    try:
        conn = _get_conn()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 总体统计
        cursor.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'hit_tp' THEN 1 ELSE 0 END) as hit_tp,
                SUM(CASE WHEN status = 'hit_sl' THEN 1 ELSE 0 END) as hit_sl,
                SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) as expired,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending,
                AVG(CASE WHEN pnl_pct IS NOT NULL THEN pnl_pct ELSE NULL END) as avg_pnl
            FROM signal_card_history
            """
        )
        overall = cursor.fetchone()

        # 近 7 天统计
        cursor.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN status = 'hit_tp' THEN 1 ELSE 0 END) as hit_tp,
                SUM(CASE WHEN status = 'hit_sl' THEN 1 ELSE 0 END) as hit_sl
            FROM signal_card_history
            WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            """
        )
        weekly = cursor.fetchone()
        cursor.close()

        return {
            "overall": overall,
            "weekly": weekly,
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        if conn:
            conn.close()


def _sharpe(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
    return (mean_r - 0.01) / std_r * math.sqrt(365)
