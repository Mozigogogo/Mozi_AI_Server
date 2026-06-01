"""
信号卡结算引擎 — 用真实价格验证预测

流程：
1. save_signal_card() — 生成时存库
2. settle_pending_cards() — 定时扫 pending 卡，拉真实K线逐根判断 TP/SL
3. 24h 内未触达 → expired
"""
import json
import math
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

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


def save_signal_card(card) -> Optional[int]:
    """
    信号卡生成时持久化到 MySQL

    Args:
        card: SignalCard 实例

    Returns:
        插入的记录 ID
    """
    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()

        sources_json = json.dumps(
            [{"name": s.name, "score": s.score, "direction": s.direction.value, "detail": s.detail}
             for s in card.sources],
            ensure_ascii=False,
        )

        math_json = None
        if card.math:
            math_json = json.dumps(card.math.model_dump(), ensure_ascii=False)

        weights_json = None
        if card.strategy and card.strategy.adaptive_weights:
            weights_json = json.dumps(card.strategy.adaptive_weights)

        cursor.execute(
            """
            INSERT INTO signal_card_history
            (coin, direction, grade, entry_low, entry_high, stop_loss, take_profit,
             current_price, invalidation_price, confidence, risk_reward_ratio, position_pct,
             sources_json, math_json, strategy_version, regime, adaptive_weights_json, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                card.coin,
                card.direction.value,
                card.grade.value,
                card.entry_low,
                card.entry_high,
                card.stop_loss,
                card.take_profit,
                card.current_price,
                card.invalidation_price,
                card.confidence,
                card.risk_reward_ratio,
                card.position_pct,
                sources_json,
                math_json,
                card.strategy.strategy_version if card.strategy else 1,
                card.strategy.regime if card.strategy else "quiet",
                weights_json,
                "pending",
            ),
        )
        conn.commit()
        record_id = cursor.lastrowid
        cursor.close()
        return record_id

    except Exception as e:
        print(f"信号卡存库失败: {e}")
        return None
    finally:
        if conn:
            conn.close()


def settle_pending_cards() -> Dict[str, int]:
    """
    扫描所有 pending 的信号卡，用真实 K 线结算

    结算逻辑：
    1. 取卡片的 created_at ~ created_at+24h 的小时 K 线
    2. 逐根判断 high/low 是否穿越 TP/SL
    3. 多头：high >= TP → hit_tp，low <= SL → hit_sl
    4. 空头：low <= TP → hit_tp，high >= SL → hit_sl
    5. 24h 内都没触达 → expired

    Returns:
        {"settled": N, "hit_tp": N, "hit_sl": N, "expired": N}
    """
    conn = None
    try:
        conn = _get_conn()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 查所有 pending 且生成超过 1 小时的卡（给 K 线数据一点时间）
        cursor.execute(
            """
            SELECT id, coin, direction, stop_loss, take_profit, current_price,
                   confidence, created_at
            FROM signal_card_history
            WHERE status = 'pending'
              AND created_at <= DATE_SUB(NOW(), INTERVAL 1 HOUR)
            ORDER BY created_at ASC
            LIMIT 100
            """
        )
        pending = cursor.fetchall()
        cursor.close()

        if not pending:
            return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        stats = {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        for card_row in pending:
            result = _settle_one(conn, card_row)
            if result:
                stats[result] = stats.get(result, 0) + 1
                stats["settled"] += 1

        return stats

    except Exception as e:
        print(f"结算任务异常: {e}")
        return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}
    finally:
        if conn:
            conn.close()


def _settle_one(conn, card_row: dict) -> Optional[str]:
    """
    结算单张信号卡

    Returns:
        "hit_tp" / "hit_sl" / "expired" / None
    """
    card_id = card_row["id"]
    coin = card_row["coin"]
    direction = card_row["direction"]
    stop_loss = float(card_row["stop_loss"])
    take_profit = float(card_row["take_profit"])
    entry_price = float(card_row["current_price"])
    created_at = card_row["created_at"]

    # 拉小时 K 线
    klines = _fetch_hourly_klines(coin, created_at)
    if not klines:
        # 无 K 线数据，如果超过 24h 则标记 expired
        cutoff = created_at + timedelta(hours=24)
        if datetime.now() > cutoff:
            _update_status(conn, card_id, "expired", entry_price, 0.0)
            return "expired"
        return None

    is_long = direction == "long"
    settled = False

    for bar in klines:
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_close = bar["close"]
        bar_time = bar["time"]

        # 检查是否超过 24h 有效期
        if bar_time > created_at + timedelta(hours=24):
            break

        if is_long:
            if bar_high >= take_profit:
                pnl = (take_profit - entry_price) / entry_price * 100
                _update_status(conn, card_id, "hit_tp", take_profit, round(pnl, 4))
                return "hit_tp"
            if bar_low <= stop_loss:
                pnl = (stop_loss - entry_price) / entry_price * 100
                _update_status(conn, card_id, "hit_sl", stop_loss, round(pnl, 4))
                return "hit_sl"
        else:
            if bar_low <= take_profit:
                pnl = (entry_price - take_profit) / entry_price * 100
                _update_status(conn, card_id, "hit_tp", take_profit, round(pnl, 4))
                return "hit_tp"
            if bar_high >= stop_loss:
                pnl = (entry_price - stop_loss) / entry_price * 100
                _update_status(conn, card_id, "hit_sl", stop_loss, round(pnl, 4))
                return "hit_sl"

    # K 线走完了但没触达 TP/SL
    # 如果距生成已超过 24h → expired
    cutoff = created_at + timedelta(hours=24)
    if datetime.now() > cutoff:
        last_close = klines[-1]["close"] if klines else entry_price
        if is_long:
            pnl = (last_close - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - last_close) / entry_price * 100
        _update_status(conn, card_id, "expired", last_close, round(pnl, 4))
        return "expired"

    return None


def _fetch_hourly_klines(coin: str, since: datetime) -> List[dict]:
    """
    获取指定时间之后的小时 K 线

    Returns:
        [{"time": datetime, "open": float, "high": float, "low": float, "close": float}, ...]
    """
    try:
        from app.services.data_service import get_kline_data
        raw = get_kline_data(coin, 1)  # 小时 K 线
        if not raw or not isinstance(raw, dict):
            return []

        values = raw.get("values", [])
        dates = raw.get("categoryData", [])

        result = []
        for i, bar in enumerate(values):
            if not isinstance(bar, (list, tuple)) or len(bar) < 4:
                continue

            dt_str = dates[i] if i < len(dates) else ""
            try:
                bar_time = datetime.strptime(dt_str, "%Y/%m/%d %H:%M")
            except (ValueError, TypeError):
                continue

            # 只取生成时间之后的 K 线
            if bar_time <= since:
                continue

            result.append({
                "time": bar_time,
                "open": float(bar[0]),
                "high": float(bar[1]),
                "low": float(bar[2]),
                "close": float(bar[3]),
            })

        return result

    except Exception as e:
        print(f"拉取K线失败({coin}): {e}")
        return []


def _update_status(conn, card_id: int, status: str, settled_price: float, pnl_pct: float):
    """更新信号卡状态"""
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE signal_card_history
            SET status = %s, settled_price = %s, pnl_pct = %s, settled_at = NOW()
            WHERE id = %s
            """,
            (status, settled_price, pnl_pct, card_id),
        )
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"更新信号卡状态失败(id={card_id}): {e}")
