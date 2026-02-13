#!/usr/bin/env python3
"""诊断智能体问题"""

import sys
import os
import traceback

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== 加密货币分析智能体诊断 ===")

try:
    # 1. 测试导入
    print("1. 测试导入...")
    from app.agents.crypto_agent import crypto_agent
    print("   [OK] 导入成功")

    # 2. 测试工具创建
    print("2. 测试工具创建...")
    tools = crypto_agent.tools
    print(f"   工具数量: {len(tools)}")

    if len(tools) > 0:
        print(f"   第一个工具: {tools[0].name}")
        print(f"   工具描述: {tools[0].description}")
        print("   [OK] 工具创建成功")
    else:
        print("   [FAIL] 工具创建失败 - 无工具")

    # 3. 测试LLM创建
    print("3. 测试LLM创建...")
    llm = crypto_agent.llm
    print(f"   LLM模型: {llm.model_name}")
    print(f"   LLM类型: {type(llm)}")
    print("   [OK] LLM创建成功")

    # 4. 测试智能体创建
    print("4. 测试智能体创建...")
    agent = crypto_agent.agent
    print(f"   智能体类型: {type(agent)}")
    print("   [OK] 智能体创建成功")

    # 5. 测试单个工具调用
    print("5. 测试单个工具调用...")
    try:
        # 测试市场数据工具
        from app.agents.tools.market_data import MarketDataTool
        tool_instance = MarketDataTool()
        print(f"   工具实例类型: {type(tool_instance)}")
        print(f"   工具名称: {tool_instance.name}")

        # 测试execute方法
        result = tool_instance.execute("BTC")
        print(f"   工具执行结果: {result.get('summary', '无summary')}")
        print("   [OK] 单个工具执行成功")
    except Exception as e:
        print(f"   [FAIL] 单个工具执行失败: {e}")
        traceback.print_exc()

    # 6. 测试智能体调用（简单测试）
    print("6. 测试智能体简单调用...")
    try:
        # 创建一个简单的消息测试
        from langchain_core.messages import HumanMessage

        messages = [HumanMessage(content="请简单介绍BTC")]
        print("   创建消息成功")

        # 尝试调用智能体，设置超时
        import asyncio
        import functools

        # 使用线程执行同步代码
        import threading
        import time

        def test_invoke():
            try:
                result = crypto_agent.agent.invoke({"messages": messages})
                print(f"   智能体调用结果: {result}")
                print("   [OK] 智能体调用成功")
                return True
            except Exception as e:
                print(f"   [FAIL] 智能体调用失败: {e}")
                return False

        # 在子线程中运行，设置超时
        thread = threading.Thread(target=test_invoke)
        thread.daemon = True
        thread.start()

        # 等待5秒
        thread.join(timeout=5)

        if thread.is_alive():
            print("   [FAIL] 智能体调用超时（5秒）")
            print("   可能原因：")
            print("   - LangChain 1.x API配置问题")
            print("   - 工具调用链卡住")
            print("   - DeepSeek API响应慢")
        else:
            print("   智能体调用完成")

    except Exception as e:
        print(f"   [FAIL] 测试过程中出错: {e}")
        traceback.print_exc()

    # 7. 检查外部依赖
    print("7. 检查外部依赖配置...")
    from app.core.config import get_settings
    settings = get_settings()

    print(f"   DeepSeek API密钥: {settings.deepseek_api_key[:10]}...")
    print(f"   K线API: {settings.kline_api_base}")
    print(f"   MySQL主机: {settings.mysql_host}")
    print(f"   调试模式: {settings.debug}")

    print("\n=== 诊断总结 ===")
    print("基础组件初始化正常，但智能体调用可能超时。")
    print("建议：")
    print("1. 检查LangChain 1.x的create_agent配置")
    print("2. 确认工具参数传递正确")
    print("3. 检查DeepSeek API响应")
    print("4. 增加超时时间或添加错误处理")

except ImportError as e:
    print(f"[FAIL] 导入失败: {e}")
    traceback.print_exc()
except Exception as e:
    print(f"[FAIL] 诊断过程中出错: {e}")
    traceback.print_exc()