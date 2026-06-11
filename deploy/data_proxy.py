"""
远程数据代理 API — 部署在新加坡服务器，代理 MySQL 读写

启动: DATA_PROXY_KEY=xxx python3 -c "import uvicorn; from data_proxy import app; uvicorn.run(app, host='0.0.0.0', port=8001)"

或: uvicorn data_proxy:app --host 0.0.0.0 --port 8001
"""
import json
import os
from typing import Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Signal Data Proxy", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# API Key 认证
API_KEY = os.environ.get("DATA_PROXY_KEY", "signal_proxy_2026")

# MySQL 配置（直接读环境变量，不依赖项目 settings）
_MYSQL = {
    "host": os.environ.get("BIGORDER_MYSQL_HOST", os.environ.get("MYSQL_HOST", "localhost")),
    "port": int(os.environ.get("BIGORDER_MYSQL_PORT", os.environ.get("MYSQL_PORT", "3306"))),
    "user": os.environ.get("BIGORDER_MYSQL_USER", os.environ.get("MYSQL_USER", "root")),
    "password": os.environ.get("BIGORDER_MYSQL_PASSWORD", os.environ.get("MYSQL_PASSWORD", "")),
    "database": os.environ.get("BIGORDER_MYSQL_DATABASE", os.environ.get("MYSQL_DATABASE", "exchange")),
}


def _check_key(key: str):
    if key != API_KEY:
        return False
    return True


def _get_conn():
    import pymysql
    return pymysql.connect(
        host=_MYSQL["host"],
        port=_MYSQL["port"],
        user=_MYSQL["user"],
        password=_MYSQL["password"],
        database=_MYSQL["database"],
        charset="utf8mb4",
        connect_timeout=5,
        read_timeout=30,
        write_timeout=30,
    )


# ── 信号卡写入 ──────────────────────────────────────────────────────────────

class SignalCardInput(BaseModel):
    coin: str
    direction: str
    grade: str
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit: float
    current_price: float
    invalidation_price: float = 0
    confidence: float = 0
    risk_reward_ratio: float = 0
    position_pct: float = 0
    sources_json: str = "[]"
    math_json: Optional[str] = None
    strategy_version: int = 1
    regime: str = "quiet"
    adaptive_weights_json: Optional[str] = None


@app.post("/api/save_signal_card")
def save_signal_card(card: SignalCardInput, key: str = Query(...)):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    if card.grade == "C":
        return {"ok": True, "id": None, "msg": "C-grade skipped"}
    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO signal_card_history
            (coin, direction, grade, entry_low, entry_high, stop_loss, take_profit,
             current_price, invalidation_price, confidence, risk_reward_ratio, position_pct,
             sources_json, math_json, strategy_version, regime, adaptive_weights_json, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (card.coin, card.direction, card.grade, card.entry_low, card.entry_high,
             card.stop_loss, card.take_profit, card.current_price, card.invalidation_price,
             card.confidence, card.risk_reward_ratio, card.position_pct,
             card.sources_json, card.math_json, card.strategy_version, card.regime,
             card.adaptive_weights_json, "pending"),
        )
        conn.commit()
        rid = cursor.lastrowid
        cursor.close()
        return {"ok": True, "id": rid}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


# ── 查询 pending 卡（供结算）───────────────────────────────────────────────

@app.get("/api/pending_cards")
def get_pending_cards(key: str = Query(...), limit: int = Query(100)):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    conn = None
    try:
        import pymysql.cursors
        conn = _get_conn()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            """
            SELECT id, coin, direction, stop_loss, take_profit, current_price,
                   confidence, created_at
            FROM signal_card_history
            WHERE status = 'pending'
              AND created_at <= DATE_SUB(NOW(), INTERVAL 1 HOUR)
            ORDER BY created_at ASC
            LIMIT %s
            """, (limit,),
        )
        rows = cursor.fetchall()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            for k, v in r.items():
                if v is not None and hasattr(v, '__float__'):
                    r[k] = float(v)
        cursor.close()
        return {"ok": True, "cards": rows}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


# ── 更新卡状态（结算用）─────────────────────────────────────────────────────

class UpdateCardInput(BaseModel):
    card_id: int
    status: str
    settled_price: float
    pnl_pct: float


@app.post("/api/update_card_status")
def update_card_status(data: UpdateCardInput, key: str = Query(...)):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE signal_card_history
            SET status = %s, settled_price = %s, pnl_pct = %s, settled_at = NOW()
            WHERE id = %s
            """,
            (data.status, data.settled_price, data.pnl_pct, data.card_id),
        )
        conn.commit()
        cursor.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


# ── 扫描结果存库 ─────────────────────────────────────────────────────────────

class ScanBatchInput(BaseModel):
    total_coins: int
    signal_count: int
    scan_time: float
    results_json: str
    displays_json: str


@app.post("/api/save_scan_batch")
def save_scan_batch(data: ScanBatchInput, key: str = Query(...)):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        cursor.execute("""
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
        """)
        cursor.execute(
            """
            INSERT INTO scan_cache (total_coins, signal_count, scan_time, results_json, displays_json)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (data.total_coins, data.signal_count, data.scan_time, data.results_json, data.displays_json),
        )
        conn.commit()
        cursor.close()
        # 清理：只保留最近 100 条
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM scan_cache WHERE id NOT IN (SELECT id FROM (SELECT id FROM scan_cache ORDER BY id DESC LIMIT 100) t)"
        )
        conn.commit()
        cursor.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


# ── 读取最新扫描 ─────────────────────────────────────────────────────────────

@app.get("/api/latest_scan")
def get_latest_scan(key: str = Query(...), max_age_seconds: int = Query(1800)):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    conn = None
    try:
        import pymysql.cursors
        conn = _get_conn()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            """
            SELECT total_coins, signal_count, scan_time, results_json, displays_json, created_at
            FROM scan_cache ORDER BY id DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        cursor.close()
        if not row:
            return {"ok": True, "data": None}
        from datetime import datetime
        cached_at = row["created_at"]
        age = (datetime.now() - cached_at).total_seconds() if cached_at else 9999
        return {
            "ok": True,
            "data": {
                "total_coins": row["total_coins"],
                "signal_count": row["signal_count"],
                "scan_time": row["scan_time"],
                "signals": json.loads(row["results_json"]) if row["results_json"] else [],
                "displays": json.loads(row["displays_json"]) if row["displays_json"] else [],
                "cached_at": cached_at.strftime("%Y-%m-%d %H:%M:%S") if cached_at else "",
                "is_stale": age > max_age_seconds,
            },
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


# ── 累加胜率 ─────────────────────────────────────────────────────────────────

@app.get("/api/winrate")
def get_winrate(key: str = Query(...), coin: str = Query(None), grade: str = Query(None), days: int = Query(30)):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    conn = None
    try:
        import pymysql.cursors
        conn = _get_conn()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        where_parts = [
            "status IN ('hit_tp', 'hit_sl', 'expired')",
            "created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)",
        ]
        params: list = [days]
        if coin:
            where_parts.append("coin = %s")
            params.append(coin.upper())
        if grade:
            where_parts.append("grade = %s")
            params.append(grade)

        where = " AND ".join(where_parts)
        cursor.execute(
            f"SELECT status, pnl_pct FROM signal_card_history WHERE {where} ORDER BY created_at DESC LIMIT 500",
            params,
        )
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            return {"ok": True, "data": None}

        hit_tp = sum(1 for r in rows if r["status"] == "hit_tp")
        hit_sl = sum(1 for r in rows if r["status"] == "hit_sl")
        expired = sum(1 for r in rows if r["status"] == "expired")
        total = len(rows)
        wins = hit_tp + sum(1 for r in rows if r["status"] == "expired" and (r.get("pnl_pct") or 0) > 0)
        win_rate = wins / total * 100
        pnls = [r.get("pnl_pct") or 0 for r in rows]
        avg_profit = sum(pnls) / len(pnls)

        return {
            "ok": True,
            "data": {
                "win_rate": round(win_rate, 1),
                "sample_count": total,
                "hit_tp": hit_tp,
                "hit_sl": hit_sl,
                "expired": expired,
                "avg_profit_pct": round(avg_profit, 2),
            },
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


# ── 历史记录查询 ─────────────────────────────────────────────────────────────

@app.get("/api/history")
def get_history(
    key: str = Query(...),
    coin: str = Query(None),
    grade: str = Query(None),
    status: str = Query(None),
    direction: str = Query(None),
    days: int = Query(7),
    limit: int = Query(50),
):
    if not _check_key(key):
        return {"ok": False, "error": "auth failed"}
    conn = None
    try:
        import pymysql.cursors
        conn = _get_conn()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        where_parts = ["created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"]
        params: list = [days]
        if coin:
            where_parts.append("coin = %s")
            params.append(coin.upper())
        if grade:
            where_parts.append("grade = %s")
            params.append(grade.upper())
        if status:
            where_parts.append("status = %s")
            params.append(status)
        if direction:
            where_parts.append("direction = %s")
            params.append(direction)

        where = " AND ".join(where_parts)

        cursor.execute(f"SELECT COUNT(*) as cnt FROM signal_card_history WHERE {where}", params)
        total = cursor.fetchone()["cnt"]

        cursor.execute(
            f"""
            SELECT id, coin, direction, grade,
                   entry_low, entry_high, stop_loss, take_profit,
                   current_price, confidence, risk_reward_ratio,
                   status, settled_price, pnl_pct,
                   created_at, settled_at
            FROM signal_card_history
            WHERE {where}
            ORDER BY created_at DESC LIMIT %s
            """,
            params + [limit],
        )
        rows = cursor.fetchall()
        cursor.close()

        cards = []
        for r in rows:
            cards.append({
                "id": r["id"],
                "coin": r["coin"],
                "direction": r["direction"],
                "grade": r["grade"],
                "entry_zone": [float(r["entry_low"] or 0), float(r["entry_high"] or 0)],
                "stop_loss": float(r["stop_loss"] or 0),
                "take_profit": float(r["take_profit"] or 0),
                "price": float(r["current_price"] or 0),
                "confidence": float(r["confidence"] or 0),
                "risk_reward": float(r["risk_reward_ratio"] or 0),
                "status": r["status"],
                "settled_price": float(r["settled_price"] or 0) if r["settled_price"] else None,
                "pnl_pct": float(r["pnl_pct"] or 0) if r["pnl_pct"] else None,
                "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else None,
                "settled_at": r["settled_at"].strftime("%Y-%m-%d %H:%M") if r["settled_at"] else None,
            })

        return {"ok": True, "total": total, "count": len(cards), "cards": cards}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if conn:
            conn.close()


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
