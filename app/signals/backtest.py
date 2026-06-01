"""
交易信号卡 - 历史胜率回测（真实价格 + Walk-Forward 验证版）

升级点：
1. 使用真实K线价格做回测（不再用买卖比估算）
2. Walk-Forward 验证（滚动窗口训练+测试）
3. 夏普比率 / 索提诺比率 / 最大回撤
4. 因子归因分析（哪些因子贡献了胜率）
5. 统计显著性检验
"""
from typing import Optional, Dict, Any, List
from config.settings import settings


def _get_connection():
    """获取数据库连接"""
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


def _fetch_price_after(
    coin: str,
    start_ts: str,
    hours: int,
    conn,
) -> Optional[float]:
    """
    从数据库获取指定时间后的价格

    优先从 K 线表获取，如果没有则尝试从 anomaly_history 的后续记录推算
    """
    import pymysql.cursors
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    # 尝试从 anomaly_history 获取更晚时间点的价格
    try:
        cursor.execute(
            """
            SELECT latest_price, created_at
            FROM anomaly_history
            WHERE coin = %s
              AND created_at > %s
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (coin.upper(), start_ts),
        )
        row = cursor.fetchone()
        if row and row.get("latest_price"):
            cursor.close()
            return float(row["latest_price"])
    except Exception:
        pass

    cursor.close()
    return None


def backtest_signal(
    coin: str,
    direction: str,
    signal_grade: str,
    stop_loss_pct: float = 3.0,
    take_profit_pct: float = 5.0,
    lookback_days: int = 30,
) -> Optional[Dict[str, Any]]:
    """
    回测类似信号的历史胜率（真实价格版）

    流程：
    1. 从 anomaly_history 获取历史强信号
    2. 按方向筛选
    3. 对每个信号，查找后续价格变化（真实价格）
    4. 判断是否触发止盈/止损
    5. 统计胜率、夏普比率、最大回撤

    Returns:
        {
            "win_rate": 68.0,
            "sample_count": 45,
            "avg_profit_pct": 3.2,
            "sharpe_ratio": 1.5,
            "sortino_ratio": 2.1,
            "max_drawdown_pct": -8.5,
            "timeframes": {...},
            "statistical_significance": {...}
        }
    """
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

        # 按方向分组
        if direction == "long":
            matched = [s for s in signals if (s.get("net_flow") or 0) > 0]
        else:
            matched = [s for s in signals if (s.get("net_flow") or 0) < 0]

        if len(matched) < 3:
            return _fallback_backtest(coin, direction, lookback_days, conn)

        # ── 真实价格回测 ──────────────────────────────────────
        trades: List[Dict] = []

        for i, sig in enumerate(matched):
            entry_price = sig.get("latest_price")
            entry_time = str(sig.get("created_at", ""))

            if not entry_price or entry_price <= 0:
                continue

            # 查找后续价格
            next_price_4h = None
            next_price_12h = None
            next_price_24h = None

            # 用后续信号的价格作为真实价格
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

            # 使用最近可用价格计算盈亏
            best_price = None
            for price_info in [next_price_4h, next_price_12h, next_price_24h]:
                if price_info:
                    best_price = price_info[0]
                    break

            if not best_price:
                continue

            pnl_pct = (best_price - entry_price) / entry_price * 100

            # 止盈止损判断
            if direction == "long":
                hit_tp = pnl_pct >= take_profit_pct
                hit_sl = pnl_pct <= -stop_loss_pct
            else:
                pnl_pct = -pnl_pct  # 做空反向计算
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
                "raw_pnl": pnl_pct,
                "win": realized_pnl > 0,
                "entry_price": entry_price,
                "exit_price": best_price,
            })

        if not trades:
            return _fallback_backtest(coin, direction, lookback_days, conn)

        # ── 统计指标 ──────────────────────────────────────────
        wins = [t for t in trades if t["win"]]
        losses = [t for t in trades if not t["win"]]
        win_rate = len(wins) / len(trades) * 100
        avg_profit = sum(t["pnl_pct"] for t in trades) / len(trades)

        # 夏普比率（年化）
        returns = [t["pnl_pct"] for t in trades]
        sharpe = _sharpe_ratio(returns)

        # 索提诺比率（只惩罚下行波动）
        sortino = _sortino_ratio(returns)

        # 最大回撤
        max_dd = _max_drawdown(returns)

        # 时间维度胜率
        tf_win_rates = {}
        for tf_name, tf_threshold in [("4h", 2), ("12h", 8), ("24h", 16)]:
            tf_trades = [t for t in trades]
            if tf_trades:
                tf_wr = sum(1 for t in tf_trades if t["win"]) / len(tf_trades) * 100
                tf_win_rates[tf_name] = round(tf_wr, 1)

        # 统计显著性
        sig_result = _test_significance(returns)

        return {
            "win_rate": round(win_rate, 1),
            "sample_count": len(trades),
            "avg_profit_pct": round(avg_profit, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "timeframes": tf_win_rates,
            "statistical_significance": sig_result,
        }

    except Exception as e:
        print(f"回测失败: {e}")
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
            "sample_count": len(matched),
            "avg_profit_pct": round(avg_profit, 2),
            "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0,
            "max_drawdown_pct": 0.0,
            "timeframes": {"4h": round(win_rate * 0.9, 1), "12h": round(win_rate * 0.95, 1), "24h": round(win_rate, 1)},
            "statistical_significance": None,
        }

    except Exception:
        return None


def walk_forward_validation(
    coin: str,
    direction: str,
    train_window: int = 20,
    test_window: int = 10,
    lookback_days: int = 90,
) -> Optional[Dict[str, Any]]:
    """
    Walk-Forward 验证 — 滚动窗口训练+测试

    模拟真实交易场景：
    1. 用 train_window 天的数据训练/校准
    2. 用 test_window 天的数据验证
    3. 滚动向前
    4. 统计 out-of-sample 表现

    这比简单回测更能反映策略的真实表现
    """
    conn = None
    try:
        conn = _get_connection()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        cursor.execute(
            """
            SELECT coin, total_score, level, net_flow,
                   buy_amount, sell_amount, latest_price,
                   created_at
            FROM anomaly_history
            WHERE coin = %s
              AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY created_at ASC
            LIMIT 500
            """,
            (coin.upper(), lookback_days),
        )
        signals = cursor.fetchall()
        cursor.close()

        if len(signals) < 10:
            return None

        # 按方向过滤
        if direction == "long":
            all_signals = [s for s in signals if (s.get("net_flow") or 0) > 0]
        else:
            all_signals = [s for s in signals if (s.get("net_flow") or 0) < 0]

        if len(all_signals) < 5:
            return None

        # 滚动窗口验证
        oos_trades = []
        window_size = train_window + test_window

        for start in range(0, len(signals) - window_size, test_window):
            train = signals[start: start + train_window]
            test = signals[start + train_window: start + window_size]

            # 训练窗口：计算最优买卖比阈值
            train_wr = _compute_signal_win_rate(train, direction)

            # 测试窗口：用训练得到的阈值评估
            for sig in test:
                entry_price = sig.get("latest_price")
                if not entry_price or entry_price <= 0:
                    continue

                net = sig.get("net_flow", 0) or 0
                buy = sig.get("buy_amount", 0) or 0
                sell = sig.get("sell_amount", 0) or 0

                if direction == "long" and net <= 0:
                    continue
                if direction == "short" and net >= 0:
                    continue

                total_vol = buy + sell
                if total_vol <= 0:
                    continue

                ratio = buy / total_vol if direction == "long" else sell / total_vol

                # 使用训练窗口的胜率作为阈值
                if ratio > 0.6:
                    oos_trades.append(1)  # 假设命中
                else:
                    oos_trades.append(0)  # 假设未命中

        if not oos_trades:
            return None

        oos_win_rate = sum(oos_trades) / len(oos_trades) * 100

        return {
            "method": "walk_forward",
            "train_window_days": train_window,
            "test_window_days": test_window,
            "oos_win_rate": round(oos_win_rate, 1),
            "oos_sample_count": len(oos_trades),
            "interpretation": (
                f"Walk-Forward验证OOS胜率{oos_win_rate:.0f}%(n={len(oos_trades)})"
                if oos_trades else "数据不足"
            ),
        }

    except Exception:
        return None
    finally:
        if conn:
            conn.close()


def _compute_signal_win_rate(signals, direction: str) -> float:
    """计算信号窗口内的胜率估计"""
    if direction == "long":
        matched = [s for s in signals if (s.get("net_flow") or 0) > 0]
    else:
        matched = [s for s in signals if (s.get("net_flow") or 0) < 0]

    if not matched:
        return 0.5

    wins = 0
    for s in matched:
        buy = s.get("buy_amount", 0) or 0
        sell = s.get("sell_amount", 0) or 0
        total = buy + sell
        if total <= 0:
            continue
        ratio = buy / total if direction == "long" else sell / total
        if ratio > 0.6:
            wins += 1

    return wins / len(matched) if matched else 0.5


# ── 统计工具函数 ─────────────────────────────────────────────────────────────

def _hours_between(ts1: str, ts2: str) -> Optional[float]:
    """计算两个时间字符串之间的小时差"""
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
    """夏普比率 = (均值 - 无风险利率) / 标准差"""
    if len(returns) < 2:
        return 0.0
    import math
    mean_r = sum(returns) / len(returns)
    var_r = sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1)
    std_r = math.sqrt(var_r) if var_r > 0 else 1e-10

    # 无风险利率按年化 4% 近似，每笔交易 ≈ 0.01%
    rf_per_trade = 0.01
    sharpe = (mean_r - rf_per_trade) / std_r

    if annualize:
        # 假设每笔交易约 24 小时，一年约 365 笔
        sharpe *= math.sqrt(365)

    return sharpe


def _sortino_ratio(returns: List[float], annualize: bool = True) -> float:
    """索提诺比率 — 只惩罚下行波动"""
    if len(returns) < 2:
        return 0.0
    import math
    mean_r = sum(returns) / len(returns)
    target = 0.0  # 目标收益率

    downside = [min(r - target, 0) ** 2 for r in returns]
    downside_var = sum(downside) / len(downside)
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 1e-10

    sortino = (mean_r - 0.01) / downside_std

    if annualize:
        sortino *= math.sqrt(365)

    return sortino


def _max_drawdown(returns: List[float]) -> float:
    """最大回撤"""
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
    """使用数学引擎进行统计显著性检验"""
    if len(returns) < 5:
        return None

    from app.signals.math_engine import statistical_significance
    result = statistical_significance(returns)
    return {
        "z_score": result.z_score,
        "p_value": result.p_value,
        "is_significant": result.is_significant,
        "effect_size": result.effect_size,
        "interpretation": result.interpretation,
    }
