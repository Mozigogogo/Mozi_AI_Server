"""
v6 vs v7 静态对比回测（Phase 6 验证框架的一部分）。

目标：用 signal_card_history 里 v6 时代已结算的卡，模拟 v7 + quality_gate + ev_guardrail
过滤后的 wr / sum_pnl，回答：
  1. v7 三重过滤是否真的提升 long wr？
  2. 被砍掉的卡 wr 是否显著低于保留的（验证过滤方向正确）？
  3. S/short 金矿是否被误杀？
  4. 各 alpha 在历史数据里的独立 wr（alpha_breakdown_30d）？

局限（必须坦白）：
  - 这是"剔除式"回测：v7 通过的卡 ⊆ v6 通过的卡。无法评估 v7 新增的 alpha 在历史数据上
    会触发哪些新信号（因为 alpha 触发依赖历史 funding rate / BTC trend 等不在表里的数据）。
  - ev_guardrail 用当前表状态评估，不是历史 60d 滚动重算的动态。
  - quality_gate 重算依赖 sources_json，但表里只存了 name/score/direction/detail，
    没有 weight 字段，会用默认权重 1.0 估算。

入口：
  - 命令行：python -m app.signals.backtest_v7_compare --days 30
  - HTTP：GET /api/v1/signals/backtest_v7?days=30
"""
import argparse
import json
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)


def _get_conn():
    from app.signals.settlement import _get_conn
    return _get_conn()


def _fetch_settled_cards(days: int) -> List[Dict[str, Any]]:
    """拉 N 天内已结算的卡（含 sources_json / math_json / regime）。"""
    import pymysql.cursors
    conn = _get_conn()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """
                SELECT id, coin, direction, grade, confidence, status, pnl_pct,
                       sources_json, math_json, regime, created_at
                FROM signal_card_history
                WHERE status IN ('hit_tp', 'hit_sl', 'expired')
                  AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                """,
                (days,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def _safe_json(s: Optional[str], default):
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _v7_grade(
    sources: List[Dict[str, Any]],
    confidence: float,
    direction: str,
    s_conf: float = 70,
    a3_conf: float = 60,
    a2_conf: float = 75,
    b_conf: float = 35,
) -> Tuple[str, bool]:
    """重算 v7 grade。返回 (grade, consistent_count_ok)。"""
    n = len(sources)
    consistent = sum(1 for s in sources if s.get("direction") == direction)
    if n >= 3 and consistent >= 3 and confidence >= s_conf:
        return "S", True
    if n >= 3 and consistent >= 3 and confidence >= a3_conf:
        return "A", True
    if consistent >= 2 and confidence >= a2_conf:
        return "A", True
    if consistent >= 2 and confidence >= b_conf:
        return "B", True
    return "C", consistent >= 2


def _v7_quality_score(sources: List[Dict[str, Any]], math_json: Dict[str, Any]) -> Dict[str, Any]:
    """重算 quality_gate 分数（参数和 quality_gate.py 保持一致）。"""
    from app.signals.quality_gate import score_signal
    from app.signals.models import SignalSource, SignalDirection

    srcs = []
    for s in sources:
        try:
            d = SignalDirection(s.get("direction", "long"))
        except Exception:
            d = SignalDirection.LONG
        srcs.append(SignalSource(
            name=s.get("name", "?"),
            score=float(s.get("score", 0)),
            direction=d,
            weight=1.0,
            detail=s.get("detail", ""),
        ))

    # 用 math_json 重建一个轻量 math_result（只够 _information_content 用）
    class _FakeMath:
        pass
    class _FakeEntropy:
        pass
    fake = _FakeMath()
    fake.entropy = _FakeEntropy()
    fake.entropy.predictability = (math_json or {}).get("entropy_predictability")
    dual_tf = {
        "tf_agreement": (math_json or {}).get("tf_agreement", ""),
    }
    return score_signal(srcs, fake, dual_tf)


def _v7_ev_guardrail(regime: str, math_json: Dict[str, Any], direction: str) -> Tuple[bool, Optional[Dict]]:
    """查 ev_guardrail 当前表，命中返回 (True, rule)。"""
    from app.signals.ev_guardrail import check_signal
    tfa = (math_json or {}).get("tf_agreement", "") or ""
    return check_signal(regime or "quiet", tfa, direction)


def _is_win(card: Dict[str, Any]) -> bool:
    s = card.get("status")
    if s == "hit_tp":
        return True
    if s == "expired":
        try:
            return float(card.get("pnl_pct") or 0) > 0
        except Exception:
            return False
    return False


def _aggregate(cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合 wr / sum_pnl / avg_pnl / count。"""
    n = len(cards)
    if n == 0:
        return {"n": 0, "wins": 0, "wr": 0.0, "sum_pnl": 0.0, "avg_pnl": 0.0}
    wins = sum(1 for c in cards if _is_win(c))
    sum_pnl = sum(float(c.get("pnl_pct") or 0) for c in cards)
    return {
        "n": n,
        "wins": wins,
        "wr": round(wins / n * 100, 1),
        "sum_pnl": round(sum_pnl, 2),
        "avg_pnl": round(sum_pnl / n, 3),
    }


def _by_direction_grade(cards: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """按 direction × grade 聚合。"""
    buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for c in cards:
        buckets[(c.get("direction", "?"), c.get("grade", "?"))].append(c)
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for (d, g), v in buckets.items():
        out.setdefault(d, {})[g] = _aggregate(v)
    return out


def _alpha_breakdown(cards: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 sources_json 里的 alpha name 聚合独立 wr。"""
    agg: Dict[str, Dict[str, Any]] = {}
    for c in cards:
        sources = _safe_json(c.get("sources_json"), [])
        names = {s.get("name") for s in sources if isinstance(s, dict)}
        alpha_names = {n for n in names if n and n.startswith("alpha_")}
        if not alpha_names:
            continue
        won = _is_win(c)
        pnl = float(c.get("pnl_pct") or 0)
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
            "alpha": d["alpha"], "n": n,
            "wr": round(d["wins"] / n * 100, 1) if n else 0,
            "sum_pnl": round(d["sum_pnl"], 2),
        })
    out.sort(key=lambda x: x["sum_pnl"], reverse=True)
    return out


def compare_v6_v7(days: int = 30, verbose: bool = False) -> Dict[str, Any]:
    """
    主入口：v6 实际 vs v7 模拟对比。

    Args:
        days: 回测窗口（30 / 90）
        verbose: 详细模式（多打日志）
    """
    try:
        cards = _fetch_settled_cards(days)
    except Exception as e:
        logger.error(f"拉取历史卡失败: {type(e).__name__}: {e}")
        return {"error": str(e), "days": days}

    if not cards:
        return {"error": "no_data", "days": days, "n": 0}

    # 读取当前 v7 阈值
    try:
        from app.signals.grade_calibrator import get_effective_thresholds
        eff = get_effective_thresholds()
        s_conf = eff["S_min_conf"]
        a3_conf = eff["A_3src_min_conf"]
        a2_conf = eff["A_2src_min_conf"]
        b_conf = eff["B_min_conf"]
    except Exception:
        s_conf, a3_conf, a2_conf, b_conf = 70, 60, 75, 35

    # 对每张卡模拟 v7 评估
    v7_kept: List[Dict[str, Any]] = []
    v7_killed: List[Dict[str, Any]] = []
    kill_reasons: Counter = Counter()

    for c in cards:
        sources = _safe_json(c.get("sources_json"), [])
        math_json = _safe_json(c.get("math_json"), {})
        direction = c.get("direction", "long")
        regime = c.get("regime") or (math_json or {}).get("market_regime") or "quiet"
        confidence = float(c.get("confidence") or 0)

        # 1) v7 grade 重新评估
        v7_grade, _ = _v7_grade(sources, confidence, direction, s_conf, a3_conf, a2_conf, b_conf)
        c_v7 = dict(c)
        c_v7["grade_v7"] = v7_grade

        # 2) quality_gate 重算
        try:
            qs = _v7_quality_score(sources, math_json)
            c_v7["quality_score_v7"] = qs["total"]
            qg_drop = qs["total"] < 0.6
        except Exception as e:
            c_v7["quality_score_v7"] = None
            qg_drop = False

        # 3) ev_guardrail 模拟过滤
        try:
            blocked, rule = _v7_ev_guardrail(regime, math_json, direction)
        except Exception:
            blocked, rule = False, None

        # 综合判断 v7 是否保留
        killed = False
        if qg_drop:
            kill_reasons["quality_gate"] += 1
            killed = True
        if blocked:
            kill_reasons[f"ev_guardrail:{rule.get('regime')}+{rule.get('tfa')}+{rule.get('direction')}"] += 1
            killed = True

        if killed:
            v7_killed.append(c_v7)
        else:
            v7_kept.append(c_v7)

    # 聚合对比
    result = {
        "days": days,
        "total_v6_cards": len(cards),
        "v7_kept_count": len(v7_kept),
        "v7_killed_count": len(v7_killed),
        "v7_kill_rate": round(len(v7_killed) / len(cards) * 100, 1) if cards else 0,
        "v6_actual": _aggregate(cards),
        "v7_simulated": _aggregate(v7_kept),
        "killed_cards_stats": _aggregate(v7_killed),
        "v6_by_direction_grade": _by_direction_grade(cards),
        "v7_by_direction_grade": _by_direction_grade(v7_kept),
        "kill_reasons": dict(kill_reasons.most_common(20)),
        "alpha_breakdown": _alpha_breakdown(cards),
        "thresholds_used": {"S": s_conf, "A_3src": a3_conf, "A_2src": a2_conf, "B": b_conf},
    }

    # 关键 KPI 单行摘要
    v6 = result["v6_actual"]
    v7 = result["v7_simulated"]
    k = result["killed_cards_stats"]
    logger.info(
        f"[V7Compare] days={days} n={len(cards)} "
        f"v6_wr={v6['wr']}% v7_wr={v7['wr']}% "
        f"v6_pnl={v6['sum_pnl']:+.2f} v7_pnl={v7['sum_pnl']:+.2f} "
        f"killed_n={len(v7_killed)} killed_wr={k['wr']}% killed_pnl={k['sum_pnl']:+.2f}"
    )
    logger.info(f"[V7Compare][JSON] {json.dumps(result, ensure_ascii=False, default=str)}")
    return result


# ── CLI 入口 ────────────────────────────────────────────────────────────────

def _cli():
    parser = argparse.ArgumentParser(description="v6 vs v7 静态对比回测")
    parser.add_argument("--days", type=int, default=30, help="回测窗口（默认 30 天）")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    r = compare_v6_v7(days=args.days, verbose=args.verbose)
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    _cli()
