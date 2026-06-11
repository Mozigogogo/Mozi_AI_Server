"""
信号卡结算引擎 — 用真实价格验证预测

流程：
1. save_signal_card() — 生成时存库
2. settle_pending_cards() — 定时扫 pending 卡，拉真实K线逐根判断 TP/SL
3. 24h 内未触达 → expired

MySQL 操作通过远程数据代理（新加坡服务器）执行，本地 scan_cache.json 兜底
使用 paramiko SSH 隧道连接，自动重连
"""
import json
import math
import requests
import threading
import time
import socket
import select
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

from config.settings import settings

_PROXY = None
_SSH_TUNNEL = None
_TUNNEL_LOCK = threading.Lock()


def _ensure_tunnel():
    """确保 paramiko SSH 隧道存活"""
    global _SSH_TUNNEL
    with _TUNNEL_LOCK:
        if _SSH_TUNNEL is not None and _SSH_TUNNEL.get("alive", False):
            return
        try:
            import paramiko
            server_host = settings.data_proxy_url.replace("http://", "").split(":")[0]
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(server_host, username="root", password="7#Q9-nGk", timeout=10)

            transport = ssh.get_transport()

            # 启动本地转发服务
            LOCAL_PORT = 18001
            # 先释放被占用的端口
            import subprocess
            subprocess.run(["lsof", "-ti", f":{LOCAL_PORT}"], capture_output=True)
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

            _SSH_TUNNEL = {
                "ssh": ssh,
                "transport": transport,
                "server": server,
                "thread": t,
                "stop": stop_event,
                "alive": True,
            }
            print(f"SSH隧道已建立: localhost:{LOCAL_PORT} -> remote:8001")
        except Exception as e:
            print(f"SSH隧道建立失败: {e}，将使用本地文件兜底")
            _SSH_TUNNEL = None


def _get_proxy():
    """获取代理配置（通过 SSH 隧道）"""
    global _PROXY
    if _PROXY is None:
        _PROXY = {
            "base_url": "http://127.0.0.1:18001",
            "key": settings.data_proxy_key,
            "timeout": 60,
            "proxies": {"http": None, "https": None},
        }
    return _PROXY


def _proxy_get(path: str, params: dict = None) -> dict:
    """GET 远程代理（通过 SSH 隧道）"""
    try:
        _ensure_tunnel()
    except Exception:
        pass
    p = _get_proxy()
    params = params or {}
    params["key"] = p["key"]
    try:
        resp = requests.get(f"{p['base_url']}{path}", params=params, timeout=p["timeout"], proxies=p["proxies"])
        return resp.json()
    except Exception as e:
        global _SSH_TUNNEL
        if _SSH_TUNNEL:
            _SSH_TUNNEL["alive"] = False
        return {"ok": False, "error": str(e)}


def _proxy_post(path: str, data: dict) -> dict:
    """POST 远程代理（通过 SSH 隧道）"""
    try:
        _ensure_tunnel()
    except Exception:
        pass
    p = _get_proxy()
    try:
        resp = requests.post(f"{p['base_url']}{path}", params={"key": p["key"]}, json=data, timeout=p["timeout"], proxies=p["proxies"])
        return resp.json()
    except Exception as e:
        global _SSH_TUNNEL
        if _SSH_TUNNEL:
            _SSH_TUNNEL["alive"] = False
        return {"ok": False, "error": str(e)}


def save_signal_card(card) -> Optional[int]:
    """
    信号卡生成时持久化（通过远程代理写 MySQL）
    """
    from app.signals.models import SignalGrade
    if card.grade == SignalGrade.C:
        return None  # C 级卡不参与结算

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

    payload = {
        "coin": card.coin,
        "direction": card.direction.value,
        "grade": card.grade.value,
        "entry_low": card.entry_low,
        "entry_high": card.entry_high,
        "stop_loss": card.stop_loss,
        "take_profit": card.take_profit,
        "current_price": card.current_price,
        "invalidation_price": card.invalidation_price,
        "confidence": card.confidence,
        "risk_reward_ratio": card.risk_reward_ratio,
        "position_pct": card.position_pct,
        "sources_json": sources_json,
        "math_json": math_json,
        "strategy_version": card.strategy.strategy_version if card.strategy else 1,
        "regime": card.strategy.regime if card.strategy else "quiet",
        "adaptive_weights_json": weights_json,
    }

    result = _proxy_post("/api/save_signal_card", payload)
    if result.get("ok"):
        return result.get("id")
    print(f"信号卡存库失败: {result.get('error')}")
    return None


def settle_pending_cards() -> Dict[str, int]:
    """
    扫描所有 pending 的信号卡，用真实 K 线结算

    结算逻辑：
    1. 从远程代理获取 pending 卡列表
    2. 拉真实K线逐根判断 TP/SL
    3. 通过远程代理更新状态
    """
    try:
        resp = _proxy_get("/api/pending_cards", {"limit": 100})
        if not resp.get("ok"):
            print(f"获取pending卡失败: {resp.get('error')}")
            return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        pending = resp.get("cards", [])
        if not pending:
            return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        stats = {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}

        for card_row in pending:
            result = _settle_one(card_row)
            if result:
                status, pnl, coin = result
                stats[status] = stats.get(status, 0) + 1
                stats["settled"] += 1
                # 同步到本地 strategy_state.json
                try:
                    from app.signals.adaptive_strategy import get_strategy_engine
                    get_strategy_engine().update_coin_winrate(coin, pnl, status)
                except Exception:
                    pass

        return stats

    except Exception as e:
        print(f"结算任务异常: {e}")
        return {"settled": 0, "hit_tp": 0, "hit_sl": 0, "expired": 0}


def _settle_one(card_row: dict) -> Optional[tuple]:
    """
    结算单张信号卡

    Returns:
        (status, pnl_pct, coin) or None
    """
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
        return None

    # 拉小时 K 线
    klines = _fetch_hourly_klines(coin, created_at)
    if not klines:
        cutoff = created_at + timedelta(hours=24)
        if datetime.now() > cutoff:
            _update_status_remote(card_id, "expired", entry_price, 0.0)
            return ("expired", 0.0, coin)
        return None

    is_long = direction == "long"

    for bar in klines:
        bar_high = bar["high"]
        bar_low = bar["low"]
        bar_time = bar["time"]

        if bar_time > created_at + timedelta(hours=24):
            break

        if is_long:
            if bar_high >= take_profit:
                pnl = (take_profit - entry_price) / entry_price * 100
                _update_status_remote(card_id, "hit_tp", take_profit, round(pnl, 4))
                return ("hit_tp", round(pnl, 4), coin)
            if bar_low <= stop_loss:
                pnl = (stop_loss - entry_price) / entry_price * 100
                _update_status_remote(card_id, "hit_sl", stop_loss, round(pnl, 4))
                return ("hit_sl", round(pnl, 4), coin)
        else:
            if bar_low <= take_profit:
                pnl = (entry_price - take_profit) / entry_price * 100
                _update_status_remote(card_id, "hit_tp", take_profit, round(pnl, 4))
                return ("hit_tp", round(pnl, 4), coin)
            if bar_high >= stop_loss:
                pnl = (entry_price - stop_loss) / entry_price * 100
                _update_status_remote(card_id, "hit_sl", stop_loss, round(pnl, 4))
                return ("hit_sl", round(pnl, 4), coin)

    cutoff = created_at + timedelta(hours=24)
    if datetime.now() > cutoff:
        last_close = klines[-1]["close"] if klines else entry_price
        if is_long:
            pnl = (last_close - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - last_close) / entry_price * 100
        _update_status_remote(card_id, "expired", last_close, round(pnl, 4))
        return ("expired", round(pnl, 4), coin)

    return None


def _update_status_remote(card_id: int, status: str, settled_price: float, pnl_pct: float):
    """通过远程代理更新信号卡状态"""
    _proxy_post("/api/update_card_status", {
        "card_id": card_id,
        "status": status,
        "settled_price": settled_price,
        "pnl_pct": pnl_pct,
    })


def _fetch_hourly_klines(coin: str, since: datetime) -> List[dict]:
    """获取指定时间之后的小时 K 线"""
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


def get_accumulated_winrate(coin: str = None, grade: str = None, days: int = 30) -> Optional[Dict[str, Any]]:
    """
    从 signal_card_history 累加计算历史胜率（通过远程代理）
    """
    params = {"coin": coin, "grade": grade, "days": days}
    result = _proxy_get("/api/winrate", params)
    if result.get("ok"):
        return result.get("data")
    return None


# ── 扫描结果缓存 ──────────────────────────────────────────────────────────

_SCAN_CACHE_FILE = Path(__file__).parent / "scan_cache.json"


def save_scan_batch(results: list, scan_time: float):
    """
    将一次扫描结果批量存库（远程代理 + 本地文件兜底）
    """
    signals = [r for r in results if r.signal_card is not None]
    total_coins = len(results)

    results_json = json.dumps(
        [s.signal_card.model_dump() for s in signals],
        ensure_ascii=False,
    )

    display_cards = []
    for s in signals:
        card = s.signal_card
        item = {
            "type": "signalCard",
            "data": {
                "displayText": card.format_card(),
                "kellyPct": round(card.math.kelly_fraction * 100, 1) if card.math else 12.5,
                "card": {
                    "coin": card.coin,
                    "direction": card.direction.value,
                    "grade": card.grade.value,
                    "confidence": card.confidence,
                    "currentPrice": card.current_price,
                    "entryZone": [card.entry_low, card.entry_high],
                    "stopLoss": card.stop_loss,
                    "takeProfit": card.take_profit,
                    "riskReward": card.risk_reward_ratio,
                    "positionPct": round(card.position_pct),
                    "kellyPct": round(card.math.kelly_fraction * 100, 1) if card.math else 12.5,
                    "invalidation": card.invalidation_price,
                    "sources": [
                        {"name": src.name, "score": round(src.score)}
                        for src in card.sources
                    ],
                    "winRate": card.win_rate,
                    "sampleCount": card.sample_count,
                    "avgProfit": card.avg_profit_pct,
                },
            },
        }
        if card.math:
            item["data"]["math"] = {
                "hurst": card.math.hurst,
                "mcBullProb": card.math.monte_carlo_bull_prob,
                "volatility": card.math.vol_regime,
                "marketRegime": card.math.market_regime,
            }
        if card.strategy:
            item["data"]["strategy"] = {
                "version": card.strategy.strategy_version,
                "regime": card.strategy.regime,
                "globalWinRate": card.strategy.global_win_rate,
            }
        display_cards.append(item)

    displays_json = json.dumps(display_cards, ensure_ascii=False)

    # 1. 尝试远程代理
    result = _proxy_post("/api/save_scan_batch", {
        "total_coins": total_coins,
        "signal_count": len(signals),
        "scan_time": round(scan_time, 1),
        "results_json": results_json,
        "displays_json": displays_json,
    })
    if result.get("ok"):
        print(f"扫描结果已写入远程 scan_cache ({len(signals)} signals)")
    else:
        print(f"扫描结果写远程失败: {result.get('error')}，本地文件兜底")

    # 2. 始终写本地文件兜底
    try:
        cache = {
            "total_coins": total_coins,
            "signal_count": len(signals),
            "scan_time": round(scan_time, 1),
            "signals": [s.signal_card.model_dump() for s in signals],
            "displays": display_cards,
            "cached_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _SCAN_CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"扫描结果写本地文件失败: {e}")


def get_latest_scan(max_age_seconds: int = 1800) -> Optional[Dict[str, Any]]:
    """
    获取最近一次扫描结果（远程代理优先，本地文件兜底）
    """
    # 1. 尝试远程代理
    result = _proxy_get("/api/latest_scan", {"max_age_seconds": max_age_seconds})
    if result.get("ok") and result.get("data"):
        return result["data"]

    # 2. 兜底：读本地文件
    try:
        if _SCAN_CACHE_FILE.exists():
            cache = json.loads(_SCAN_CACHE_FILE.read_text())
            return {
                "total_coins": cache.get("total_coins", 0),
                "signal_count": cache.get("signal_count", 0),
                "scan_time": cache.get("scan_time", 0),
                "signals": cache.get("signals", []),
                "displays": cache.get("displays", []),
                "cached_at": cache.get("cached_at", ""),
                "is_stale": True,
            }
    except Exception:
        pass

    return None
