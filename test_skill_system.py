"""测试 Skill 系统"""
import asyncio
from app.skills.agent import crypto_agent


async def test_intent_analysis():
    """测试意图分析"""
    print("=" * 60)
    print("测试 1: 意图分析")
    print("=" * 60)

    test_questions = [
        "ETH 涨势怎么样？",
        "What is the current price of BTC?",
        "分析一下 BTC 当前的技术面",
        "SOL 最近有什么新闻？",
        "Hello, how are you?",
    ]

    for question in test_questions:
        print(f"\n问题: {question}")
        intent = await crypto_agent.test_intent_analysis(question)
        print(f"语言: {intent.language}")
        print(f"意图类型: {intent.intent_type}")
        print(f"币种: {intent.coin_symbol}")
        print(f"需要的 API: {intent.required_apis}")
        print("-" * 40)


async def test_answer_chat_mode():
    """测试对话模式"""
    print("\n" + "=" * 60)
    print("测试 2: 对话模式 (Chat Mode)")
    print("=" * 60)

    test_questions = [
        "ETH 现在多少钱？",
        "What is the current price of BTC?",
    ]

    for question in test_questions:
        print(f"\n问题: {question}")
        print("回答:")
        async for chunk in crypto_agent.answer(question, mode="chat"):
            print(chunk, end="", flush=True)
        print("\n" + "-" * 40)


async def test_answer_think_mode():
    """测试思考模式"""
    print("\n" + "=" * 60)
    print("测试 3: 思考模式 (Think Mode)")
    print("=" * 60)

    test_questions = [
        "分析一下 BTC 当前的技术面",
        "Comprehensive analysis of ETH",
    ]

    for question in test_questions:
        print(f"\n问题: {question}")
        print("回答:")
        async for chunk in crypto_agent.answer(question, mode="think"):
            print(chunk, end="", flush=True)
        print("\n" + "-" * 40)


async def test_simple_chat():
    """测试简单对话"""
    print("\n" + "=" * 60)
    print("测试 4: 简单对话")
    print("=" * 60)

    test_questions = [
        "你好",
        "Hello",
        "谢谢",
        "Thanks",
    ]

    for question in test_questions:
        print(f"\n问题: {question}")
        print("回答:")
        async for chunk in crypto_agent.answer(question, mode="chat"):
            print(chunk, end="", flush=True)
        print("\n" + "-" * 40)


async def test_no_symbol():
    """测试无币种情况"""
    print("\n" + "=" * 60)
    print("测试 5: 无币种情况")
    print("=" * 60)

    test_questions = [
        "现在的市场怎么样？",
        "What's the market trend?",
    ]

    for question in test_questions:
        print(f"\n问题: {question}")
        print("回答:")
        async for chunk in crypto_agent.answer(question, mode="chat"):
            print(chunk, end="", flush=True)
        print("\n" + "-" * 40)


async def main():
    """主函数"""
    print("开始测试 Skill 系统...")
    print()

    try:
        await test_intent_analysis()
        await test_answer_chat_mode()
        await test_answer_think_mode()
        await test_simple_chat()
        await test_no_symbol()

        print("\n" + "=" * 60)
        print("所有测试完成！")
        print("=" * 60)

    except Exception as e:
        print(f"\n测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
