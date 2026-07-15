"""
交易信号卡 - 历史胜率回测

数据源优先级：
1. signal_card_history 真实结算结果（推荐，跟给用户的卡同源）
   - status 字段由 settle_pending_cards 任务用 24h 真实 K 线后验填充
   - 24h 内触发 TP/SL → hit_tp/hit_sl；24h 未触发 → expired
2. anomaly_history + 后续异动价（legacy fallback，样本稀疏）
3. 买卖比估算（最低优先级 fallback）

统计指标：胜率 / 夏普 / 索提诺 / 最大回撤 / 统计显著性
"""
import os
import time as _time
from typing import Optional, Dict, Any, List, Tuple
from config.settings import settings
from app.utils.logger import get_logger

logger = get_logger("app.signals.backtest")


def _env_get(key: str) -> str:
    """从 os.environ 取值，兼容 Railway env var key 前后空格"""
    val = os.environ.get(key)
    if val is not None:
        return val.strip()
    for k, v in os.environ.items():
        if k.strip() == key:
            return v.strip()
    return ""


def _get_connection():
    """获取数据库连接 — 复用 settlement._get_conn 保证 Railway 环境变量兼容性"""
    from app.signals.settlement import _get_conn
    return _get_conn()


# ── 主入口 ────────────────────────────────────────────────────────────────────

def backtest_signal(
    coin: str,
    direction: str,
    signal_grade: str,
    stop_loss_pct: float = 3.0,
    take_profit_pct: float = 5.0,
    lookback_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """
    回测信号卡历史胜率

    优先用 signal_card_history 真实结算结果（跟给用户的卡同源策略 + 同源 TP/SL）；
    样本不足时 fallback 到 anomaly_history legacy 回测。

    Returns:
        {
            "win_rate": 62.5,            # 宽松胜率（含 expired 盈利）
            "strict_win_rate": 68.0,     # 严格胜率（仅看触发 TP/SL 的卡）
            "sample_count": 48,
            "avg_profit_pct": 2.3,
            "sharpe_ratio": 1.5,
            "sortino_ratio": 2.1,
            "max_drawdown_pct": -8.5,
            "breakdown": {"hit_tp": 20, "hit_sl": 10, "expired": 18},
            "source": "signal_card_history"
        }
    """
    result = backtest_from_signal_history(coin, direction, signal_grade, lookback_days)
    if result:
        return result

    return _backtest_from_anomaly_legacy(
        coin, direction, signal_grade,
        stop_loss_pct, take_profit_pct, lookback_days,
    )


# ── 数据源 1：signal_card_history（推荐） ─────────────────────────────────────

def backtest_from_signal_history(
    coin: str,
    direction: str,
    signal_grade: Optional[str],
    lookback_days: int = 90,
    min_sample: int = 10,
) -> Optional[Dict[str, Any]]:
    """
    基于 signal_card_history 真实结算结果的回测（含维度 fallback）

    Fallback 优先级（第一性原理：coin > direction > grade）：
      1. coin+direction+grade, sample≥30  → confidence_level="high"
      2. coin+direction, sample≥30         → confidence_level="mid"
      3. coin+direction, sample≥min_sample → confidence_level="low"
      4. otherwise                         → None

    每条记录都是「真实生成的信号卡 + 真实 TP/SL + 24h K 线后验结果」，
    跟给用户的卡完全同源。
    """
    conn = None
    try:
        conn = _get_connection()

        # Tier 1: 同币+同向+同等级
        if signal_grade:
            cards = _query_settled_cards(conn, coin, direction, signal_grade, lookback_days)
            if len(cards) >= 30:
                return _build_result(cards, confidence_level="high", grade_filter=signal_grade)

        # Tier 2/3: 同币+同向（合并等级）
        cards = _query_settled_cards(conn, coin, direction, None, lookback_days)
        if len(cards) >= 30:
            return _build_result(cards, confidence_level="mid", grade_filter=None)
        if len(cards) >= min_sample:
            return _build_result(cards, confidence_level="low", grade_filter=None)

        return None

    except Exception as e:
        logger.error(f"signal_history 回测失败: {e}")
        return None
    finally:
        if conn:
            conn.close()


def _query_settled_cards(conn, coin: str, direction: str, grade: Optional[str], lookback_days: int) -> List[dict]:
    """查询已结算卡（hit_tp/hit_sl/expired），按 coin+direction[+grade] 过滤"""
    import pymysql.cursors
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    where_parts = [
        "coin = %s",
        "direction = %s",
        "status IN ('hit_tp', 'hit_sl', 'expired')",
        "created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)",
    ]
    params: list = [coin.upper(), direction, lookback_days]
    if grade:
        where_parts.append("grade = %s")
        params.append(grade)
    cursor.execute(
        f"""SELECT id, grade, direction, current_price, stop_loss, take_profit,
                  status, pnl_pct, created_at, settled_at
           FROM signal_card_history
           WHERE {" AND ".join(where_parts)}
           ORDER BY created_at DESC
           LIMIT 500""",
        params,
    )
    rows = cursor.fetchall()
    cursor.close()
    return rows


# ── 批量胜率查询（/simple/best 实时选卡用） ─────────────────────────────────

_WINRATE_CACHE_TTL = 600  # 10 分钟（信号卡 24h 才结算，胜率变化慢）
_winrate_cache: Dict[str, Any] = {"data": None, "expires_at": 0.0}


def batch_query_winrates(
    coin_directions: List[Tuple[str, str]],
    lookback_days: int = 90,
    min_sample: int = 1,
) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """
    一次性查所有 (coin, direction) 在 lookback_days 内的真实结算胜率。

    用单次 GROUP BY 拿全市场，再在内存里过滤候选列表，避免 N 次 DB round-trip。
    结果缓存 10 分钟（_WINRATE_CACHE_TTL），因为 signal_card 24h 才结算。

    Args:
        coin_directions: 候选列表 [(coin, direction), ...]，仅用于过滤
        lookback_days: 回看窗口（默认 90 天）
        min_sample: 最小样本数过滤

    Returns:
        {(coin, direction): {"win_rate": float, "sample_count": int, "avg_pnl_pct": float}}
        找不到的 pair 不在 dict 里
    """
    now = _time.time()
    cache = _winrate_cache["data"]
    if cache is None or now > _winrate_cache["expires_at"]:
        conn = None
        try:
            conn = _get_connection()
            import pymysql.cursors
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute(
                f"""SELECT
                        UPPER(coin) AS coin,
                        direction,
                        COUNT(*) AS total,
                        SUM(status='hit_tp') AS wins,
                        ROUND(AVG(pnl_pct), 3) AS avg_pnl
                    FROM signal_card_history
                    WHERE status IN ('hit_tp', 'hit_sl', 'expired')
                      AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    GROUP BY UPPER(coin), direction
                 """,
                (lookback_days,),
            )
            rows = cursor.fetchall()
            cursor.close()
            cache = {}
            for r in rows:
                total = int(r["total"] or 0)
                if total < 1:
                    continue
                wins = int(r["wins"] or 0)
                cache[(r["coin"], r["direction"])] = {
                    "win_rate": round(wins / total * 100, 1),
                    "sample_count": total,
                    "avg_pnl_pct": float(r["avg_pnl"] or 0.0),
                }
            _winrate_cache["data"] = cache
            _winrate_cache["expires_at"] = now + _WINRATE_CACHE_TTL
        except Exception as e:
            logger.error(f"batch_query_winrates 失败: {e}")
            return {}
        finally:
            if conn:
                conn.close()

    # 过滤候选
    result = {}
    for coin, direction in coin_directions:
        key = (coin.upper(), direction.lower())
        stats = cache.get(key)
        if stats and stats["sample_count"] >= min_sample:
            result[key] = stats
    return result


def invalidate_winrate_cache() -> None:
    """强制清缓存（测试用 / 关键节点后立即刷新）"""
    _winrate_cache["data"] = None
    _winrate_cache["expires_at"] = 0.0


# ── 反过度交易护栏（防止 TST 类灾难：4h 内连出 7 张 short 全打止损） ────────

_COOLDOWN_CACHE_TTL = 300  # 5 分钟（结算状态变化慢，5min 足够）
_cooldown_cache: Dict[str, Any] = {}


def is_direction_in_cooldown(
    coin: str,
    direction: str,
    consecutive_sl_threshold: int = 3,
    window_hours: int = 24,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    判断 (coin, direction) 是否处于"连续止损冷却期"。

    触发条件：window_hours 内最近 consecutive_sl_threshold 张卡全部 hit_sl。
    命中后自动冷却到 window_hours 内最早一张 hit_sl 卡的 created_at + window_hours。

    5 分钟缓存（避免扫描时 96 币 × 2 方向 = 192 次重复查询）。

    Returns:
        (in_cooldown: bool, context: {recent_sl_count, last_sl_at, sample_card_created_at} | None)
    """
    cache_key = f"{coin.upper()}|{direction.lower()}"
    now = _time.time()
    cached = _cooldown_cache.get(cache_key)
    if cached and cached["expires_at"] > now:
        return cached["in_cooldown"], cached["context"]

    conn = None
    in_cooldown = False
    context: Optional[Dict[str, Any]] = None
    try:
        conn = _get_connection()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        # 注意：INTERVAL 和 LIMIT 用 f-string 拼接整数（参数化会被引号包裹成字符串报错）
        # window_hours / consecutive_sl_threshold 都是模块级 int 字面量，不存在 SQL 注入风险
        cursor.execute(
            f"""SELECT id, status, created_at, settled_at, pnl_pct
               FROM signal_card_history
               WHERE coin = %s AND direction = %s
                 AND status IN ('hit_tp', 'hit_sl', 'expired')
                 AND created_at >= DATE_SUB(NOW(), INTERVAL {int(window_hours)} HOUR)
               ORDER BY created_at DESC
               LIMIT {int(consecutive_sl_threshold)}""",
            (coin.upper(), direction.lower()),
        )
        rows = cursor.fetchall()
        cursor.close()

        if len(rows) >= consecutive_sl_threshold and all(r["status"] == "hit_sl" for r in rows):
            in_cooldown = True
            context = {
                "recent_sl_count": len(rows),
                "last_sl_at": str(rows[0]["created_at"]),
                "first_sl_at": str(rows[-1]["created_at"]),
                "window_hours": window_hours,
                "coin": coin.upper(),
                "direction": direction.lower(),
            }
    except Exception as e:
        logger.error(f"is_direction_in_cooldown 查询失败 ({coin}/{direction}): {e}")
        # 查询失败不阻塞信号生成（fail-open）
        return False, None
    finally:
        if conn:
            conn.close()

    _cooldown_cache[cache_key] = {
        "in_cooldown": in_cooldown,
        "context": context,
        "expires_at": now + _COOLDOWN_CACHE_TTL,
    }
    return in_cooldown, context


def invalidate_cooldown_cache(coin: Optional[str] = None, direction: Optional[str] = None) -> None:
    """清冷却期缓存。不传参则清全部。"""
    if not coin:
        _cooldown_cache.clear()
        return
    key = f"{coin.upper()}|{(direction or '').lower()}"
    if direction:
        _cooldown_cache.pop(key, None)
    else:
        # 清该币所有 direction
        keys_to_del = [k for k in _cooldown_cache if k.startswith(f"{coin.upper()}|")]
        for k in keys_to_del:
            _cooldown_cache.pop(k, None)


def _build_result(cards: List[dict], confidence_level: str, grade_filter: Optional[str]) -> Dict[str, Any]:
    """从已结算卡列表构建回测结果"""
    hit_tp = [c for c in cards if c["status"] == "hit_tp"]
    hit_sl = [c for c in cards if c["status"] == "hit_sl"]
    expired = [c for c in cards if c["status"] == "expired"]

    triggered = len(hit_tp) + len(hit_sl)
    strict_win_rate = (len(hit_tp) / triggered * 100) if triggered > 0 else 0.0

    wins = len(hit_tp) + sum(1 for c in expired if (c.get("pnl_pct") or 0) > 0)
    loose_win_rate = wins / len(cards) * 100

    pnls = [float(c.get("pnl_pct") or 0) for c in cards]
    avg_profit = sum(pnls) / len(pnls)

    return {
        "win_rate": round(loose_win_rate, 1),
        "strict_win_rate": round(strict_win_rate, 1),
        "sample_count": len(cards),
        "avg_profit_pct": round(avg_profit, 2),
        "sharpe_ratio": round(_sharpe_ratio(pnls), 2),
        "sortino_ratio": round(_sortino_ratio(pnls), 2),
        "max_drawdown_pct": round(_max_drawdown(pnls), 2),
        "breakdown": {
            "hit_tp": len(hit_tp),
            "hit_sl": len(hit_sl),
            "expired": len(expired),
        },
        "timeframes": {"24h": round(loose_win_rate, 1)},
        "statistical_significance": _test_significance(pnls),
        "confidence_level": confidence_level,
        "grade_filter": grade_filter,
        "source": "signal_card_history",
    }


# ── 数据源 2：anomaly_history（legacy fallback） ──────────────────────────────

def _backtest_from_anomaly_legacy(
    coin: str,
    direction: str,
    signal_grade: str,
    stop_loss_pct: float,
    take_profit_pct: float,
    lookback_days: int,
) -> Optional[Dict[str, Any]]:
    """legacy 回测：anomaly_history + 后续异动价（样本稀疏，仅作 fallback）"""
    conn = None
    try:
        conn = _get_connection()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        level_filter = "strong" if signal_grade == "S" else "'medium', 'strong'"
        cursor.execute(
            f"""
            SELECT coin, total_score, level, net_flow,
                   buy_amount, sell_amount, latest_price,
                   timestamp, created_at
            FROM anomaly_history
            WHERE coin = %s
              AND level IN ({level_filter})
              AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY created_at ASC
            LIMIT 300
            """,
            (coin.upper(), lookback_days),
        )
        signals = cursor.fetchall()
        cursor.close()

        if not signals:
            return _fallback_backtest(coin, direction, lookback_days, conn)

        if direction == "long":
            matched = [s for s in signals if (s.get("net_flow") or 0) > 0]
        else:
            matched = [s for s in signals if (s.get("net_flow") or 0) < 0]

        if len(matched) < 3:
            return _fallback_backtest(coin, direction, lookback_days, conn)

        trades: List[Dict] = []
        for i, sig in enumerate(matched):
            entry_price = sig.get("latest_price")
            entry_time = str(sig.get("created_at", ""))
            if not entry_price or entry_price <= 0:
                continue

            next_price_4h = next_price_12h = next_price_24h = None
            for j in range(i + 1, min(i + 6, len(signals))):
                future = signals[j]
                future_time = str(future.get("created_at", ""))
                future_price = future.get("latest_price")
                if not future_price or future_price <= 0:
                    continue
                time_diff_hours = _hours_between(entry_time, future_time)
                if time_diff_hours is None:
                    continue
                if next_price_4h is None and time_diff_hours >= 2:
                    next_price_4h = (future_price, time_diff_hours)
                if next_price_12h is None and time_diff_hours >= 8:
                    next_price_12h = (future_price, time_diff_hours)
                if next_price_24h is None and time_diff_hours >= 16:
                    next_price_24h = (future_price, time_diff_hours)
                if next_price_24h:
                    break

            best_price = None
            for price_info in [next_price_4h, next_price_12h, next_price_24h]:
                if price_info:
                    best_price = price_info[0]
                    break
            if not best_price:
                continue

            pnl_pct = (best_price - entry_price) / entry_price * 100
            if direction == "long":
                hit_tp = pnl_pct >= take_profit_pct
                hit_sl = pnl_pct <= -stop_loss_pct
            else:
                pnl_pct = -pnl_pct
                hit_tp = pnl_pct >= take_profit_pct
                hit_sl = pnl_pct <= -stop_loss_pct

            if hit_tp:
                realized_pnl = take_profit_pct
            elif hit_sl:
                realized_pnl = -stop_loss_pct
            else:
                realized_pnl = pnl_pct

            trades.append({
                "pnl_pct": realized_pnl,
                "win": realized_pnl > 0,
            })

        if not trades:
            return _fallback_backtest(coin, direction, lookback_days, conn)

        wins = [t for t in trades if t["win"]]
        win_rate = len(wins) / len(trades) * 100
        avg_profit = sum(t["pnl_pct"] for t in trades) / len(trades)
        returns = [t["pnl_pct"] for t in trades]

        return {
            "win_rate": round(win_rate, 1),
            "strict_win_rate": round(win_rate, 1),
            "sample_count": len(trades),
            "avg_profit_pct": round(avg_profit, 2),
            "sharpe_ratio": round(_sharpe_ratio(returns), 2),
            "sortino_ratio": round(_sortino_ratio(returns), 2),
            "max_drawdown_pct": round(_max_drawdown(returns), 2),
            "breakdown": {"hit_tp": len(wins), "hit_sl": len(trades) - len(wins), "expired": 0},
            "timeframes": {"24h": round(win_rate, 1)},
            "statistical_significance": _test_significance(returns),
            "source": "anomaly_history_legacy",
        }

    except Exception as e:
        logger.error(f"回测失败: {e}")
        return None
    finally:
        if conn:
            conn.close()


def _fallback_backtest(
    coin: str,
    direction: str,
    lookback_days: int,
    conn,
) -> Optional[Dict[str, Any]]:
    """降级回测（无真实价格时使用买卖比估算）"""
    if not conn:
        return None

    try:
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            """
            SELECT coin, total_score, level, net_flow,
                   buy_amount, sell_amount, created_at
            FROM anomaly_history
            WHERE coin = %s
              AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY created_at DESC
            LIMIT 200
            """,
            (coin.upper(), lookback_days),
        )
        signals = cursor.fetchall()
        cursor.close()

        if not signals:
            return None

        if direction == "long":
            matched = [s for s in signals if (s.get("net_flow") or 0) > 0]
        else:
            matched = [s for s in signals if (s.get("net_flow") or 0) < 0]

        if not matched:
            return None

        correct = 0
        total_pnl = 0.0
        for s in matched:
            net = s.get("net_flow", 0) or 0
            buy = s.get("buy_amount", 0) or 0
            sell = s.get("sell_amount", 0) or 0
            total_vol = buy + sell
            if total_vol <= 0:
                continue
            if direction == "long":
                ratio = buy / total_vol
                expected_win = ratio > 0.6
            else:
                ratio = sell / total_vol
                expected_win = ratio > 0.6
            if expected_win:
                correct += 1
                total_pnl += abs(net) / total_vol * 100
            else:
                total_pnl -= abs(net) / total_vol * 50

        win_rate = (correct / len(matched) * 100) if matched else 0
        avg_profit = total_pnl / len(matched) if matched else 0

        return {
            "win_rate": round(win_rate, 1),
            "strict_win_rate": round(win_rate, 1),
            "sample_count": len(matched),
            "avg_profit_pct": round(avg_profit, 2),
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "breakdown": {"hit_tp": correct, "hit_sl": len(matched) - correct, "expired": 0},
            "timeframes": {"24h": round(win_rate, 1)},
            "statistical_significance": None,
            "source": "buy_sell_ratio_estimate",
        }

    except Exception:
        return None


# ── Walk-Forward 验证 ─────────────────────────────────────────────────────────

def walk_forward_validation(
    coin: str,
    direction: str,
    signal_grade: Optional[str] = None,
    train_ratio: float = 0.7,
    lookback_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """
    Walk-Forward 验证 — 基于 signal_card_history 真实结算

    Fallback 与 backtest_from_signal_history 一致：
      1. coin+direction+grade, sample≥30 → high
      2. coin+direction, sample≥10 → mid
      3. otherwise → None

    按时间排序，前 train_ratio 比例作为 in-sample，剩余作为 out-of-sample。
    """
    conn = None
    try:
        conn = _get_connection()
        cards: List[dict] = []
        confidence_level = None

        # Tier 1: 同币+同向+同等级
        if signal_grade:
            cards = _query_settled_cards(conn, coin, direction, signal_grade, lookback_days)
            if len(cards) >= 30:
                confidence_level = "high"

        # Tier 2: 同币+同向
        if not confidence_level:
            cards = _query_settled_cards(conn, coin, direction, None, lookback_days)
            if len(cards) >= 10:
                confidence_level = "mid"

        if not confidence_level or len(cards) < 10:
            return None

        # 按时间正序排列
        cards.sort(key=lambda c: c.get("created_at"))

        split = int(len(cards) * train_ratio)
        in_sample = cards[:split]
        out_sample = cards[split:]
        if not out_sample:
            return None

        def _wr(slice_cards):
            if not slice_cards:
                return 0.0
            wins = sum(1 for c in slice_cards
                       if c["status"] == "hit_tp"
                       or (c["status"] == "expired" and (c.get("pnl_pct") or 0) > 0))
            return wins / len(slice_cards) * 100

        in_wr = _wr(in_sample)
        oos_wr = _wr(out_sample)
        gap = abs(in_wr - oos_wr)
        robustness = max(0.0, 100.0 - gap)
        is_robust = gap < 15

        return {
            "method": "walk_forward",
            "train_ratio": train_ratio,
            "in_sample_win_rate": round(in_wr, 1),
            "out_sample_win_rate": round(oos_wr, 1),
            "in_sample_count": len(in_sample),
            "out_sample_count": len(out_sample),
            "robustness_score": round(robustness, 1),
            "is_robust": is_robust,
            "confidence_level": confidence_level,
            "interpretation": (
                f"Walk-Forward OOS 胜率 {oos_wr:.1f}% (n={len(out_sample)})，"
                f"与 in-sample 差距 {gap:.1f}pp，"
                f"{'稳健' if is_robust else '存在过拟合风险'}"
            ),
            "source": "signal_card_history",
        }

    except Exception:
        return None
    finally:
        if conn:
            conn.close()


# ── 统计工具函数 ─────────────────────────────────────────────────────────────

def _hours_between(ts1: str, ts2: str) -> Optional[float]:
    try:
        from datetime import datetime
        fmt = "%Y-%m-%d %H:%M:%S"
        if "T" in ts1:
            ts1 = ts1.replace("T", " ")[:19]
        if "T" in ts2:
            ts2 = ts2.replace("T", " ")[:19]
        dt1 = datetime.strptime(ts1[:19], fmt)
        dt2 = datetime.strptime(ts2[:19], fmt)
        return (dt2 - dt1).total_seconds() / 3600
    except Exception:
        return None


def _sharpe_ratio(returns: List[float], annualize: bool = True) -> float:
    if len(returns) < 2:
        return 0.0
    import math
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
    rf_per_trade = 0.01
    sharpe = (mean_r - rf_per_trade) / std_r
    if annualize:
        sharpe *= math.sqrt(365)
    return sharpe


def _sortino_ratio(returns: List[float], annualize: bool = True) -> float:
    if len(returns) < 2:
        return 0.0
    import math
    mean_r = sum(returns) / len(returns)
    target = 0.0
    downside = [min(r - target, 0) ** 2 for r in returns]
    downside_var = sum(downside) / len(downside)
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 1e-10
    sortino = (mean_r - 0.01) / downside_std
    if annualize:
        sortino *= math.sqrt(365)
    return sortino


def _max_drawdown(returns: List[float]) -> float:
    if not returns:
        return 0.0
    cumulative = 100.0
    peak = cumulative
    max_dd = 0.0
    for r in returns:
        cumulative *= (1 + r / 100)
        if cumulative > peak:
            peak = cumulative
        dd = (cumulative - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return max_dd


def _test_significance(returns: List[float]) -> Optional[Dict[str, Any]]:
    if len(returns) < 5:
        return None
    try:
        from app.signals.math_engine import statistical_significance
        result = statistical_significance(returns)
        return {
            "z_score": result.z_score,
            "p_value": result.p_value,
            "is_significant": result.is_significant,
            "effect_size": result.effect_size,
            "interpretation": result.interpretation,
        }
    except Exception:
        return None
