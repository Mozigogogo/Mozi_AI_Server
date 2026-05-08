"""加密货币分析 Agent - 基于 Skill 架构"""
import asyncio
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app.skills.base import IntentInfo
from app.skills.intent_analyzer import IntentAnalyzer
from app.skills.skill_router import SkillRouter
from app.skills.response_generator import ResponseGenerator
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
        mode: str = "chat"
    ) -> AsyncGenerator[str, None]:
        """
        异步回答用户问题（流式）

        Args:
            question: 用户问题
            mode: 模式（chat/think）

        Yields:
            str: 流式回答内容
        """
        async with self.semaphore:
            try:
                print(f"\n=== 新请求 ===")
                print(f"问题: {question}")
                print(f"模式: {mode}")

                # 步骤1：意图识别（LLM）
                print(f"\n[步骤1] 意图识别...")
                intent = await self.intent_analyzer.analyze(question)
                print(f"意图分析结果:")
                print(f"  - 语言: {intent.language}")
                print(f"  - 意图类型: {intent.intent_type}")
                print(f"  - 币种: {intent.coin_symbol}")
                print(f"  - 需要的 API: {intent.required_apis}")

                # 检查是否是简单对话
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

                # 兜底：确保实时价格存在（header API 可能超时），最多重试3次
                has_realtime_price = False
                data = skill_result.data
                if isinstance(data, dict):
                    rt = data.get("实时数据") or data.get("实时价格")
                    if isinstance(rt, dict) and rt.get("当前价格"):
                        has_realtime_price = True

                if not has_realtime_price:
                    print(f"  ⚠️ 缺少实时价格，尝试单独获取 header 数据（最多重试3次）...")
                    for retry in range(3):
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
                            else:
                                print(f"  ⚠️ 第{retry+1}次 header 返回空数据，重试...")
                        except Exception as e:
                            print(f"  ⚠️ 第{retry+1}次 header 获取失败: {e}")
                    else:
                        # 3次都失败，直接报错不让 LLM 对空数据幻觉
                        print(f"  ❌ header API 3次重试均失败，返回错误提示")
                        if intent.language == "zh":
                            yield f"抱歉，实时行情数据暂时不可用（API 连接超时），请稍后再试。"
                        else:
                            yield f"Sorry, real-time market data is temporarily unavailable (API timeout). Please try again later."
                        return

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
