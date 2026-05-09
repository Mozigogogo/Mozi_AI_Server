"""加密货币分析 Agent - 基于 Skill 架构"""
import asyncio
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.skills.base import IntentInfo
from app.skills.intent_analyzer import IntentAnalyzer
from app.skills.skill_router import SkillRouter
from app.skills.response_generator import ResponseGenerator
from app.core.session import session_manager
from app.core.config import get_settings
from app.services.data_service import get_header_data

settings = get_settings()


class CryptoAnalystAgent:
    """加密货币分析 Agent - 基于 Skill 架构"""

    def __init__(self):
        # 初始化 LLM 客户端（使用 OpenAI 兼容模式）
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_api_base
        )

        # 初始化组件
        self.intent_analyzer = IntentAnalyzer(self.client)
        self.skill_router = SkillRouter()
        self.response_generator = ResponseGenerator(self.client)

        # 信号量控制并发
        self.semaphore = asyncio.Semaphore(50)

    async def answer(
        self,
        question: str,
        mode: str = "chat",
        symbol: str = None,
        conversation_id: str = None
    ) -> AsyncGenerator[str, None]:
        """
        异步回答用户问题（流式）

        Args:
            question: 用户问题
            mode: 模式（chat/think）
            symbol: 指定币种符号
            conversation_id: 会话ID，用于上下文记忆和用户隔离
        """
        async with self.semaphore:
            try:
                print(f"\n=== 新请求 === 问题: {question} 模式: {mode} 指定币种: {symbol} 会话: {conversation_id}")

                # 加载会话上下文（用户隔离）
                session = session_manager.get(conversation_id) if conversation_id else None
                history_questions = session["questions"] if session else []
                last_coin = session["coin_symbol"] if session else None

                # 步骤1：意图识别（LLM），传入历史问题帮助理解上下文
                intent = await self.intent_analyzer.analyze(question, history_questions)

                # 币种识别策略（与 chat 接口一致）：
                # simple_chat → 直接返回通用回答（无论有没有 symbol）
                # 非闲聊意图但没币种 → 用会话/参数兜底
                if intent.intent_type == "simple_chat" and not intent.coin_symbol:
                    print(f"  检测到简单对话/无关问题，返回通用回答")
                    # 仍然保存问题到会话（用户可能在之后问到币种）
                    if conversation_id:
                        session_manager.update(conversation_id, question=question)
                    greeting = self.response_generator.get_greeting(intent.language)
                    yield greeting
                    return

                # 币种来源优先级：问题文本 > 参数 symbol > 会话记忆
                if not intent.coin_symbol:
                    if symbol:
                        symbol = symbol.strip().upper()
                        if ":" in symbol:
                            symbol = symbol.split(":")[-1]
                        intent.coin_symbol = symbol
                        print(f"  使用参数币种兜底: {symbol}")
                    elif last_coin:
                        intent.coin_symbol = last_coin
                        print(f"  使用会话记忆币种: {last_coin}")

                if symbol and intent.coin_symbol != symbol:
                    print(f"  ⚠️ 币种不一致: 意图识别={intent.coin_symbol}, 参数={symbol}, 使用意图识别结果")

                # 如果有币种但意图被误判为 simple_chat，修正为综合分析
                if intent.intent_type == "simple_chat" and intent.coin_symbol:
                    print(f"  ⚠️ 有币种但意图为 simple_chat，修正为 analyze_comprehensive")
                    intent.intent_type = "analyze_comprehensive"

                # 二次检查：LLM未识别币种但问题中可能包含币种（如 pepe、ton 等）
                # 从问题中提取候选词，通过 API 动态验证是否为有效币种，不写死任何币种
                if intent.intent_type == "simple_chat" and not intent.coin_symbol:
                    import re
                    from app.services.data_service import validate_coin_exists
                    candidates = set(w.upper() for w in re.findall(r'[A-Za-z]{2,6}', question))
                    for sym in candidates:
                        try:
                            if validate_coin_exists(sym):
                                intent.coin_symbol = sym
                                intent.intent_type = "analyze_comprehensive"
                                print(f"  ⚠️ 二次验证: API确认 {sym} 为有效币种，修正意图")
                                break
                        except Exception:
                            pass

                # 补充 required_apis（LLM 可能返回空列表）
                if intent.coin_symbol and not intent.required_apis:
                    intent.required_apis = ["get_header_data", "get_kline_data", "get_buy_sell_ratio", "get_funding_rate"]

                print(f"  意图: {intent.intent_type} 币种: {intent.coin_symbol} APIs: {intent.required_apis}")

                # 检查是否是简单对话（无币种）
                if intent.intent_type == "simple_chat":
                    print(f"\n[步骤2] 检测到简单对话")
                    greeting = self.response_generator.get_greeting(intent.language)
                    yield greeting
                    return

                # 检查是否有币种
                if not intent.coin_symbol:
                    print(f"\n[步骤2] 未检测到币种")
                    msg = self.response_generator.get_no_symbol_message(intent.language)
                    yield msg
                    return

                # 步骤2：Skill 路由（精准匹配）
                print(f"\n[步骤2] Skill 路由...")
                skill = self.skill_router.route(intent, mode)
                print(f"匹配到 Skill: {skill.name}")

                # 步骤3：执行 Skill（只调用必要的 API）
                print(f"\n[步骤3] 执行 Skill: {skill.name}")
                skill_result = await skill.execute_async(intent.coin_symbol, intent)
                print(f"Skill 执行结果:")
                print(f"  - 调用的 API: {skill_result.api_calls}")
                print(f"  - 时间戳: {skill_result.timestamp}")

                # 检查数据是否全空（API 可能因币种符号错误而全部失败）
                if not skill_result.api_calls:
                    print(f"  ⚠️ 所有 API 调用失败，币种符号可能有误: {intent.coin_symbol}")
                    if intent.language == "zh":
                        yield f"抱歉，无法获取 {intent.coin_symbol} 的市场数据，请检查币种符号是否正确（如 BTC、ETH、SOL）。"
                    else:
                        yield f"Sorry, unable to fetch market data for {intent.coin_symbol}. Please verify the symbol (e.g., BTC, ETH, SOL)."
                    return

                # 兜底：尝试补充实时价格（header API 可能超时）
                data = skill_result.data
                has_realtime_price = False
                if isinstance(data, dict):
                    rt = data.get("实时数据") or data.get("实时价格")
                    if isinstance(rt, dict) and rt.get("当前价格"):
                        has_realtime_price = True

                if not has_realtime_price:
                    print(f"  ⚠️ 缺少实时价格，尝试单独获取 header 数据...")
                    for retry in range(2):
                        try:
                            header = await asyncio.to_thread(get_header_data, intent.coin_symbol)
                            if header and isinstance(header, dict) and header.get("currentPrice"):
                                price_info = {
                                    "当前价格": header.get("currentPrice"),
                                    "24h涨跌幅": header.get("priceChangePercentage_24h"),
                                    "24h最高": header.get("high_24h"),
                                    "24h最低": header.get("low_24h"),
                                }
                                skill_result.data["实时数据"] = price_info
                                if "get_header_data" not in skill_result.api_calls:
                                    skill_result.api_calls.append("get_header_data")
                                print(f"  ✅ 兜底获取成功(第{retry+1}次): 价格 {header.get('currentPrice')}")
                                break
                        except Exception as e:
                            print(f"  ⚠️ 第{retry+1}次 header 获取失败: {e}")

                    # 补充失败不报错，用已有数据继续生成回答
                    if not has_realtime_price and "实时数据" not in skill_result.data:
                        print(f"  ⚠️ header API 不可用，使用已有 {len(skill_result.api_calls)} 个 API 数据继续回答")

                # 步骤4：生成回答（使用用户语言）
                print(f"\n[步骤4] 生成回答...")

                # 根据意图类型决定模式
                if intent.intent_type == "analyze_quantitative":
                    response_mode = "quantitative"
                else:
                    response_mode = mode

                # 流式生成回答
                full_response = []
                async for chunk in self.response_generator.generate_response_stream(
                    skill_result,
                    intent,
                    response_mode
                ):
                    full_response.append(chunk)
                    yield chunk  # 立即发送每个chunk

                response = "".join(full_response)
                print(f"生成的回答: {response[:100]}...")

                # 保存会话：只存问题 + 识别到的币种
                if conversation_id:
                    session_manager.update(
                        conversation_id,
                        coin_symbol=intent.coin_symbol,
                        question=question
                    )

                # 生成推荐问题（dict 类型，与 str 区分）
                if intent.coin_symbol and intent.intent_type != "simple_chat":
                    suggestions = self.response_generator.get_suggestions(
                        intent.intent_type, intent.coin_symbol, intent.language
                    )
                    yield {"type": "suggestions", "suggestions": suggestions}

                print(f"\n=== 请求完成 ===\n")

            except Exception as e:
                print(f"\n处理请求出错: {e}")
                import traceback
                traceback.print_exc()

                # 根据错误类型返回友好的错误消息
                error_msg = ""
                error_type = type(e).__name__
                error_detail = str(e)

                if "502" in error_detail or "Bad Gateway" in error_detail:
                    error_msg = "抱歉，外部数据服务暂时不可用，请稍后再试。"
                elif "timeout" in error_detail.lower() or "超时" in error_detail:
                    error_msg = "抱歉，请求超时，请稍后再试。"
                elif "Connection" in error_type or "Network" in error_type:
                    error_msg = "抱歉，网络连接异常，请检查网络设置。"
                elif "解析" in error_detail or "parse" in error_detail.lower():
                    error_msg = "抱歉，数据解析失败，请稍后再试。"
                else:
                    error_msg = "抱歉，处理您的请求时出现了错误，请稍后再试。"

                # 如果有语言信息，使用用户友好的消息
                yield error_msg

    async def test_intent_analysis(self, question: str) -> IntentInfo:
        """
        测试意图分析

        Args:
            question: 用户问题

        Returns:
            IntentInfo: 意图信息
        """
        return await self.intent_analyzer.analyze(question)


# 创建全局 Agent 实例
crypto_agent = CryptoAnalystAgent()
