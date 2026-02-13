#!/usr/bin/env python3
"""
API使用示例
展示如何使用加密货币分析助手API
"""

import json
import requests
import time
from typing import Dict, Any

BASE_URL = "http://localhost:8000/api/v1"


def print_section(title: str):
    """打印章节标题"""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def test_health_check():
    """测试健康检查"""
    print_section("健康检查")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 服务状态: {data['status']}")
            print(f"📦 版本: {data['version']}")
            print(f"⏰ 时间戳: {data['timestamp']}")
            return True
        else:
            print(f"❌ 健康检查失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_get_tools():
    """测试获取工具列表"""
    print_section("获取工具列表")
    try:
        response = requests.get(f"{BASE_URL}/tools", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"🛠️  可用工具数量: {data['count']}")
            print("\n部分工具:")
            for i, tool in enumerate(data['tools'][:5]):
                print(f"  {i+1}. {tool['name']}")
                print(f"     描述: {tool['description'][:60]}...")
            if data['count'] > 5:
                print(f"  ... 还有 {data['count']-5} 个工具")
            return True
        else:
            print(f"❌ 获取工具失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_analyze_btc():
    """测试分析BTC"""
    print_section("分析BTC（非流式）")

    payload = {
        "symbol": "BTC",
        "question": "请分析当前市场状况和技术面",
        "lang": "zh"
    }

    try:
        start_time = time.time()
        response = requests.post(
            f"{BASE_URL}/analyze",
            json=payload,
            timeout=30
        )
        elapsed = time.time() - start_time

        if response.status_code == 200:
            data = response.json()
            print(f"✅ 分析完成 (耗时: {elapsed:.2f}秒)")
            print(f"📊 币种: {data['symbol']}")
            print(f"❓ 问题: {data['question']}")
            print(f"📝 响应长度: {len(data['response'])} 字符")
            print(f"🔄 中间步骤: {len(data['intermediate_steps'])} 步")

            # 显示响应前200字符
            print(f"\n响应预览:")
            print(data['response'][:200] + "...")

            return True
        else:
            print(f"❌ 分析失败: {response.status_code}")
            print(f"错误信息: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_analyze_stream():
    """测试流式分析"""
    print_section("流式分析ETH")

    payload = {
        "symbol": "ETH",
        "question": "简要分析一下当前状况",
        "lang": "zh"
    }

    try:
        print("🔄 开始流式分析...")
        print("  收到数据:")

        response = requests.post(
            f"{BASE_URL}/analyze/stream",
            json=payload,
            stream=True,
            headers={"Accept": "text/event-stream"},
            timeout=60
        )

        if response.status_code == 200:
            chunk_count = 0
            total_chars = 0

            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith("data: "):
                        data_str = line[6:]  # 移除"data: "前缀
                        try:
                            data = json.loads(data_str)
                            if data['type'] == 'chunk':
                                chunk_count += 1
                                total_chars += len(data['data'])
                                if chunk_count <= 3:  # 只显示前3个块
                                    print(f"    块{chunk_count}: {data['data'][:50]}...")
                            elif data['type'] == 'complete':
                                print(f"✅ 流式分析完成")
                                print(f"📊 收到 {chunk_count} 个数据块")
                                print(f"📝 总字符数: {total_chars}")
                                return True
                            elif data['type'] == 'error':
                                print(f"❌ 分析错误: {data['data']}")
                                return False
                        except json.JSONDecodeError:
                            continue
        else:
            print(f"❌ 流式分析失败: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_chat():
    """测试对话"""
    print_section("对话测试")

    payload = {
        "message": "BTC最近表现如何？",
        "conversation_id": "test_conversation_001",
        "lang": "zh"
    }

    try:
        response = requests.post(
            f"{BASE_URL}/chat",
            json=payload,
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            print(f"✅ 对话完成")
            print(f"💬 用户消息: {data['message']}")
            print(f"🤖 AI响应长度: {len(data['response'])} 字符")
            print(f"🆔 会话ID: {data['conversation_id']}")

            # 显示响应前150字符
            print(f"\n响应预览:")
            print(data['response'][:150] + "...")

            return True
        else:
            print(f"❌ 对话失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_chat_stream():
    """测试流式对话"""
    print_section("流式对话测试")

    payload = {
        "message": "请介绍一下加密货币市场",
        "conversation_id": "test_conversation_002",
        "lang": "zh"
    }

    try:
        print("🔄 开始流式对话...")
        print("  收到数据:")

        response = requests.post(
            f"{BASE_URL}/chat/stream",
            json=payload,
            stream=True,
            headers={"Accept": "text/event-stream"},
            timeout=60
        )

        if response.status_code == 200:
            chunk_count = 0
            total_chars = 0

            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith("data: "):
                        data_str = line[6:]  # 移除"data: "前缀
                        try:
                            data = json.loads(data_str)
                            if data['type'] == 'chunk':
                                chunk_count += 1
                                total_chars += len(data['data'])
                                if chunk_count <= 3:  # 只显示前3个块
                                    print(f"    块{chunk_count}: {data['data'][:50]}...")
                            elif data['type'] == 'complete':
                                print(f"✅ 流式对话完成")
                                print(f"📊 收到 {chunk_count} 个数据块")
                                print(f"📝 总字符数: {total_chars}")
                                return True
                            elif data['type'] == 'error':
                                print(f"❌ 对话错误: {data['data']}")
                                return False
                        except json.JSONDecodeError:
                            continue
        else:
            print(f"❌ 流式对话失败: {response.status_code}")
            return False

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_clear_memory():
    """测试清除记忆"""
    print_section("清除对话记忆")
    try:
        response = requests.post(f"{BASE_URL}/clear", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ {data['message']}")
            return True
        else:
            print(f"❌ 清除记忆失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def test_get_symbols():
    """测试获取支持的币种"""
    print_section("获取支持的币种")
    try:
        response = requests.get(f"{BASE_URL}/symbols", timeout=5)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 支持币种数量: {data['count']}")
            print(f"📋 币种列表: {', '.join(data['symbols'][:10])}")
            if data['count'] > 10:
                print(f"   ... 还有 {data['count']-10} 个币种")
            return True
        else:
            print(f"❌ 获取币种失败: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False


def run_all_examples():
    """运行所有示例"""
    print("=" * 60)
    print("       加密货币分析助手 API 使用示例")
    print("=" * 60)

    # 首先检查服务是否可用
    if not test_health_check():
        print("\n❌ 服务不可用，请先启动服务")
        print("   启动命令: python -m app.main")
        return False

    # 运行各个测试
    tests = [
        ("健康检查", lambda: True),  # 已经运行过
        ("获取工具列表", test_get_tools),
        ("获取支持的币种", test_get_symbols),
        ("分析BTC", test_analyze_btc),
        ("流式分析", test_analyze_stream),
        ("对话测试", test_chat),
        ("流式对话", test_chat_stream),
        ("清除记忆", test_clear_memory),
    ]

    results = []
    for test_name, test_func in tests[1:]:  # 跳过已经运行的健康检查
        print(f"\n▶ 正在测试: {test_name}")
        try:
            success = test_func()
            results.append((test_name, success))
        except Exception as e:
            print(f"❌ 测试异常: {e}")
            results.append((test_name, False))

    # 汇总结果
    print_section("测试结果汇总")

    passed = 0
    total = len(results)

    for test_name, success in results:
        if success:
            passed += 1
            status = "✅ 通过"
        else:
            status = "❌ 失败"
        print(f"  {test_name:20} {status}")

    print(f"\n🎯 通过率: {passed}/{total}")

    if passed == total:
        print("\n✨ 所有测试通过！API功能正常。")
        return True
    else:
        print("\n⚠ 部分测试失败，请检查服务状态和配置。")
        return False


def show_api_reference():
    """显示API参考"""
    print_section("API参考")

    print("""
主要端点:

1. 健康检查
   GET    /api/v1/health

2. 工具管理
   GET    /api/v1/tools           # 获取可用工具
   GET    /api/v1/symbols         # 获取支持的币种

3. 分析功能
   POST   /api/v1/analyze         # 非流式分析
   POST   /api/v1/analyze/stream  # 流式分析 (SSE)

4. 对话功能
   POST   /api/v1/chat            # 非流式对话
   POST   /api/v1/chat/stream     # 流式对话 (SSE)

5. 系统管理
   POST   /api/v1/clear           # 清除对话记忆

请求示例 (分析):
```json
{
  "symbol": "BTC",
  "question": "请分析当前市场状况",
  "lang": "zh"
}
```

请求示例 (对话):
```json
{
  "message": "BTC最近表现如何？",
  "conversation_id": "unique_id_123",
  "lang": "zh"
}
```

流式响应:
- 使用 Server-Sent Events (SSE)
- 每个数据块格式: {"data": "内容", "type": "chunk"}
- 完成信号: {"data": "", "type": "complete"}
- 错误信号: {"data": "错误信息", "type": "error"}
    """)


if __name__ == "__main__":
    try:
        # 运行示例
        success = run_all_examples()

        # 显示API参考
        show_api_reference()

        # 退出码
        exit(0 if success else 1)

    except KeyboardInterrupt:
        print("\n\n👋 用户中断")
        exit(0)
    except Exception as e:
        print(f"\n❌ 未预期的错误: {e}")
        exit(1)