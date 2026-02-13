#!/usr/bin/env python3
"""
验证实现脚本
检查项目结构和基本功能
"""

import os
import sys
import importlib

def print_check(name, status, message=""):
    """打印检查结果"""
    if status:
        print(f"[OK] {name}: 通过 {message}")
        return True
    else:
        print(f"[FAIL] {name}: 失败 {message}")
        return False

def check_project_structure():
    """检查项目结构"""
    print("🔍 检查项目结构...")

    required_dirs = [
        "app",
        "app/api",
        "app/core",
        "app/agents",
        "app/agents/tools",
        "app/services",
        "app/utils",
        "config",
        "examples"
    ]

    required_files = [
        "app/main.py",
        "app/api/endpoints.py",
        "app/api/schemas.py",
        "app/core/config.py",
        "app/core/exceptions.py",
        "app/agents/crypto_agent.py",
        "app/services/data_service.py",
        "app/services/llm_service.py",
        "app/utils/formatters.py",
        "app/utils/validators.py",
        "config/settings.py",
        "requirements.txt",
        ".env.example",
        "README.md"
    ]

    all_passed = True

    # 检查目录
    for dir_path in required_dirs:
        if os.path.isdir(dir_path):
            print_check(f"目录 {dir_path}", True)
        else:
            print_check(f"目录 {dir_path}", False, f"不存在")
            all_passed = False

    # 检查文件
    for file_path in required_files:
        if os.path.isfile(file_path):
            print_check(f"文件 {file_path}", True)
        else:
            print_check(f"文件 {file_path}", False, f"不存在")
            all_passed = False

    return all_passed

def check_python_imports():
    """检查Python导入"""
    print("\n🐍 检查Python导入...")

    imports_to_check = [
        ("fastapi", "FastAPI"),
        ("pydantic", "BaseModel"),
        ("langchain.agents", "AgentExecutor"),
        ("langchain.tools", "BaseTool"),
        ("openai", "OpenAI"),
        ("requests", None),
        ("pymysql", None)
    ]

    all_passed = True

    for module_name, attribute_name in imports_to_check:
        try:
            module = importlib.import_module(module_name)
            if attribute_name:
                getattr(module, attribute_name)
            print_check(f"导入 {module_name}", True)
        except ImportError as e:
            print_check(f"导入 {module_name}", False, f"未安装: {e}")
            all_passed = False
        except AttributeError as e:
            print_check(f"导入 {module_name}.{attribute_name}", False, f"属性不存在: {e}")
            all_passed = False

    return all_passed

def check_code_files():
    """检查代码文件内容"""
    print("\n📄 检查代码文件内容...")

    files_to_check = [
        ("app/main.py", ["FastAPI", "APIRouter", "lifespan"]),
        ("app/api/schemas.py", ["BaseModel", "Field", "AnalyzeRequest"]),
        ("app/core/config.py", ["BaseSettings", "get_settings"]),
        ("app/agents/crypto_agent.py", ["CryptoAnalystAgent", "AgentExecutor"]),
        ("app/services/data_service.py", ["get_kline_data", "get_header_data"]),
        ("requirements.txt", ["fastapi", "langchain", "openai"]),
    ]

    all_passed = True

    for file_path, keywords in files_to_check:
        if not os.path.isfile(file_path):
            print_check(f"文件 {file_path}", False, "不存在")
            all_passed = False
            continue

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            missing_keywords = []
            for keyword in keywords:
                if keyword not in content:
                    missing_keywords.append(keyword)

            if missing_keywords:
                print_check(f"文件 {file_path}", False, f"缺少关键字: {missing_keywords}")
                all_passed = False
            else:
                print_check(f"文件 {file_path}", True)
        except Exception as e:
            print_check(f"文件 {file_path}", False, f"读取失败: {e}")
            all_passed = False

    return all_passed

def check_environment():
    """检查环境配置"""
    print("\n⚙️ 检查环境配置...")

    all_passed = True

    # 检查.env文件
    if os.path.isfile(".env"):
        print_check(".env文件", True)

        # 检查必要的环境变量
        required_env_vars = [
            "DEEPSEEK_API_KEY",
            "MYSQL_HOST",
            "MYSQL_USER",
            "MYSQL_PASSWORD"
        ]

        try:
            with open(".env", 'r', encoding='utf-8') as f:
                env_content = f.read()

            for var in required_env_vars:
                if f"{var}=" in env_content:
                    print_check(f"环境变量 {var}", True)
                else:
                    print_check(f"环境变量 {var}", False, "未配置")
                    all_passed = False
        except Exception as e:
            print_check(".env文件", False, f"读取失败: {e}")
            all_passed = False
    else:
        print_check(".env文件", False, "不存在")
        all_passed = False

    # 检查.env.example
    if os.path.isfile("config/.env.example"):
        print_check(".env.example模板", True)
    else:
        print_check(".env.example模板", False, "不存在")
        all_passed = False

    return all_passed

def generate_summary():
    """生成项目摘要"""
    print("\n" + "="*60)
    print("📋 项目摘要")
    print("="*60)

    # 统计文件
    python_files = []
    for root, dirs, files in os.walk("."):
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))

    # 统计代码行数
    total_lines = 0
    for file_path in python_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                total_lines += len(lines)
        except:
            pass

    print(f"Python文件数量: {len(python_files)}")
    print(f"总代码行数: {total_lines}")

    # 显示主要模块
    print("\n主要模块:")
    modules = [
        "app.main - FastAPI应用入口",
        "app.api - API路由和数据模型",
        "app.core - 核心配置和异常处理",
        "app.agents - LangChain智能体和工具",
        "app.services - 数据服务和LLM服务",
        "app.utils - 工具函数"
    ]

    for module in modules:
        print(f"  • {module}")

    # 显示工具数量
    tools_file = "app/agents/crypto_agent.py"
    if os.path.isfile(tools_file):
        try:
            with open(tools_file, 'r', encoding='utf-8') as f:
                content = f.read()
                tool_count = content.count('Tool.from_function')
                print(f"\nLangChain工具数量: {tool_count}")
        except:
            pass

def main():
    """主函数"""
    print("="*60)
    print("加密货币分析助手实现验证")
    print("="*60)

    # 运行检查
    checks = [
        ("项目结构", check_project_structure),
        ("Python导入", check_python_imports),
        ("代码文件", check_code_files),
        ("环境配置", check_environment),
    ]

    results = []
    for check_name, check_func in checks:
        print(f"\n检查: {check_name}")
        try:
            success = check_func()
            results.append((check_name, success))
        except Exception as e:
            print(f"[FAIL] 检查异常: {e}")
            results.append((check_name, False))

    # 汇总结果
    print("\n" + "="*60)
    print("验证结果汇总")
    print("="*60)

    passed = 0
    total = len(results)

    for check_name, success in results:
        if success:
            passed += 1
            status = "[OK] 通过"
        else:
            status = "[FAIL] 失败"
        print(f"  {check_name:15} {status}")

    print(f"\n🎯 通过率: {passed}/{total} ({passed/total*100:.1f}%)")

    # 生成摘要
    generate_summary()

    # 下一步建议
    print("\n" + "="*60)
    print("🚀 下一步建议")
    print("="*60)

    if passed == total:
        print("""
1. 安装依赖:
   $ pip install -r requirements.txt

2. 启动服务:
   $ python -m app.main
   或
   $ ./start.sh  # Linux/Mac
   $ start.bat   # Windows

3. 测试API:
   $ python examples/api_examples.py

4. 访问文档:
   - http://localhost:8000/docs
   - http://localhost:8000/redoc
        """)
    else:
        print("""
1. 修复失败的检查项
2. 确保所有必需的文件都存在
3. 配置正确的环境变量
4. 然后重新运行验证
        """)

    return passed == total

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n👋 用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 未预期的错误: {e}")
        sys.exit(1)