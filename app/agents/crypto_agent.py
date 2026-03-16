from typing import List, Dict, Any, Optional, Generator, AsyncGenerator
from langchain.agents import create_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
# ConversationBufferMemory is not available in LangChain 1.x, using alternative approach
# from langchain.memory import ConversationBufferMemory
from langchain_core.tools import Tool
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage

from app.core.config import get_settings
from app.agents.tools.market_data import (
    MarketDataTool,
    KlineDataTool,
    HeaderDataTool
)
from app.agents.tools.news_data import (
    NewsDataTool,
    RecentNewsTool,
    NewsCountTool
)
from app.agents.tools.derivatives_data import (
    DerivativesDataTool,
    BuySellRatioTool,
    OpenInterestTool,
    TradingVolumeTool,
    FundingRateTool
)
from app.agents.tools.analysis_tools import (
    TechnicalAnalysisTool,
    NewsAnalysisTool,
    DerivativesAnalysisTool,
    QuantitativeAnalysisTool,
    SummaryAnalysisTool
)
from app.agents.tools.prompt_tools import (
    PromptBuilderTool,
    SystemPromptTool
)
from app.utils.validators import validate_symbol, validate_language, validate_question

settings = get_settings()


class CryptoAnalystAgent:
    """加密货币分析智能体"""

    def __init__(self):
        self.settings = settings
        self.llm = self._create_llm()
        self.tools = self._create_tools()
        # Memory is handled differently in LangChain 1.x
        # We'll maintain chat history as a list of messages
        self.chat_history = []
        self.agent = self._create_agent()

    def _create_llm(self) -> ChatOpenAI:
        """创建LLM实例"""
        return ChatOpenAI(
            model=settings.deepseek_model,
            openai_api_key=settings.deepseek_api_key,
            openai_api_base=settings.deepseek_api_base,
            temperature=settings.llm_temperature,
            streaming=True
        )

    def _create_tools(self) -> List[Tool]:
        """创建所有工具"""
        tools = []

        # 市场数据工具
        tools.extend([
            Tool.from_function(
                func=MarketDataTool()._run,
                name="get_market_data",
                description="获取加密货币的市场数据，包括K线数据和基本信息"
            ),
            Tool.from_function(
                func=KlineDataTool()._run,
                name="get_kline_data",
                description="获取加密货币的K线数据（价格历史数据）"
            ),
            Tool.from_function(
                func=HeaderDataTool()._run,
                name="get_header_data",
                description="获取加密货币的基本信息，包括价格、市值、排名、供应量等"
            )
        ])

        # 新闻数据工具
        tools.extend([
            Tool.from_function(
                func=NewsDataTool()._run,
                name="get_news_data",
                description="获取加密货币相关的新闻数据"
            ),
            Tool.from_function(
                func=RecentNewsTool()._run,
                name="get_recent_news",
                description="获取加密货币的近期新闻（默认最近20条）"
            ),
            Tool.from_function(
                func=NewsCountTool()._run,
                name="get_news_count",
                description="统计加密货币相关新闻的数量"
            )
        ])

        # 衍生品数据工具
        tools.extend([
            Tool.from_function(
                func=DerivativesDataTool()._run,
                name="get_derivatives_data",
                description="获取加密货币的衍生品市场数据，包括买卖比例、持仓量、交易量、资金费率等"
            ),
            Tool.from_function(
                func=BuySellRatioTool()._run,
                name="get_buy_sell_ratio",
                description="获取加密货币的买卖比例数据，反映市场多空力量对比"
            ),
            Tool.from_function(
                func=OpenInterestTool()._run,
                name="get_open_interest",
                description="获取加密货币的持仓量数据，反映市场参与度和多空博弈强度"
            ),
            Tool.from_function(
                func=TradingVolumeTool()._run,
                name="get_trading_volume",
                description="获取加密货币的交易量数据，反映市场活跃度和流动性"
            ),
            Tool.from_function(
                func=FundingRateTool()._run,
                name="get_funding_rate",
                description="获取加密货币的资金费率数据，反映永续合约市场的多空平衡"
            )
        ])

        # 分析工具
        tools.extend([
            Tool.from_function(
                func=TechnicalAnalysisTool()._run,
                name="technical_analysis",
                description="基于加密货币的K线数据进行技术分析，包括趋势、支撑阻力、技术指标等"
            ),
            Tool.from_function(
                func=NewsAnalysisTool()._run,
                name="news_analysis",
                description="分析加密货币相关的新闻数据，解读市场情绪和事件影响"
            ),
            Tool.from_function(
                func=DerivativesAnalysisTool()._run,
                name="derivatives_analysis",
                description="分析加密货币的衍生品市场数据，包括多空结构、资金流向、市场情绪等"
            ),
            Tool.from_function(
                func=QuantitativeAnalysisTool()._run,
                name="quantitative_analysis",
                description="基于六因子量化评分模型进行加密货币的量化分析，提供概率判断"
            ),
            Tool.from_function(
                func=SummaryAnalysisTool()._run,
                name="summary_analysis",
                description="综合所有分析给出加密货币的最终总结"
            )
        ])

        # 提示词工具
        tools.extend([
            Tool.from_function(
                func=PromptBuilderTool()._run,
                name="build_analysis_prompt",
                description="构建加密货币分析提示词模板"
            ),
            Tool.from_function(
                func=SystemPromptTool()._run,
                name="get_system_prompt",
                description="获取系统提示词模板，定义AI助手的角色和行为准则"
            )
        ])

        return tools

    def _create_agent(self):
        """创建智能体（LangChain 1.x版本）"""
        # 构建工具描述字符串
        tool_descriptions = []
        for tool in self.tools:
            tool_descriptions.append(f"- {tool.name}: {tool.description}")
        tools_list_str = "\n".join(tool_descriptions)

        # 系统提示词（包含工具列表）
        system_prompt = """你是一位专业的加密货币分析助手，拥有多种分析工具。

你的角色：
1. 专业、谨慎、遵守合规要求的加密货币分析师
2. 不提供投资建议或确定性结论
3. 基于数据进行分析，强调风险
4. 使用谨慎措辞，避免绝对化语言

可用工具：
{tools_list}

工作流程：
1. 理解用户问题，确定需要哪些数据和分析
2. 选择合适的工具获取数据或进行分析
3. 基于工具返回的结果，提供综合分析
4. 始终提醒用户加密货币市场的高风险性

重要规则：
- 在获得足够信息后，直接给出最终答案，不要无限调用工具
- 对于简单问题（如"介绍BTC"），调用1-2个关键工具获取基本信息即可
- 工具调用次数最多不超过5次
- 当你有足够信息回答问题时就停止调用工具

请根据用户问题选择合适的工具，并提供有价值的分析。""".format(tools_list=tools_list_str)

        # 使用create_agent创建智能体（LangChain 1.x API）
        agent = create_agent(
            model=self.llm,
            tools=self.tools,
            system_prompt=system_prompt,
            debug=self.settings.debug
        )

        return agent

    def analyze(
        self,
        symbol: str,
        question: str,
        lang: str = "zh"
    ) -> Dict[str, Any]:
        """执行分析"""
        # 验证输入
        symbol = validate_symbol(symbol)
        question = validate_question(question)
        lang = validate_language(lang)

        # 构建用户输入
        user_input = f"请分析{symbol}：{question}（使用语言：{lang}）"

        # 构建消息列表（包含历史消息和新用户消息）
        messages = []
        # 添加历史消息
        messages.extend(self.chat_history)
        # 添加新用户消息
        messages.append(HumanMessage(content=user_input))

        # 执行智能体（LangChain 1.x API）
        result = self.agent.invoke({"messages": messages})

        # 提取AI响应（最后一条消息）
        ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
        response = ai_messages[-1].content if ai_messages else ""

        # 更新聊天历史
        self.chat_history.extend([HumanMessage(content=user_input), ai_messages[-1] if ai_messages else AIMessage(content="")])

        return {
            "symbol": symbol,
            "question": question,
            "response": response,
            "intermediate_steps": [],  # LangChain 1.x doesn't provide intermediate steps directly
            "lang": lang
        }

    def analyze_stream(
        self,
        symbol: str,
        question: str,
        lang: str = "zh"
    ) -> Generator[str, None, None]:
        """流式分析"""
        print(f"DEBUG: Agent type: {type(self.agent)}")
        # 验证输入
        symbol = validate_symbol(symbol)
        question = validate_question(question)
        lang = validate_language(lang)

        # 构建用户输入
        user_input = f"请分析{symbol}：{question}（使用语言：{lang}）"

        # 构建消息列表
        messages = []
        messages.extend(self.chat_history)
        messages.append(HumanMessage(content=user_input))

        # 执行流式智能体（LangChain 1.x API）
        accumulated_response = ""
        # 打印调试信息，确认流式输出结构
        print("DEBUG: Starting agent stream...")
        
        for chunk in self.agent.stream({"messages": messages}):
            # 兼容 LangGraph 输出格式：chunk 是 {node_name: state}
            # 我们需要遍历所有节点（通常是 'agent' 或 'tools'）
            for node_name, node_content in chunk.items():
                if "messages" in node_content:
                    # 获取新增的AI消息
                    new_ai_messages = [
                        msg for msg in node_content["messages"]
                        if isinstance(msg, AIMessage) and msg.content
                    ]
                    for msg in new_ai_messages:
                        if msg.content:
                            # 只有当内容是新的或者是最后一条消息时才输出
                            # 简单策略：输出所有非空AI消息内容
                            # 注意：LangGraph可能返回完整的历史消息，我们需要去重
                            # 这里简单假设最后一条是新的
                            accumulated_response = msg.content
                            yield msg.content

        # 流式结束后，更新聊天历史
        if accumulated_response:
            self.chat_history.extend([
                HumanMessage(content=user_input),
                AIMessage(content=accumulated_response)
            ])

    async def analyze_stream_async(
        self,
        symbol: str,
        question: str,
        lang: str = "zh"
    ) -> AsyncGenerator[str, None]:
        """异步流式分析 (使用 astream_events 获取 token 级输出)"""
        print(f"DEBUG: Async Agent type: {type(self.agent)}")
        
        # 验证输入
        symbol = validate_symbol(symbol)
        question = validate_question(question)
        lang = validate_language(lang)

        # 构建用户输入
        user_input = f"请分析{symbol}：{question}（使用语言：{lang}）"

        # 构建消息列表
        messages = []
        messages.extend(self.chat_history)
        messages.append(HumanMessage(content=user_input))

        accumulated_response = ""
        print("DEBUG: Starting async agent stream (astream_events)...")

        try:
            # 使用 astream_events 获取细粒度事件（包括 token 生成）
            async for event in self.agent.astream_events(
                {"messages": messages},
                version="v1"
            ):
                kind = event["event"]
                
                # 1. 捕获 LLM 生成的 token
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        accumulated_response += content
                        yield content
                
                # 2. 捕获工具调用开始（可选，增加用户反馈）
                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    # 过滤掉一些内部工具或不重要的工具显示
                    if tool_name and not tool_name.startswith("_"):
                        yield f"\n> 正在调用工具: {tool_name}...\n"
                        
                # 3. 捕获工具调用结束
                elif kind == "on_tool_end":
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"> 工具 {tool_name} 执行完成。\n"

        except Exception as e:
            print(f"Error in analyze_stream_async: {e}")
            yield f"\n[系统错误: 分析过程中发生异常 - {str(e)}]\n"

        # 流式结束后，更新聊天历史
        if accumulated_response:
            self.chat_history.extend([
                HumanMessage(content=user_input),
                AIMessage(content=accumulated_response)
            ])

    def chat(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        lang: str = "zh"
    ) -> Dict[str, Any]:
        """对话式交互"""
        # 验证输入
        message = validate_question(message)
        lang = validate_language(lang)

        # 构建消息列表
        messages = []
        messages.extend(self.chat_history)
        messages.append(HumanMessage(content=message))

        # 执行智能体（LangChain 1.x API）
        result = self.agent.invoke({"messages": messages})

        # 提取AI响应
        ai_messages = [msg for msg in result["messages"] if isinstance(msg, AIMessage)]
        response = ai_messages[-1].content if ai_messages else ""

        # 更新聊天历史
        self.chat_history.extend([HumanMessage(content=message), ai_messages[-1] if ai_messages else AIMessage(content="")])

        return {
            "message": message,
            "response": response,
            "conversation_id": conversation_id,
            "lang": lang
        }

    def chat_stream(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        lang: str = "zh"
    ) -> Generator[str, None, None]:
        """流式对话"""
        # 验证输入
        message = validate_question(message)
        lang = validate_language(lang)

        # 构建消息列表
        messages = []
        messages.extend(self.chat_history)
        messages.append(HumanMessage(content=message))

        # 执行流式智能体（LangChain 1.x API）
        accumulated_response = ""
        for chunk in self.agent.stream({"messages": messages}):
            # chunk可能包含消息更新，我们提取AI消息内容
            if "messages" in chunk:
                # 获取新增的AI消息
                new_ai_messages = [
                    msg for msg in chunk["messages"]
                    if isinstance(msg, AIMessage) and msg.content
                ]
                for msg in new_ai_messages:
                    if msg.content:
                        accumulated_response = msg.content
                        yield msg.content

        # 流式结束后，更新聊天历史
        if accumulated_response:
            self.chat_history.extend([
                HumanMessage(content=message),
                AIMessage(content=accumulated_response)
            ])

    async def chat_stream_async(
        self,
        message: str,
        conversation_id: Optional[str] = None,
        lang: str = "zh"
    ) -> AsyncGenerator[str, None]:
        """异步流式对话 (使用 astream_events)"""
        # 验证输入
        message = validate_question(message)
        lang = validate_language(lang)

        # 构建消息列表
        messages = []
        messages.extend(self.chat_history)
        messages.append(HumanMessage(content=message))

        accumulated_response = ""
        
        try:
            # 使用 astream_events 获取细粒度事件
            async for event in self.agent.astream_events(
                {"messages": messages},
                version="v1"
            ):
                kind = event["event"]
                
                # 1. 捕获 LLM 生成的 token
                if kind == "on_chat_model_stream":
                    content = event["data"]["chunk"].content
                    if content:
                        accumulated_response += content
                        yield content
                
                # 2. 捕获工具调用开始
                elif kind == "on_tool_start":
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"\n> 正在调用工具: {tool_name}...\n"
                        
                # 3. 捕获工具调用结束
                elif kind == "on_tool_end":
                    tool_name = event["name"]
                    if tool_name and not tool_name.startswith("_"):
                        yield f"> 工具 {tool_name} 执行完成。\n"

        except Exception as e:
            print(f"Error in chat_stream_async: {e}")
            yield f"\n[系统错误: 对话过程中发生异常 - {str(e)}]\n"

        # 流式结束后，更新聊天历史
        if accumulated_response:
            self.chat_history.extend([
                HumanMessage(content=message),
                AIMessage(content=accumulated_response)
            ])

    def get_available_tools(self) -> List[Dict[str, Any]]:
        """获取可用工具列表"""
        tools_info = []
        for tool in self.tools:
            tools_info.append({
                "name": tool.name,
                "description": tool.description,
                "args_schema": str(tool.args) if hasattr(tool, 'args') else None
            })
        return tools_info

    def clear_memory(self):
        """清除对话记忆"""
        self.chat_history = []


# 全局智能体实例
crypto_agent = CryptoAnalystAgent()