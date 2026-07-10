"""BigOrder 对话式路由 - Function Calling"""
import json
import time
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.bigorder.models import ChatRequest, SignalLevel
import app.bigorder.deps as bigorder_deps
from app.core.llm_client import get_llm_client
from config.settings import settings
from app.utils.chat_trace import trace, Timer, mask
from app.utils.sse_protocol import (
    sse_start, sse_chat_delta, sse_suggestions,
    sse_tool_debug, sse_done, sse_error, render,
    ERR_LLM_TIMEOUT, ERR_TOOL_TIMEOUT, ERR_INTERNAL,
)

router = APIRouter()


@router.get("/status")
async def bigorder_status():
    """诊断 BigOrder：REDIS_ENABLED 是否生效、Redis 是否连上（无需 Redis 即可访问）"""
    return bigorder_deps.get_status()


SYSTEM_PROMPT = """You are the "BigOrder Detection" intelligent assistant, specializing in cryptocurrency large-trade anomaly analysis.

You MUST call exactly ONE tool to get real-time data, then answer based on the result.

Rules:
1. Call exactly one tool per question — pick the most relevant one
2. Be concise, include key numbers (scores, amounts, ratios)
3. If data is empty, clearly inform the user
4. Use tables when comparing multiple items
5. Never fabricate data

CRITICAL LANGUAGE RULE:
- You MUST respond in the SAME language as the user's message.
- If the user writes in English, your ENTIRE response (headings, analysis, labels, conclusions) MUST be in English.
- If the user writes in Chinese, your ENTIRE response MUST be in Chinese.
- This is mandatory. Do NOT mix languages.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "query_anomalies",
            "description": "获取最新异动信号列表，可按交易所和最低分数过滤 | Get latest anomaly signals, filterable by exchange and minimum score",
            "parameters": {
                "type": "object",
                "properties": {
                    "exchange": {"type": "string", "description": "交易所: Binance/OKX/Bybit/Bitget/Gate | Exchange name"},
                    "min_score": {"type": "integer", "description": "最低得分阈值 | Minimum score threshold"},
                    "limit": {"type": "integer", "description": "返回条数，默认20 | Number of results, default 20"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_coin_signal",
            "description": "查询指定币种的异动评分详情（四维得分、综合得分、信号等级） | Get anomaly score details for a specific coin (4-dimension scores, total score, signal level)",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "币种名称，如 BTC/ETH/SOL | Coin symbol, e.g. BTC/ETH/SOL"}
                },
                "required": ["coin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_order_flow",
            "description": "查询指定币种的资金流向（买入额、卖出额、净流入、买卖比） | Get fund flow for a specific coin (buy/sell amount, net flow, buy/sell ratio)",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "币种名称 | Coin symbol"},
                    "window": {"type": "integer", "description": "时间窗口（分钟），默认5 | Time window in minutes, default 5"}
                },
                "required": ["coin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_large_orders",
            "description": "获取指定币种最近的大单明细（按金额排序） | Get recent large orders for a specific coin, sorted by amount",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "币种名称 | Coin symbol"},
                    "top": {"type": "integer", "description": "取前N笔，默认10 | Top N orders, default 10"},
                    "exchange": {"type": "string", "description": "交易所，默认Binance | Exchange, default Binance"}
                },
                "required": ["coin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_history",
            "description": "查询历史异动记录，可按币种、天数、等级过滤 | Query historical anomaly records, filterable by coin, days, and level",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "币种名称 | Coin symbol"},
                    "days": {"type": "integer", "description": "最近N天，默认7 | Last N days, default 7"},
                    "level": {"type": "string", "enum": ["medium", "strong"], "description": "信号等级 | Signal level"},
                    "limit": {"type": "integer", "description": "返回条数，默认50 | Number of results, default 50"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_exchange_compare",
            "description": "对比同一币种在不同交易所的买卖分布 | Compare buy/sell distribution of a coin across exchanges",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "币种名称 | Coin symbol"}
                },
                "required": ["coin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manual_scan",
            "description": "手动触发全量扫描 | Manually trigger a full scan",
            "parameters": {
                "type": "object",
                "properties": {
                    "coins": {"type": "array", "items": {"type": "string"}, "description": "币种列表 | List of coin symbols"}
                },
                "required": []
            }
        }
    },
]

_TOOL_TIMEOUTS = {
    "query_anomalies": 5, "query_coin_signal": 5,
    "query_order_flow": 5, "query_large_orders": 5,
    "query_history": 5, "query_exchange_compare": 5,
    "manual_scan": 15,
}


class _Cache:
    def __init__(self):
        self._store: dict = {}

    def get(self, key: str, ttl: float = 8.0):
        entry = self._store.get(key)
        if entry and time.time() - entry[0] < ttl:
            return entry[1]
        return None

    def set(self, key: str, value):
        self._store[key] = (time.time(), value)
        if len(self._store) > 500:
            now = time.time()
            expired = [k for k, v in self._store.items() if now - v[0] > 30]
            for k in expired:
                del self._store[k]

_cache = _Cache()


# ── 推荐问题模板 ──────────────────────────────────────────
_SUGGESTION_TEMPLATES = {
    "zh": {
        "query_anomalies": [
            "{coin}有什么异动信号",
            "{coin}大单资金流怎么样",
            "过去3天有哪些强信号",
        ],
        "query_coin_signal": [
            "{coin}资金流向怎么样",
            "{coin}最近的大单明细",
            "对比{coin}各交易所",
        ],
        "query_order_flow": [
            "{coin}有什么异动信号",
            "{coin}最近的大单明细",
            "对比{coin}各交易所",
        ],
        "query_large_orders": [
            "{coin}有什么异动信号",
            "{coin}资金流向怎么样",
            "对比{coin}各交易所",
        ],
        "query_history": [
            "市场有哪些异动信号",
            "{coin}有什么异动",
            "{coin}资金流向怎么样",
        ],
        "query_exchange_compare": [
            "{coin}有什么异动信号",
            "{coin}最近的大单明细",
            "{coin}资金流向怎么样",
        ],
        "manual_scan": [
            "市场有哪些异动信号",
            "{coin}有什么异动",
            "{coin}资金流向怎么样",
        ],
    },
    "en": {
        "query_anomalies": [
            "Any anomalies for {coin}?",
            "{coin} fund flow stats",
            "Strong signals in the past 3 days",
        ],
        "query_coin_signal": [
            "{coin} fund flow stats",
            "{coin} recent large orders",
            "Compare {coin} across exchanges",
        ],
        "query_order_flow": [
            "{coin} anomaly signals",
            "{coin} recent large orders",
            "Compare {coin} across exchanges",
        ],
        "query_large_orders": [
            "{coin} anomaly signals",
            "{coin} fund flow stats",
            "Compare {coin} across exchanges",
        ],
        "query_history": [
            "Market anomaly signals",
            "{coin} anomaly details",
            "{coin} fund flow stats",
        ],
        "query_exchange_compare": [
            "{coin} anomaly signals",
            "{coin} recent large orders",
            "{coin} fund flow stats",
        ],
        "manual_scan": [
            "Market anomaly signals",
            "{coin} anomaly details",
            "{coin} fund flow stats",
        ],
    },
}


def _detect_language(text: str) -> str:
    """简单语言检测：含中文字符返回 zh"""
    for ch in text:
        if '一' <= ch <= '鿿':
            return "zh"
    return "en"


def _get_suggestions(tool_name: str, user_message: str, coin: str = "") -> list:
    """根据调用的 tool、用户消息和币种生成推荐问题

    coin 来自 LLM 从用户问题抽取的 tool_args.coin；没有就用空串，
    模板里的 {coin} 占位会被替换成空，问题变成「最近有什么异动信号」这种通用表达。
    """
    language = _detect_language(user_message)
    coin = (coin or "").strip().upper()
    templates = _SUGGESTION_TEMPLATES.get(language, _SUGGESTION_TEMPLATES["zh"])
    suggestions = templates.get(tool_name, templates.get("query_anomalies", []))
    return [
        {"id": str(i + 1), "suggestion": s.format(coin=coin)}
        for i, s in enumerate(suggestions)
    ]


def _get_bigorder_mysql_config():
    """获取 bigorder 专属 MySQL 配置"""
    from app.signals.settlement import _env_get
    return {
        "host": _env_get("BIGORDER_MYSQL_HOST") or settings.bigorder_mysql_host or settings.mysql_host,
        "port": int(_env_get("BIGORDER_MYSQL_PORT") or 0) or settings.bigorder_mysql_port or settings.mysql_port,
        "user": _env_get("BIGORDER_MYSQL_USER") or settings.bigorder_mysql_user or settings.mysql_user,
        "password": _env_get("BIGORDER_MYSQL_PASSWORD") or settings.bigorder_mysql_password or settings.mysql_password,
        "database": _env_get("BIGORDER_MYSQL_DATABASE") or settings.bigorder_mysql_database or settings.mysql_database,
    }


def _humanize_timestamps(obj):
    """递归把所有时间戳字段转可读字符串（不喂原始数字给 LLM）。

    tick.deal_timestamp (秒) → deal_time
    signal.timestamp / updated_at (毫秒 > 1e12) → *_str
    """
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            obj[k] = _humanize_timestamps(obj[k])
        if "deal_timestamp" in obj and isinstance(obj["deal_timestamp"], (int, float)):
            ts = obj.pop("deal_timestamp")
            try:
                obj["deal_time"] = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, OSError, OverflowError):
                obj["deal_time"] = str(ts)
        for key in ("timestamp", "updated_at"):
            v = obj.get(key)
            if isinstance(v, (int, float)) and v > 1_000_000_000_000:
                try:
                    obj[key + "_str"] = datetime.fromtimestamp(int(v) / 1000).strftime("%Y-%m-%d %H:%M:%S")
                    obj.pop(key)
                except (ValueError, OSError, OverflowError):
                    pass
        return obj
    if isinstance(obj, list):
        return [_humanize_timestamps(x) for x in obj]
    return obj


def _execute_tool(name: str, args: dict) -> dict:
    """工具调度入口 — 统一转换所有时间戳字段为可读字符串"""
    result = _execute_tool_impl(name, args)
    return _humanize_timestamps(result)


def _execute_tool_impl(name: str, args: dict) -> dict:
    if name == "query_anomalies":
        cache_key = f"anomalies:{args.get('exchange')}:{args.get('min_score')}:{args.get('limit', 20)}"
        hit = _cache.get(cache_key)
        if hit:
            return hit
        signals = bigorder_deps.scorer.get_anomaly_list(
            exchange=args.get("exchange"),
            min_score=args.get("min_score"),
            limit=args.get("limit", 20)
        )
        result = {"count": len(signals), "signals": [
            {k: v for k, v in s.items() if k not in ("llm_analysis", "timestamp")} for s in signals
        ]}
        _cache.set(cache_key, result)
        return result

    if name == "query_coin_signal":
        coin = args["coin"].upper()
        cache_key = f"coin_signal:{coin}"
        hit = _cache.get(cache_key, ttl=5.0)
        if hit:
            return hit
        cached = bigorder_deps.scorer.get_coin_signal(coin)
        if cached:
            cached.pop("llm_analysis", None)
            cached.pop("timestamp", None)
            _cache.set(cache_key, cached)
            return cached
        result = {}
        for exchange in settings.exchanges:
            try:
                signal = bigorder_deps.scorer.score_exchange(exchange, coin)
                if signal:
                    d = signal.model_dump()
                    d.pop("llm_analysis", None)
                    d.pop("top_orders", None)
                    d.pop("timestamp", None)
                    result[exchange] = d
            except Exception:
                continue
        data = {"coin": coin, "exchanges": result} if result else {"coin": coin, "message": "No data available"}
        _cache.set(cache_key, data)
        return data

    if name == "query_order_flow":
        coin = args["coin"].upper()
        window = args.get("window", 5)
        cache_key = f"order_flow:{coin}:{window}"
        hit = _cache.get(cache_key, ttl=5.0)
        if hit:
            return hit
        cached = bigorder_deps.scorer.get_order_flow(coin, window)
        if cached:
            _cache.set(cache_key, cached)
            return cached
        all_data = bigorder_deps.consumer.fetch_all_exchanges_pipeline(coin, window * 60)
        result = {"coin": coin, "window_minutes": window, "exchanges": {}}
        for exchange, (buy_ticks, sell_ticks) in all_data.items():
            buy_amount = sum(t.amount for t in buy_ticks)
            sell_amount = sum(t.amount for t in sell_ticks)
            total = buy_amount + sell_amount
            result["exchanges"][exchange] = {
                "buy_amount": round(buy_amount, 2),
                "sell_amount": round(sell_amount, 2),
                "net_flow": round(buy_amount - sell_amount, 2),
                "buy_count": len(buy_ticks),
                "sell_count": len(sell_ticks),
                "buy_ratio": round(buy_amount / total, 4) if total > 0 else 0.5,
            }
        _cache.set(cache_key, result)
        return result

    if name == "query_large_orders":
        coin = args["coin"].upper()
        top = args.get("top", 10)
        exchange = args.get("exchange", "Binance")
        cache_key = f"large_orders:{coin}:{top}:{exchange}"
        hit = _cache.get(cache_key)
        if hit:
            return hit
        cached = bigorder_deps.scorer.get_large_orders(coin, top)
        if cached:
            data = {"coin": coin, "count": len(cached), "orders": cached}
            _cache.set(cache_key, data)
            return data
        orders = bigorder_deps.consumer.get_top_orders(coin, exchange=exchange, top_n=top)
        orders_serialized = []
        for o in orders:
            d = o.model_dump()
            if d.get("deal_timestamp"):
                d["deal_time"] = datetime.fromtimestamp(d["deal_timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
                d.pop("deal_timestamp", None)
            orders_serialized.append(d)
        data = {"coin": coin, "count": len(orders), "orders": orders_serialized}
        _cache.set(cache_key, data)
        return data

    if name == "query_history":
        return _query_history(
            coin=args.get("coin"), days=args.get("days", 7),
            level=args.get("level"), limit=args.get("limit", 50)
        )

    if name == "query_exchange_compare":
        coin = args["coin"].upper()
        cache_key = f"exchange_compare:{coin}"
        hit = _cache.get(cache_key, ttl=5.0)
        if hit:
            return hit
        data = bigorder_deps.scorer.get_exchange_compare(coin)
        _cache.set(cache_key, data)
        return data

    if name == "manual_scan":
        coins = args.get("coins")
        signals = bigorder_deps.scorer.score_all(coins)
        result = []
        for s in signals:
            d = s.model_dump()
            d.pop("top_orders", None)
            result.append(d)
        return {
            "total": len(signals),
            "strong_count": sum(1 for s in signals if s.score.level == SignalLevel.STRONG),
            "medium_count": sum(1 for s in signals if s.score.level == SignalLevel.MEDIUM),
            "signals": result
        }

    return {"error": f"Unknown tool: {name}"}


def _query_history(coin: Optional[str], days: int, level: Optional[str], limit: int) -> dict:
    import pymysql
    conn = None
    try:
        mysql_cfg = _get_bigorder_mysql_config()
        conn = pymysql.connect(
            host=mysql_cfg["host"], port=mysql_cfg["port"],
            user=mysql_cfg["user"], password=mysql_cfg["password"],
            database=mysql_cfg["database"], charset="utf8mb4",
            connect_timeout=5, read_timeout=10
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        sql = "SELECT * FROM anomaly_history WHERE 1=1"
        params = []
        if coin:
            sql += " AND coin = %s"
            params.append(coin.upper())
        if level:
            sql += " AND level = %s"
            params.append(level)
        if days:
            sql += " AND created_at >= DATE_SUB(NOW(), INTERVAL %s DAY)"
            params.append(days)
        sql += " ORDER BY timestamp DESC LIMIT %s"
        params.append(limit)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return {"count": len(rows), "data": rows}
    except Exception as e:
        return {"count": 0, "data": [], "error": str(e)}
    finally:
        if conn:
            conn.close()


@router.post("/chat")
async def chat(request: ChatRequest):
    """对话式 SSE 流式接口"""
    if not bigorder_deps.is_redis_available():
        status = bigorder_deps.get_status()
        return JSONResponse(status_code=503, content=status)

    rid = request.request_id
    user_message = request.message

    trace(rid, "enter", endpoint="bigorder_chat",
          conv=request.conversation_id, msg=mask(user_message))

    client = get_llm_client()
    model = settings.bigorder_deepseek_model

    # 加载会话上下文（conversation_id 维持跨轮币种，取代 coin 入参）
    from app.core.session import session_manager
    session = session_manager.get(request.conversation_id) if request.conversation_id else None
    history_questions = session["questions"] if session else None
    last_coin = session["coin_symbol"] if session else None
    trace(rid, "session.loaded",
          has_session=bool(session),
          history_n=len(history_questions or []),
          last_coin=last_coin)

    async def event_generator():
        trace(rid, "generator.start")
        # 把最近 3 个用户问题拼进 system prompt，让 LLM 理解 "再来一个" 这类省略语
        sys_content = SYSTEM_PROMPT
        if history_questions:
            recent = "\n".join(f"- {q}" for q in history_questions[-3:])
            sys_content = f"{SYSTEM_PROMPT}\n\n[最近用户提问，供理解省略语]\n{recent}"

        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_message},
        ]

        yield render(sse_start(rid, request.conversation_id))
        yield render(sse_tool_debug(rid, "thinking", {"status": "Analyzing..."}))

        # ---- Step 1: 非流式调用，让 LLM 选 tool ----
        try:
            with Timer(rid, "step1.llm_route"):
                route_resp = await asyncio.wait_for(
                    client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        max_tokens=500,
                    ),
                    timeout=30.0
                )
        except Exception as e:
            trace(rid, "step1.error", error=f"{type(e).__name__}: {e}")
            yield render(sse_error(rid, ERR_LLM_TIMEOUT, str(e)))
            return

        msg = route_resp.choices[0].message

        msg = route_resp.choices[0].message
        trace(rid, "step1.done",
              has_tool_calls=bool(msg.tool_calls),
              tool=msg.tool_calls[0].function.name if msg.tool_calls else None)

        # 无 tool_call -> 直接输出 LLM 回答
        if not msg.tool_calls:
            yield render(sse_chat_delta(rid, msg.content or ""))
            yield render(sse_done(rid))
            trace(rid, "generator.end", reason="no_tool_calls")
            return

        # ---- Step 2: 执行 tool ----
        tc = msg.tool_calls[0]
        tool_name = tc.function.name
        try:
            tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except json.JSONDecodeError:
            tool_args = {}

        yield render(sse_tool_debug(rid, "tool_call", {"tool": tool_name, "args": tool_args}))

        loop = asyncio.get_running_loop()
        timeout = _TOOL_TIMEOUTS.get(tool_name, 10)
        try:
            with Timer(rid, "step2.tool_exec", tool=tool_name, timeout=timeout):
                tool_result = await asyncio.wait_for(
                    loop.run_in_executor(None, _execute_tool, tool_name, tool_args),
                    timeout=timeout
                )
        except asyncio.TimeoutError:
            trace(rid, "step2.timeout", tool=tool_name, timeout=timeout)
            tool_result = {"error": "Query timed out"}

        trace(rid, "step2.done", tool=tool_name,
              has_error=isinstance(tool_result, dict) and bool(tool_result.get("error")))

        # 保存本轮到 session（coin_symbol 用于下一轮的省略语解析）
        if request.conversation_id:
            session_manager.update(
                request.conversation_id,
                question=user_message,
                coin_symbol=tool_args.get("coin") or last_coin,
            )

        yield render(sse_tool_debug(rid, "tool_result", {"tool": tool_name, "result": tool_result}))

        # ---- Step 3: 流式输出最终回答 ----
        # 关键：重置 messages 为 system+user（不带 tool_calls 历史），
        # 否则 DeepSeek 收到 tool_calls+tool_result 会继续生成 tool_call 标签
        # （如 "｜｜DSML｜｜invoke>...</｜｜DSML｜｜tool_calls>"）并流到 content。
        user_lang = _detect_language(user_message)
        if user_lang == "en":
            sys_content = (
                "You are a cryptocurrency large-trade anomaly analyst.\n"
                "Based on the data provided, write a clear analysis in English.\n"
                "Be concise, include key numbers. Use tables when comparing multiple items. Never fabricate data."
            )
            user_suffix = "Write a clear analysis in English."
        else:
            sys_content = (
                "你是一名加密货币大单异动分析师。\n"
                "根据提供的数据，用中文写一段清晰的分析。\n"
                "简洁、突出关键数字。对比多个项目时用表格。绝对不要编造数据。\n"
                "不要输出任何 XML/标签/工具调用格式，只输出自然语言分析。"
            )
            user_suffix = "请用中文写一段清晰的分析（不要输出任何标签或工具调用格式）。"

        messages = [
            {"role": "system", "content": sys_content},
            {
                "role": "user",
                "content": (
                    f"Question: {user_message}\n\n"
                    f"Data:\n{json.dumps(tool_result, ensure_ascii=False, default=str, indent=2)}\n\n"
                    f"{user_suffix}"
                )
            },
        ]

        try:
            with Timer(rid, "step3.llm_stream"):
                final_resp = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    max_tokens=1000,
                    stream=True,
                )
                chunk_n = 0
                async for chunk in final_resp:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        chunk_n += 1
                        yield render(sse_chat_delta(rid, delta.content))
            trace(rid, "step3.done", chunks=chunk_n)
        except Exception as e:
            trace(rid, "step3.error", error=f"{type(e).__name__}: {e}")
            yield render(sse_error(rid, ERR_INTERNAL, str(e)))

        # 生成推荐问题
        suggestions = _get_suggestions(tool_name, user_message, tool_args.get("coin", ""))
        if suggestions:
            yield render(sse_suggestions(rid, [s["suggestion"] for s in suggestions]))

        yield render(sse_done(rid))
        trace(rid, "generator.end", reason="normal")

    return EventSourceResponse(event_generator())
