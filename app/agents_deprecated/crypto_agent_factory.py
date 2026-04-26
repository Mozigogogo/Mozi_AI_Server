"""Agent 工厂 - 创建模式专属的 Agent 实例"""
from typing import List, Dict, Any, Optional, Generator, AsyncGenerator

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage, BaseMessage
from langchain.agents import create_agent

from app.core.config import get_settings
from app.agents.tool_registry import ToolRegistry
from app.agents.prompts.chat_prompt import build_chat_system_prompt
from app.agents.prompts.analysis_prompt import build_analysis_system_prompt
from app.utils.validators import validate_symbol, validate_language, validate_question

settings = get_settings()

CRYPTO_KEYWORDS = [
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "DOT", "AVAX", "MATIC",
    "LTC", "LINK", "UNI", "ATOM", "ETC", "XLM", "FIL", "ICP", "ALGO", "VET",
    "USDT", "USDC", "SHIB", "PEPE", "ARB", "OP", "APT", "SUI", "NEAR", "TON",
    "价格", "行情", "走势", "市值", "涨跌", "分析", "跌幅", "涨幅",
    "K线", "持仓", "费率", "买卖比", "交易量", "新闻", "换手率",
    "技术面", "支撑", "阻力", "RSI", "均线", "MACD", "布林",
    "多头", "空头", "做多", "做空", "爆仓", "清算",
    "资金费率", "持仓量", "衍生品", "永续", "合约",
    "price", "market", "analysis", "volume", "trend", "bullish", "bearish",
    "support", "resistance", "funding", "open interest",
]


def _is_crypto_query(message: str) -> bool:
    """判断是否是加密货币相关问题"""
    upper = message.upper()
    return any(kw.upper() in upper for kw in CRYPTO_KEYWORDS)


class CryptoAgent:
    """模式专属的加密货币分析 Agent"""

    def __init__(self, mode: str, tools: List, temperature: float, max_tokens: int):
        self.mode = mode
        self.tools = tools
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.chat_history: List[BaseMessage] = []

        self.llm = self._create_llm()
        self.system_prompt = self._build_system_prompt()
        self.agent = self._create_agent()

    def _create_llm(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=settings.deepseek_model,
            openai_api_key=settings.deepseek_api_key,
            openai_api_base=settings.deepseek_api_base,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            streaming=True,
        )

    def _build_system_prompt(self) -> str:
        if self.mode == "chat":
            return build_chat_system_prompt(self.tools)
        else:
            return build_analysis_system_prompt(self.tools)

    def _create_agent(self):
        return create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=self.system_prompt,
            debug=settings.debug,
        )

    def _has_tool_calls(self, result: dict) -> bool:
        """检查 Agent 结果中是否包含工具调用"""
        for msg in result.get("messages", []):
            if isinstance(msg, ToolMessage):
                return True
            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                return True
        return False

    def _invoke_with_tool_enforcement(self, messages: list, max_retries: int = None) -> dict:
        """调用 Agent 并在必要时强制工具调用"""
        if max_retries is None:
            max_retries = settings.tool_call_max_retries

        result = self.agent.invoke({"messages": messages})

        user_msg = ""
        if messages:
            last = messages[-1]
            user_msg = last.content if isinstance(last, HumanMessage) else ""

        if (not self._has_tool_calls(result)
                and _is_crypto_query(user_msg)
                and max_retries > 0):
            enforcement_msg = HumanMessage(
                content="IMPORTANT: You must call at least one tool to fetch real data before answering this question. Do not answer from memory."
            )
            retry_messages = messages[:-1] + [enforcement_msg, messages[-1]]
            return self._invoke_with_tool_enforcement(retry_messages, max_retries - 1)

        return result

    # --- Analysis mode methods ---

    def analyze(self, symbol: str, question: str, lang: str = "zh") -> Dict[str, Any]:
        symbol = validate_symbol(symbol)
        question = validate_question(question)
        lang = validate_language(lang)

        user_input = f"请分析{symbol}：{question}（使用语言：{lang}）"
        messages = list(self.chat_history)
        messages.append(HumanMessage(content=user_input))

        result = self._invoke_with_tool_enforcement(messages)

        ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
        response = ai_messages[-1].content if ai_messages else ""

        self.chat_history.extend([
            HumanMessage(content=user_input),
            ai_messages[-1] if ai_messages else AIMessage(content=""),
        ])

        return {
            "symbol": symbol,
            "question": question,
            "response": response,
            "intermediate_steps": [],
            "lang": lang,
        }

    async def analyze_stream_async(
        self, symbol: str, question: str, lang: str = "zh"
    ) -> AsyncGenerator[str, None]:
        symbol = validate_symbol(symbol)
        question = validate_question(question)
        lang = validate_language(lang)

        user_input = f"请分析{symbol}：{question}（使用语言：{lang}）"
        messages = list(self.chat_history)
        messages.append(HumanMessage(content=user_input))

        accumulated_response = ""
        tool_called = False

        try:
            async for event in self.agent.astream_events(
                {"messages": messages}, version="v1"
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        accumulated_response += content
                        yield content

                elif kind == "on_tool_start":
                    tool_called = True
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"\n> 正在调用工具: {tool_name}...\n"

                elif kind == "on_tool_end":
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"> 工具 {tool_name} 执行完成。\n"

        except Exception as e:
            yield f"\n[系统错误: 分析过程中发生异常 - {str(e)}]\n"

        # 流式结束后如果没调用工具且是加密货币问题，提示用户
        if not tool_called and _is_crypto_query(user_input):
            yield "\n[提示: 本次回答未获取实时数据，建议重新提问以获取最新数据]\n"

        if accumulated_response:
            self.chat_history.extend([
                HumanMessage(content=user_input),
                AIMessage(content=accumulated_response),
            ])

    # --- Chat mode methods ---

    def chat(
        self, message: str, conversation_id: Optional[str] = None, lang: str = "zh"
    ) -> Dict[str, Any]:
        message = validate_question(message)
        lang = validate_language(lang)

        messages = list(self.chat_history)
        messages.append(HumanMessage(content=message))

        result = self._invoke_with_tool_enforcement(messages)

        ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
        response = ai_messages[-1].content if ai_messages else ""

        self.chat_history.extend([
            HumanMessage(content=message),
            ai_messages[-1] if ai_messages else AIMessage(content=""),
        ])

        return {
            "message": message,
            "response": response,
            "conversation_id": conversation_id,
            "lang": lang,
        }

    async def chat_stream_async(
        self, message: str, conversation_id: Optional[str] = None, lang: str = "zh"
    ) -> AsyncGenerator[str, None]:
        message = validate_question(message)
        lang = validate_language(lang)

        messages = list(self.chat_history)
        messages.append(HumanMessage(content=message))

        accumulated_response = ""
        tool_called = False

        try:
            async for event in self.agent.astream_events(
                {"messages": messages}, version="v1"
            ):
                kind = event["event"]

                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        accumulated_response += content
                        yield content

                elif kind == "on_tool_start":
                    tool_called = True
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"\n> 正在调用工具: {tool_name}...\n"

                elif kind == "on_tool_end":
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"> 工具 {tool_name} 执行完成。\n"

        except Exception as e:
            yield f"\n[系统错误: 对话过程中发生异常 - {str(e)}]\n"

        if not tool_called and _is_crypto_query(message):
            yield "\n[提示: 本次回答未获取实时数据，建议重新提问以获取最新数据]\n"

        if accumulated_response:
            self.chat_history.extend([
                HumanMessage(content=message),
                AIMessage(content=accumulated_response),
            ])

    # --- Utility methods ---

    def get_available_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "args_schema": str(tool.args) if hasattr(tool, "args") else None,
            }
            for tool in self.tools
        ]

    def clear_memory(self):
        self.chat_history = []


class AgentFactory:
    """Agent 工厂 - 创建和缓存模式专属 Agent 实例"""

    _shared_tools = None
    _chat_instance: CryptoAgent = None
    _analysis_instance: CryptoAgent = None

    @classmethod
    def _get_tools(cls):
        if cls._shared_tools is None:
            cls._shared_tools = ToolRegistry.create_tools()
        return cls._shared_tools

    @classmethod
    def get_chat_agent(cls) -> CryptoAgent:
        if cls._chat_instance is None:
            cls._chat_instance = CryptoAgent(
                mode="chat",
                tools=cls._get_tools(),
                temperature=settings.chat_llm_temperature,
                max_tokens=settings.chat_llm_max_tokens,
            )
        return cls._chat_instance

    @classmethod
    def get_analysis_agent(cls) -> CryptoAgent:
        if cls._analysis_instance is None:
            cls._analysis_instance = CryptoAgent(
                mode="analysis",
                tools=cls._get_tools(),
                temperature=settings.analysis_llm_temperature,
                max_tokens=settings.analysis_llm_max_tokens,
            )
        return cls._analysis_instance


# 全局 Agent 实例（懒加载）
chat_agent = AgentFactory.get_chat_agent()
analysis_agent = AgentFactory.get_analysis_agent()
