"""
方向无关的 EV 护栏 — 自动从历史数据学习"哪些 (regime, tfa, direction) 组合 wr 差"。

设计原则：
1. 方向无关：long 和 short 都可能进 guardrail 表，谁 wr 差过滤谁
2. 数据驱动：每日 recompute_guardrails() 扫近 60d signal_card_history 自动重算
3. 自动重审：每条规则 30 天过期，市场结构变化后自动失效
4. 可解释：fusion 写入 math_json.ev_guardrail 说明为什么被砍/奖励
5. 可回滚：env GUARDRAIL_ENABLED=0 全局关闭
"""
import json
import os
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)

LOOKBACK_DAYS = int(os.getenv("GUARDRAIL_LOOKBACK_DAYS", "60"))
MIN_SAMPLE = int(os.getenv("GUARDRAIL_MIN_SAMPLE", "30"))
WR_BAD_THRESHOLD = float(os.getenv("GUARDRAIL_WR_BAD", "0.30"))
WR_GOOD_THRESHOLD = float(os.getenv("GUARDRAIL_WR_GOOD", "0.60"))
TTL_DAYS = int(os.getenv("GUARDRAIL_TTL_DAYS", "30"))

DATA_DIR = Path(os.getenv("GUARDRAIL_DATA_DIR", "app/signals/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
GUARDRAIL_FILE = DATA_DIR / "guardrail_table.json"
REWARD_FILE = DATA_DIR / "reward_table.json"

_cache_lock = threading.Lock()
_cache: Dict = {"guardrail": None, "reward": None, "loaded_at": None}
_CACHE_TTL_SEC = 300


def _key(regime: str, tfa: str, direction: str) -> str:
    return f"{(regime or 'unknown')}|{tfa or 'unknown'}|{(direction or 'unknown')}"


def _load_table(path: Path) -> Dict:
    if not path.exists():
        return {"rules": {}, "updated_at": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"读取 {path.name} 失败: {e}")
        return {"rules": {}, "updated_at": None}


def _save_table(path: Path, table: Dict) -> None:
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_cached(table_type: str) -> Dict:
    now = datetime.utcnow()
    with _cache_lock:
        if (
            _cache["loaded_at"] is None
            or (now - _cache["loaded_at"]).total_seconds() > _CACHE_TTL_SEC
            or _cache.get(table_type) is None
        ):
            path = GUARDRAIL_FILE if table_type == "guardrail" else REWARD_FILE
            _cache[table_type] = _load_table(path)
            _cache["loaded_at"] = now
        return _cache[table_type] or {"rules": {}}


def _rule_active(rule: Dict) -> bool:
    if not rule or not rule.get("expires_at"):
        return False
    try:
        return datetime.utcnow() < datetime.fromisoformat(rule["expires_at"])
    except Exception:
        return False


def check_signal(regime: str, tfa: str, direction: str) -> Tuple[bool, Optional[Dict]]:
    """检查 (regime, tfa, direction) 是否命中 guardrail。返回 (is_blocked, rule_detail)。"""
    if os.getenv("GUARDRAIL_ENABLED", "1") != "1":
        return False, None

    rules = _get_cached("guardrail").get("rules", {})
    rule = rules.get(_key(regime, tfa, direction))
    if not _rule_active(rule):
        return False, None
    return True, rule


def check_reward(regime: str, tfa: str, direction: str) -> Tuple[bool, float, Optional[Dict]]:
    """检查是否命中 reward。返回 (is_rewarded, confidence_multiplier, rule_detail)。"""
    if os.getenv("GUARDRAIL_ENABLED", "1") != "1":
        return False, 1.0, None

    rules = _get_cached("reward").get("rules", {})
    rule = rules.get(_key(regime, tfa, direction))
    if not _rule_active(rule):
        return False, 1.0, None
    return True, 1.1, rule


def recompute_guardrails() -> Dict:
    """每日 cron 调用：扫近 60d 历史，重算 guardrail 和 reward 表。"""
    try:
        from app.signals.settlement import _get_conn
        import pymysql.cursors
    except ImportError as e:
        logger.error(f"recompute_guardrails import 失败: {e}")
        return {"guardrail_count": 0, "reward_count": 0}

    query = """
        SELECT
            LOWER(direction) AS direction,
            LOWER(JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.market_regime'))) AS regime,
            LOWER(JSON_UNQUOTE(JSON_EXTRACT(math_json, '$.tf_agreement'))) AS tfa,
            COUNT(*) AS n,
            SUM(status='hit_tp' OR (status='expired' AND pnl_pct > 0)) AS wins,
            AVG(pnl_pct) AS avg_pnl
        FROM signal_card_history
        WHERE status IN ('hit_tp', 'hit_sl', 'expired')
          AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
          AND math_json IS NOT NULL
        GROUP BY direction, regime, tfa
        HAVING n >= %s
    """

    try:
        conn = _get_conn()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(query, (LOOKBACK_DAYS, MIN_SAMPLE))
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.error(f"recompute_guardrails 查询失败: {type(e).__name__}: {e}")
        return {"guardrail_count": 0, "reward_count": 0, "error": str(e)}

    now = datetime.utcnow()
    expires = now + timedelta(days=TTL_DAYS)

    new_guardrail: Dict = {"rules": {}, "updated_at": now.isoformat(), "lookback_days": LOOKBACK_DAYS}
    new_reward: Dict = {"rules": {}, "updated_at": now.isoformat(), "lookback_days": LOOKBACK_DAYS}

    for row in rows:
        direction = (row.get("direction") or "").lower()
        if direction not in ("long", "short"):
            continue
        regime = (row.get("regime") or "unknown").lower()
        tfa = (row.get("tfa") or "unknown").lower()
        n = int(row.get("n") or 0)
        wins = int(row.get("wins") or 0)
        avg_pnl = float(row.get("avg_pnl") or 0)
        wr = wins / n if n > 0 else 0

        rule = {
            "regime": regime,
            "tfa": tfa,
            "direction": direction,
            "n": n,
            "wins": wins,
            "wr": round(wr, 3),
            "avg_pnl": round(avg_pnl, 3),
            "expires_at": expires.isoformat(),
            "computed_at": now.isoformat(),
        }

        if wr < WR_BAD_THRESHOLD:
            new_guardrail["rules"][_key(regime, tfa, direction)] = rule
            logger.info(f"GUARDRAIL+ {regime}|{tfa}|{direction}: n={n} wr={wr:.2f} avg_pnl={avg_pnl:+.3f}")
        elif wr >= WR_GOOD_THRESHOLD:
            new_reward["rules"][_key(regime, tfa, direction)] = rule
            logger.info(f"REWARD+   {regime}|{tfa}|{direction}: n={n} wr={wr:.2f} avg_pnl={avg_pnl:+.3f}")

    _save_table(GUARDRAIL_FILE, new_guardrail)
    _save_table(REWARD_FILE, new_reward)

    with _cache_lock:
        _cache["guardrail"] = None
        _cache["reward"] = None
        _cache["loaded_at"] = None

    summary = {
        "guardrail_count": len(new_guardrail["rules"]),
        "reward_count": len(new_reward["rules"]),
        "sample_rows": len(rows),
        "updated_at": now.isoformat(),
    }
    logger.info(f"recompute_guardrails 完成: {summary}")
    return summary


def list_rules() -> Dict:
    """调试端点用：列出当前所有规则和配置。"""
    return {
        "guardrail": _load_table(GUARDRAIL_FILE),
        "reward": _load_table(REWARD_FILE),
        "config": {
            "lookback_days": LOOKBACK_DAYS,
            "min_sample": MIN_SAMPLE,
            "wr_bad": WR_BAD_THRESHOLD,
            "wr_good": WR_GOOD_THRESHOLD,
            "ttl_days": TTL_DAYS,
            "enabled": os.getenv("GUARDRAIL_ENABLED", "1") == "1",
        },
    }
