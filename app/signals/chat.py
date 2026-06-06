"""信号卡 Chat — 独立对话式 SSE 接口（量化分析 + 信号卡融合）"""
import json
import time
import asyncio
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.core.llm_client import get_llm_client
from config.settings import settings

router = APIRouter()

SYSTEM_PROMPT = """You are the "Signal Alpha" quantitative analyst, specializing in cryptocurrency signal card analysis and trading recommendations.

You MUST call exactly ONE tool to get real-time data, then answer based on the result.

Rules:
1. Call exactly one tool per question — pick the most relevant one
2. Be concise, include key numbers (confidence, entry/stop-loss/take-profit, risk-reward)
3. If a signal card is provided, first acknowledge the card, then provide detailed text analysis
4. For C-grade signals, clearly warn about low confidence and higher risk
5. Use tables when comparing multiple items
6. Never fabricate data

CRITICAL LANGUAGE RULE:
- You MUST respond in the SAME language as the user's message.
- If the user writes in English, your ENTIRE response MUST be in English.
- If the user writes in Chinese, your ENTIRE response MUST be in Chinese.
- This is mandatory. Do NOT mix languages.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_coin",
            "description": "分析指定币种，生成交易信号卡 + 量化分析 | Analyze a coin and generate a signal card with quantitative analysis",
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
            "name": "query_winrate",
            "description": "查询指定币种的历史胜率和回测数据 | Query historical win rate and backtest data for a coin",
            "parameters": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string", "description": "币种名称 | Coin symbol"},
                    "days": {"type": "integer", "description": "回看天数，默认30 | Lookback days, default 30"}
                },
                "required": ["coin"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_strategy",
            "description": "查看策略性能报告（自适应权重、各因子胜率、市场状态） | View strategy performance report",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_scan_results",
            "description": "查看最近一次全市场扫描结果 | View latest full-market scan results",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回信号数量，默认10 | Number of signals, default 10"}
                },
                "required": []
            }
        }
    },
]

_TOOL_TIMEOUTS = {
    "analyze_coin": 60,
    "query_winrate": 10,
    "query_strategy": 5,
    "query_scan_results": 10,
}


def _detect_language(text: str) -> str:
    cn_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return "zh" if cn_chars > len(text) * 0.15 else "en"


def _execute_tool(tool_name: str, args: dict, lang: str = "zh") -> dict:
    """同步执行 tool"""
    if tool_name == "analyze_coin":
        return _tool_analyze_coin(args.get("coin", "BTC"), lang)
    elif tool_name == "query_winrate":
        return _tool_query_winrate(args.get("coin", "BTC"), args.get("days", 30))
    elif tool_name == "query_strategy":
        return _tool_query_strategy()
    elif tool_name == "query_scan_results":
        return _tool_query_scan_results(args.get("limit", 10))
    return {"error": f"Unknown tool: {tool_name}"}


def _tool_analyze_coin(coin: str, lang: str = "zh") -> dict:
    """分析币种 → 信号卡 + 量化数据"""
    from app.signals.fusion import generate_card_for_chat

    card_event = generate_card_for_chat(coin.upper(), tier="pro", always=True, lang=lang)

    if not card_event:
        err = "Insufficient data to generate signal card" if lang == "en" else "数据不足，无法生成信号卡"
        return {"coin": coin.upper(), "error": err}

    return {
        "coin": coin.upper(),
        "signal_card": card_event,
        "display": card_event.get("display", ""),
    }


def _tool_query_winrate(coin: str, days: int) -> dict:
    """查询历史胜率"""
    result = {"coin": coin.upper(), "days": days}

    # 1. 本地累加
    try:
        from app.signals.adaptive_strategy import get_strategy_engine
        local_wr = get_strategy_engine().get_coin_winrate(coin.upper())
        if local_wr:
            result["local"] = local_wr
    except Exception:
        pass

    # 2. DB 累加
    try:
        from app.signals.settlement import get_accumulated_winrate
        acc = get_accumulated_winrate(coin=coin.upper(), days=days)
        if acc:
            result["accumulated"] = acc
    except Exception:
        pass

    if "local" not in result and "accumulated" not in result:
        result["message"] = "暂无历史胜率数据（需上线积累）"

    return result


def _tool_query_strategy() -> dict:
    """查看策略性能"""
    try:
        from app.signals.adaptive_strategy import get_strategy_engine
        engine = get_strategy_engine()
        return engine.get_performance_report()
    except Exception as e:
        return {"error": str(e)}


def _tool_query_scan_results(limit: int) -> dict:
    """查看最近扫描结果"""
    try:
        from app.signals.settlement import get_latest_scan
        cached = get_latest_scan(max_age_seconds=3600)
        if not cached:
            return {"message": "暂无扫描结果，请先触发一次全市场扫描"}
        return {
            "total_coins": cached["total_coins"],
            "signal_count": cached["signal_count"],
            "scan_time": cached["scan_time"],
            "cached_at": cached["cached_at"],
            "is_stale": cached["is_stale"],
            "signals": cached.get("signals", [])[:limit],
            "displays": cached.get("displays", [])[:limit],
        }
    except Exception as e:
        return {"error": str(e)}


def _get_suggestions(tool_name: str, user_message: str, coin_hint: str = None) -> list:
    """生成推荐追问"""
    lang = _detect_language(user_message)
    coin = coin_hint or ""

    templates = {
        "zh": {
            "analyze_coin": [f"{coin}历史胜率怎么样", "最近有什么信号", "策略表现如何"],
            "query_winrate": [f"分析一下{coin}", f"{coin}最近有什么信号", "策略权重如何"],
            "query_strategy": ["BTC现在怎么样", "最近有什么信号", "ETH分析一下"],
            "query_scan_results": ["BTC分析一下", "策略表现如何", "ETH胜率多少"],
        },
        "en": {
            "analyze_coin": [f"{coin} win rate", "Latest signals", "Strategy performance"],
            "query_winrate": [f"Analyze {coin}", f"{coin} signals", "Strategy weights"],
            "query_strategy": ["How is BTC", "Latest signals", "Analyze ETH"],
            "query_scan_results": ["Analyze BTC", "Strategy performance", "ETH win rate"],
        },
    }
    return templates.get(lang, templates["en"]).get(tool_name, [])


@router.post("/chat")
async def chat(request: Request):
    """信号卡对话式 SSE 流式接口"""
    body = await request.json()
    user_message = body.get("message", "")
    coin_hint = body.get("coin")

    client = get_llm_client()
    model = settings.bigorder_deepseek_model

    async def event_generator():
        user_content = user_message
        if coin_hint:
            lang = _detect_language(user_message)
            prefix = f"[当前关注币种: {coin_hint.upper()}]" if lang == "zh" else f"[Focus coin: {coin_hint.upper()}]"
            user_content = f"{prefix} {user_message}"

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        yield {"event": "thinking", "data": json.dumps({"status": "Analyzing..."}, ensure_ascii=False)}

        # ---- Step 1: LLM 选 tool ----
        try:
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
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}
            return

        msg = route_resp.choices[0].message

        if not msg.tool_calls:
            yield {"event": "content", "data": json.dumps({"text": msg.content or ""}, ensure_ascii=False)}
            yield {"event": "done", "data": "{}"}
            return

        # ---- Step 2: 执行 tool ----
        tc = msg.tool_calls[0]
        tool_name = tc.function.name
        try:
            tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
        except json.JSONDecodeError:
            tool_args = {}

        yield {"event": "tool_call", "data": json.dumps({"tool": tool_name, "args": tool_args}, ensure_ascii=False)}

        loop = asyncio.get_running_loop()
        timeout = _TOOL_TIMEOUTS.get(tool_name, 30)
        user_lang = _detect_language(user_message)
        try:
            tool_result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: _execute_tool(tool_name, tool_args, user_lang)),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            tool_result = {"error": "Query timed out"}

        # ---- Step 2.5: 如果有信号卡，先推送 signal_card 事件 ----
        if tool_name == "analyze_coin" and isinstance(tool_result, dict):
            card_event = tool_result.get("signal_card")
            if card_event:
                yield {"event": "signal_card", "data": json.dumps(card_event, ensure_ascii=False, default=str)}

        yield {"event": "tool_result", "data": json.dumps({"tool": tool_name}, ensure_ascii=False)}

        # ---- Step 3: 流式输出文字解读 ----

        # 构建上下文：去掉 tool_call 历史，避免 DeepSeek 继续调 tool
        analysis_context = tool_result.copy() if isinstance(tool_result, dict) else tool_result
        # 不重复传信号卡数据给 LLM（已经推给前端了），只传分析摘要
        if "signal_card" in analysis_context:
            card = analysis_context["signal_card"]
            analysis_context["signal_summary"] = {
                "direction": card.get("card", {}).get("direction"),
                "grade": card.get("card", {}).get("grade"),
                "confidence": card.get("card", {}).get("confidence"),
                "entry_zone": card.get("card", {}).get("entry_zone"),
                "stop_loss": card.get("card", {}).get("stop_loss"),
                "take_profit": card.get("card", {}).get("take_profit"),
                "risk_reward": card.get("card", {}).get("risk_reward"),
                "sources": card.get("card", {}).get("sources"),
                "display": analysis_context.get("display", ""),
            }
            del analysis_context["signal_card"]

        lang_instruction = "用中文回答" if user_lang == "zh" else "Answer in English"
        final_messages = [
            {
                "role": "system",
                "content": (
                    f"You are a cryptocurrency quantitative analyst. {lang_instruction}.\n"
                    "Based on the data provided, write a concise analysis.\n"
                    "If a signal card summary is provided, interpret it clearly.\n"
                    "For C-grade signals, warn about low confidence.\n"
                    "Never fabricate data."
                )
            },
            {
                "role": "user",
                "content": (
                    f"Question: {user_message}\n\n"
                    f"Data:\n{json.dumps(analysis_context, ensure_ascii=False, default=str, indent=2)}\n\n"
                    "Write a clear analysis."
                )
            }
        ]

        try:
            final_resp = await client.chat.completions.create(
                model=model,
                messages=final_messages,
                max_tokens=1000,
                stream=True,
            )
            async for chunk in final_resp:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield {"event": "content", "data": json.dumps({"text": delta.content}, ensure_ascii=False)}
        except Exception as e:
            yield {"event": "error", "data": json.dumps({"error": str(e)}, ensure_ascii=False)}

        # 推荐问题
        coin = tool_args.get("coin", coin_hint)
        suggestions = _get_suggestions(tool_name, user_message, coin)
        if suggestions:
            yield {"event": "suggestions", "data": json.dumps(
                {"type": "suggestions", "suggestions": suggestions}, ensure_ascii=False
            )}

        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(event_generator())
