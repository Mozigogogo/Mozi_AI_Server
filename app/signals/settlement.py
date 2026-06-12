"""
信号卡结算引擎 — 用真实价格验证预测

流程：
1. save_signal_card() — 生成时存库
2. settle_pending_cards() — 定时扫 pending 卡，拉真实K线逐根判断 TP/SL
3. 24h 内未触达 → expired

双模式：
- Railway 生产环境：直连 MySQL（同区域，低延迟）
- 本地开发：通过 SSH 隧道连远程数据代理（绕过安全组限制）

通过环境变量 USE_DATA_PROXY=true 切换模式
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from config.settings import settings

# ── 模式检测 ────────────────────────────────────────────────────────────────

_USE_PROXY = os.environ.get("USE_DATA_PROXY", "false").lower() == "true"


# ── 直连 MySQL 模式（Railway 生产环境）──────────────────────────────────────

def _get_conn():
    import pymysql
    import os as _os
    # 调试：列出所有 MYSQL 相关环境变量
    _mysql_env = {k: v[:15] + "..." if "PASS" in k else v
                  for k, v in _os.environ.items() if "MYSQL" in k.upper()}
    print(f"[settlement DEBUG] MYSQL env vars: {_mysql_env}")
    host = _os.environ.get("BIGORDER_MYSQL_HOST") or settings.bigorder_mysql_host or settings.mysql_host
    port = int(_os.environ.get("BIGORDER_MYSQL_PORT") or 0) or settings.bigorder_mysql_port or settings.mysql_port
    user = _os.environ.get("BIGORDER_MYSQL_USER") or settings.bigorder_mysql_user or settings.mysql_user
    pwd = _os.environ.get("BIGORDER_MYSQL_PASSWORD") or settings.bigorder_mysql_password or settings.mysql_password
    db = _os.environ.get("BIGORDER_MYSQL_DATABASE") or settings.bigorder_mysql_database or settings.mysql_database
    print(f"[settlement] MySQL连接: {host}:{port}/{db}")
    return pymysql.connect(
        host=host, port=port, user=user, password=pwd, database=db,
        charset="utf8mb4", connect_timeout=5, read_timeout=15,
    )


# ── SSH 隧道代理模式（本地开发）─────────────────────────────────────────────

_PROXY = None
_SSH_TUNNEL = None
_TUNNEL_LOCK = None


def _ensure_tunnel():
    global _SSH_TUNNEL, _TUNNEL_LOCK
    if _TUNNEL_LOCK is None:
        _TUNNEL_LOCK = __import__("threading").Lock()
    with _TUNNEL_LOCK:
        if _SSH_TUNNEL is not None and _SSH_TUNNEL.get("alive", False):
            return
        try:
            import paramiko
            import socket
            import select
            import threading
            import subprocess

            server_host = settings.data_proxy_url.replace("http://", "").split(":")[0]
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_host, username="root", password="7#Q9-nGk", timeout=10)
            transport = ssh.get_transport()

            LOCAL_PORT = 18001
            try:
                pids = subprocess.check_output(["lsof", "-ti", f":{LOCAL_PORT}"]).decode().strip()
                for pid in pids.split():
                    subprocess.run(["kill", "-9", pid], capture_output=True)
            except Exception:
                pass
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", LOCAL_PORT))
            server.listen(5)
            server.settimeout(1)

            stop_event = threading.Event()

            def _forwarder():
                while not stop_event.is_set():
                    try:
                        client, _ = server.accept()
                    except socket.timeout:
                        continue
                    except Exception:
                        break
                    try:
                        chan = transport.open_channel("direct-tcpip", ("127.0.0.1", 8001), client.getpeername())
                    except Exception:
                        client.close()
                        continue

                    def _relay(c, ch):
                        try:
                            c.setblocking(False)
                            ch.setblocking(False)
                            while True:
                                r, _, _ = select.select([c, ch], [], [], 30)
                                if not r:
                                    break
                                if c in r:
                                    data = c.recv(131072)
                                    if not data:
                                        break
                                    ch.sendall(data)
                                if ch in r:
                                    data = ch.recv(131072)
                                    if not data:
                                        break
                                    c.sendall(data)
                        except Exception:
                            pass
                        finally:
                            c.close()
                            ch.close()

                    threading.Thread(target=_relay, args=(client, chan), daemon=True).start()

            t = threading.Thread(target=_forwarder, daemon=True)
            t.start()

            _SSH_TUNNEL = {"ssh": ssh, "transport": transport, "server": server,
                           "thread": t, "stop": stop_event, "alive": True}
            print(f"SSH隧道已建立: localhost:{LOCAL_PORT} -> remote:8001")
        except Exception as e:
            print(f"SSH隧道建立失败: {e}，将使用本地文件兜底")
            _SSH_TUNNEL = None


def _proxy_get(path: str, params: dict = None) -> dict:
    import requests
    try:
        _ensure_tunnel()
    except Exception:
        pass
    params = params or {}
    params["key"] = settings.data_proxy_key
    try:
        resp = requests.get("http://127.0.0.1:18001" + path, params=params,
                            timeout=60, proxies={"http": None, "https": None})
        return resp.json()
    except Exception as e:
        global _SSH_TUNNEL
        if _SSH_TUNNEL:
            _SSH_TUNNEL["alive"] = False
        return {"ok": False, "error": str(e)}


def _proxy_post(path: str, data: dict) -> dict:
    import requests
    try:
        _ensure_tunnel()
    except Exception:
        pass
    try:
        resp = requests.post("http://127.0.0.1:18001" + path, params={"key": settings.data_proxy_key},
                             json=data, timeout=60, proxies={"http": None, "https": None})
        return resp.json()
    except Exception as e:
        global _SSH_TUNNEL
        if _SSH_TUNNEL:
            _SSH_TUNNEL["alive"] = False
        return {"ok": False, "error": str(e)}


# ── 统一接口（根据模式自动选择）───────────────────────────────────────────────

def save_signal_card(card) -> Optional[int]:
    if _USE_PROXY:
        return _save_signal_card_proxy(card)
    return _save_signal_card_direct(card)


def _save_signal_card_direct(card) -> Optional[int]:
    from app.signals.models import SignalGrade
    if card.grade == SignalGrade.C:
        return None
    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        sources_json = json.dumps(
            [{"name": s.name, "score": s.score, "direction": s.direction.value, "detail": s.detail}
             for s in card.sources], ensure_ascii=False)
        math_json = json.dumps(card.math.model_dump(), ensure_ascii=False) if card.math else None
        weights_json = json.dumps(card.strategy.adaptive_weights) if card.strategy and card.strategy.adaptive_weights else None

        cursor.execute(
            """INSERT INTO signal_card_history
            (coin, direction, grade, entry_low, entry_high, stop_loss, take_profit,
             current_price, invalidation_price, confidence, risk_reward_ratio, position_pct,
             sources_json, math_json, strategy_version, regime, adaptive_weights_json, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (card.coin, card.direction.value, card.grade.value, card.entry_low, card.entry_high,
             card.stop_loss, card.take_profit, card.current_price, card.invalidation_price,
             card.confidence, card.risk_reward_ratio, card.position_pct,
             sources_json, math_json,
             card.strategy.strategy_version if card.strategy else 1,
             card.strategy.regime if card.strategy else "quiet",
             weights_json, "pending"))
        conn.commit()
        rid = cursor.lastrowid
        cursor.close()
        return rid
    except Exception as e:
        print(f"信号卡存库失败: {e}")
        return None
    finally:
        if conn:
            conn.close()


def _save_signal_card_proxy(card) -> Optional[int]:
    from app.signals.models import SignalGrade
    if card.grade == SignalGrade.C:
        return None
    math_json = json.dumps(card.math.model_dump(), ensure_ascii=False) if card.math else None
    weights_json = json.dumps(card.strategy.adaptive_weights) if card.strategy and card.strategy.adaptive_weights else None
    payload = {
        "coin": card.coin, "direction": card.direction.value, "grade": card.grade.value,
        "entry_low": card.entry_low, "entry_high": card.entry_high,
        "stop_loss": card.stop_loss, "take_profit": card.take_profit,
        "current_price": card.current_price, "invalidation_price": card.invalidation_price,
        "confidence": card.confidence, "risk_reward_ratio": card.risk_reward_ratio,
        "position_pct": card.position_pct,
        "sources_json": json.dumps([{"name": s.name, "score": s.score, "direction": s.direction.value, "detail": s.detail}
                                     for s in card.sources], ensure_ascii=False),
        "math_json": math_json, "strategy_version": card.strategy.strategy_version if card.strategy else 1,
        "regime": card.strategy.regime if card.strategy else "quiet", "adaptive_weights_json": weights_json,
    }
    result = _proxy_post("/api/save_signal_card", payload)
    if result.get("ok"):
        return result.get("id")
    print(f"信号卡存库(代理)失败: {result.get('error')}")
    return None


def settle_pending_cards() -> Dict[str, int]:
    if _USE_PROXY:
        return _settle_pending_proxy()
    return _settle_pending_direct()


def _settle_pending_direct() -> Dict[str, int]:
    conn = None
    try:
        conn = _get_conn()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            """SELECT id, coin, direction, stop_loss, take_profit, current_price,
                      confidence, created_at
               FROM signal_card_history
               WHERE status = 'pending' AND created_at <= DATE_SUB(NOW(), INTERVAL 1 HOUR)
               ORDER BY created_at ASC LIMIT 100""")
        pending = cursor.fetchall()
        cursor.close()
        if not pending:
            return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        stats = {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}
        for card_row in pending:
            result = _settle_one_direct(conn, card_row)
            if result:
                status, pnl, coin = result
                stats[status] = stats.get(status, 0) + 1
                stats["settled"] += 1
                try:
                    from app.signals.adaptive_strategy import get_strategy_engine
                    get_strategy_engine().update_coin_winrate(coin, pnl, status)
                except Exception:
                    pass
        return stats
    except Exception as e:
        print(f"结算任务异常: {e}")
        return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}
    finally:
        if conn:
            conn.close()


def _settle_one_direct(conn, card_row: dict) -> Optional[tuple]:
    card_id = card_row["id"]
    coin = card_row["coin"]
    direction = card_row["direction"]
    stop_loss = float(card_row["stop_loss"])
    take_profit = float(card_row["take_profit"])
    entry_price = float(card_row["current_price"])
    created_at = card_row["created_at"]

    klines = _fetch_hourly_klines(coin, created_at)
    if not klines:
        cutoff = created_at + timedelta(hours=24)
        if datetime.now() > cutoff:
            _update_status_direct(conn, card_id, "expired", entry_price, 0.0)
            return ("expired", 0.0, coin)
        return None

    is_long = direction == "long"
    for bar in klines:
        bar_high, bar_low, bar_time = bar["high"], bar["low"], bar["time"]
        if bar_time > created_at + timedelta(hours=24):
            break
        if is_long:
            if bar_high >= take_profit:
                pnl = (take_profit - entry_price) / entry_price * 100
                _update_status_direct(conn, card_id, "hit_tp", take_profit, round(pnl, 4))
                return ("hit_tp", round(pnl, 4), coin)
            if bar_low <= stop_loss:
                pnl = (stop_loss - entry_price) / entry_price * 100
                _update_status_direct(conn, card_id, "hit_sl", stop_loss, round(pnl, 4))
                return ("hit_sl", round(pnl, 4), coin)
        else:
            if bar_low <= take_profit:
                pnl = (entry_price - take_profit) / entry_price * 100
                _update_status_direct(conn, card_id, "hit_tp", take_profit, round(pnl, 4))
                return ("hit_tp", round(pnl, 4), coin)
            if bar_high >= stop_loss:
                pnl = (entry_price - stop_loss) / entry_price * 100
                _update_status_direct(conn, card_id, "hit_sl", stop_loss, round(pnl, 4))
                return ("hit_sl", round(pnl, 4), coin)

    cutoff = created_at + timedelta(hours=24)
    if datetime.now() > cutoff:
        last_close = klines[-1]["close"] if klines else entry_price
        pnl = ((last_close - entry_price) if is_long else (entry_price - last_close)) / entry_price * 100
        _update_status_direct(conn, card_id, "expired", last_close, round(pnl, 4))
        return ("expired", round(pnl, 4), coin)
    return None


def _update_status_direct(conn, card_id: int, status: str, settled_price: float, pnl_pct: float):
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE signal_card_history SET status=%s, settled_price=%s, pnl_pct=%s, settled_at=NOW() WHERE id=%s",
            (status, settled_price, pnl_pct, card_id))
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"更新信号卡状态失败(id={card_id}): {e}")


def _settle_pending_proxy() -> Dict[str, int]:
    try:
        resp = _proxy_get("/api/pending_cards", {"limit": 100})
        if not resp.get("ok"):
            return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}
        pending = resp.get("cards", [])
        if not pending:
            return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        stats = {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}
        for card_row in pending:
            card_id = card_row["id"]
            coin = card_row["coin"]
            direction = card_row["direction"]
            stop_loss = float(card_row["stop_loss"])
            take_profit = float(card_row["take_profit"])
            entry_price = float(card_row["current_price"])
            created_at_str = card_row.get("created_at", "")
            try:
                created_at = datetime.strptime(created_at_str, "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

            klines = _fetch_hourly_klines(coin, created_at)
            if not klines:
                if datetime.now() > created_at + timedelta(hours=24):
                    _proxy_post("/api/update_card_status", {"card_id": card_id, "status": "expired", "settled_price": entry_price, "pnl_pct": 0.0})
                    stats["expired"] += 1
                    stats["settled"] += 1
                continue

            is_long = direction == "long"
            settled = False
            for bar in klines:
                if bar["time"] > created_at + timedelta(hours=24):
                    break
                bh, bl = bar["high"], bar["low"]
                if is_long:
                    if bh >= take_profit:
                        pnl = round((take_profit - entry_price) / entry_price * 100, 4)
                        _proxy_post("/api/update_card_status", {"card_id": card_id, "status": "hit_tp", "settled_price": take_profit, "pnl_pct": pnl})
                        stats["hit_tp"] += 1; stats["settled"] += 1; settled = True; break
                    if bl <= stop_loss:
                        pnl = round((stop_loss - entry_price) / entry_price * 100, 4)
                        _proxy_post("/api/update_card_status", {"card_id": card_id, "status": "hit_sl", "settled_price": stop_loss, "pnl_pct": pnl})
                        stats["hit_sl"] += 1; stats["settled"] += 1; settled = True; break
                else:
                    if bl <= take_profit:
                        pnl = round((entry_price - take_profit) / entry_price * 100, 4)
                        _proxy_post("/api/update_card_status", {"card_id": card_id, "status": "hit_tp", "settled_price": take_profit, "pnl_pct": pnl})
                        stats["hit_tp"] += 1; stats["settled"] += 1; settled = True; break
                    if bh >= stop_loss:
                        pnl = round((entry_price - stop_loss) / entry_price * 100, 4)
                        _proxy_post("/api/update_card_status", {"card_id": card_id, "status": "hit_sl", "settled_price": stop_loss, "pnl_pct": pnl})
                        stats["hit_sl"] += 1; stats["settled"] += 1; settled = True; break

            if not settled and datetime.now() > created_at + timedelta(hours=24):
                last_close = klines[-1]["close"]
                pnl = round(((last_close - entry_price) if is_long else (entry_price - last_close)) / entry_price * 100, 4)
                _proxy_post("/api/update_card_status", {"card_id": card_id, "status": "expired", "settled_price": last_close, "pnl_pct": pnl})
                stats["expired"] += 1; stats["settled"] += 1

            if stats["settled"] > 0:
                try:
                    from app.signals.adaptive_strategy import get_strategy_engine
                    get_strategy_engine().update_coin_winrate(coin, pnl, "hit_tp" if stats["hit_tp"] else "hit_sl" if stats["hit_sl"] else "expired")
                except Exception:
                    pass
        return stats
    except Exception as e:
        print(f"结算(代理)异常: {e}")
        return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}


def _fetch_hourly_klines(coin: str, since: datetime) -> List[dict]:
    try:
        from app.services.data_service import get_kline_data
        raw = get_kline_data(coin, 1)
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
            if bar_time <= since:
                continue
            result.append({"time": bar_time, "open": float(bar[0]), "high": float(bar[1]),
                           "low": float(bar[2]), "close": float(bar[3])})
        return result
    except Exception as e:
        print(f"拉取K线失败({coin}): {e}")
        return []


def get_accumulated_winrate(coin: str = None, grade: str = None, days: int = 30) -> Optional[Dict[str, Any]]:
    if _USE_PROXY:
        result = _proxy_get("/api/winrate", {"coin": coin, "grade": grade, "days": days})
        if result.get("ok"):
            return result.get("data")
        return None

    conn = None
    try:
        conn = _get_conn()
        import pymysql.cursors
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        where_parts = ["status IN ('hit_tp', 'hit_sl', 'expired')", "created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"]
        params: list = [days]
        if coin:
            where_parts.append("coin = %s"); params.append(coin.upper())
        if grade:
            where_parts.append("grade = %s"); params.append(grade)
        where = " AND ".join(where_parts)
        cursor.execute(f"SELECT status, pnl_pct FROM signal_card_history WHERE {where} ORDER BY created_at DESC LIMIT 500", params)
        rows = cursor.fetchall()
        cursor.close()
        if not rows:
            return None
        hit_tp = sum(1 for r in rows if r["status"] == "hit_tp")
        hit_sl = sum(1 for r in rows if r["status"] == "hit_sl")
        expired = sum(1 for r in rows if r["status"] == "expired")
        total = len(rows)
        wins = hit_tp + sum(1 for r in rows if r["status"] == "expired" and (r.get("pnl_pct") or 0) > 0)
        pnls = [r.get("pnl_pct") or 0 for r in rows]
        return {"win_rate": round(wins / total * 100, 1), "sample_count": total,
                "hit_tp": hit_tp, "hit_sl": hit_sl, "expired": expired,
                "avg_profit_pct": round(sum(pnls) / len(pnls), 2)}
    except Exception as e:
        print(f"累加胜率查询失败: {e}")
        return None
    finally:
        if conn:
            conn.close()


# ── 扫描结果缓存 ──────────────────────────────────────────────────────────

_SCAN_CACHE_FILE = Path(__file__).parent / "scan_cache.json"

_CREATE_SCAN_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS scan_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    total_coins INT NOT NULL DEFAULT 0,
    signal_count INT NOT NULL DEFAULT 0,
    scan_time FLOAT NOT NULL DEFAULT 0,
    results_json MEDIUMTEXT,
    displays_json MEDIUMTEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def save_scan_batch(results: list, scan_time: float):
    signals = [r for r in results if r.signal_card is not None]
    total_coins = len(results)
    results_json = json.dumps([s.signal_card.model_dump() for s in signals], ensure_ascii=False)

    display_cards = []
    for s in signals:
        card = s.signal_card
        item = {
            "type": "signalCard",
            "data": {
                "displayText": card.format_card(),
                "kellyPct": round(card.math.kelly_fraction * 100, 1) if card.math else 12.5,
                "card": {
                    "coin": card.coin, "direction": card.direction.value, "grade": card.grade.value,
                    "confidence": card.confidence, "currentPrice": card.current_price,
                    "entryZone": [card.entry_low, card.entry_high],
                    "stopLoss": card.stop_loss, "takeProfit": card.take_profit,
                    "riskReward": card.risk_reward_ratio, "positionPct": round(card.position_pct),
                    "kellyPct": round(card.math.kelly_fraction * 100, 1) if card.math else 12.5,
                    "invalidation": card.invalidation_price,
                    "sources": [{"name": src.name, "score": round(src.score)} for src in card.sources],
                    "winRate": card.win_rate, "sampleCount": card.sample_count, "avgProfit": card.avg_profit_pct,
                },
            },
        }
        if card.math:
            item["data"]["math"] = {"hurst": card.math.hurst, "mcBullProb": card.math.monte_carlo_bull_prob,
                                    "volatility": card.math.vol_regime, "marketRegime": card.math.market_regime}
        if card.strategy:
            item["data"]["strategy"] = {"version": card.strategy.strategy_version, "regime": card.strategy.regime,
                                        "globalWinRate": card.strategy.global_win_rate}
        display_cards.append(item)

    displays_json = json.dumps(display_cards, ensure_ascii=False)

    # 写 MySQL（直连或代理）
    if _USE_PROXY:
        result = _proxy_post("/api/save_scan_batch", {
            "total_coins": total_coins, "signal_count": len(signals),
            "scan_time": round(scan_time, 1), "results_json": results_json, "displays_json": displays_json})
        if result.get("ok"):
            print(f"扫描结果已写入远程 scan_cache ({len(signals)} signals)")
        else:
            print(f"扫描结果写远程失败: {result.get('error')}")
    else:
        conn = None
        try:
            conn = _get_conn()
            cursor = conn.cursor()
            cursor.execute(_CREATE_SCAN_CACHE_TABLE)
            cursor.execute(
                "INSERT INTO scan_cache (total_coins, signal_count, scan_time, results_json, displays_json) VALUES (%s,%s,%s,%s,%s)",
                (total_coins, len(signals), round(scan_time, 1), results_json, displays_json))
            conn.commit()
            cursor.close()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM scan_cache WHERE id NOT IN (SELECT id FROM (SELECT id FROM scan_cache ORDER BY id DESC LIMIT 100) t)")
            conn.commit()
            cursor.close()
            print(f"扫描结果已写入 scan_cache ({len(signals)} signals)")
        except Exception as e:
            print(f"扫描结果存MySQL失败: {e}")
        finally:
            if conn:
                conn.close()

    # 始终写本地文件兜底
    try:
        cache = {"total_coins": total_coins, "signal_count": len(signals),
                 "scan_time": round(scan_time, 1), "signals": [s.signal_card.model_dump() for s in signals],
                 "displays": display_cards, "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        _SCAN_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"扫描结果写本地文件失败: {e}")


def get_latest_scan(max_age_seconds: int = 1800) -> Optional[Dict[str, Any]]:
    if _USE_PROXY:
        result = _proxy_get("/api/latest_scan", {"max_age_seconds": max_age_seconds})
        if result.get("ok") and result.get("data"):
            return result["data"]
    else:
        conn = None
        try:
            conn = _get_conn()
            import pymysql.cursors
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute(_CREATE_SCAN_CACHE_TABLE)
            cursor.execute("""SELECT total_coins, signal_count, scan_time, results_json, displays_json, created_at
                              FROM scan_cache ORDER BY id DESC LIMIT 1""")
            row = cursor.fetchone()
            cursor.close()
            if row:
                cached_at = row["created_at"]
                age = (datetime.now() - cached_at).total_seconds() if cached_at else 9999
                return {
                    "total_coins": row["total_coins"], "signal_count": row["signal_count"],
                    "scan_time": row["scan_time"],
                    "signals": json.loads(row["results_json"]) if row["results_json"] else [],
                    "displays": json.loads(row["displays_json"]) if row["displays_json"] else [],
                    "cached_at": cached_at.strftime("%Y-%m-%d %H:%M:%S") if cached_at else "",
                    "is_stale": age > max_age_seconds,
                }
        except Exception as e:
            print(f"读MySQL扫描缓存失败: {e}")
        finally:
            if conn:
                conn.close()

    # 兜底：读本地文件
    try:
        if _SCAN_CACHE_FILE.exists():
            cache = json.loads(_SCAN_CACHE_FILE.read_text())
            return {"total_coins": cache.get("total_coins", 0), "signal_count": cache.get("signal_count", 0),
                    "scan_time": cache.get("scan_time", 0), "signals": cache.get("signals", []),
                    "displays": cache.get("displays", []), "cached_at": cache.get("cached_at", ""),
                    "is_stale": True}
    except Exception:
        pass
    return None
