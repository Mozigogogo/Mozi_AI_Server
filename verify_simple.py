#!/usr/bin/env python3
"""
简化验证脚本
检查项目结构和基本功能
"""

import os
import sys

def check_file_exists(file_path):
    """检查文件是否存在"""
    if os.path.isfile(file_path):
        print(f"[OK] 文件存在: {file_path}")
        return True
    else:
        print(f"[FAIL] 文件不存在: {file_path}")
        return False

def check_dir_exists(dir_path):
    """检查目录是否存在"""
    if os.path.isdir(dir_path):
        print(f"[OK] 目录存在: {dir_path}")
        return True
    else:
        print(f"[FAIL] 目录不存在: {dir_path}")
        return False

def main():
    """主函数"""
    print("="*60)
    print("加密货币分析助手实现验证")
    print("="*60)

    # 检查项目结构
    print("\n检查项目结构...")

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
        "README.md",
        "MIGRATION.md"
    ]

    # 检查目录
    dir_results = []
    for dir_path in required_dirs:
        success = check_dir_exists(dir_path)
        dir_results.append(success)

    # 检查文件
    file_results = []
    for file_path in required_files:
        success = check_file_exists(file_path)
        file_results.append(success)

    # 检查.env文件
    print("\n检查环境配置...")
    if os.path.isfile(".env"):
        print("[OK] .env文件存在")

        # 简单检查内容
        try:
            with open(".env", 'r', encoding='utf-8') as f:
                content = f.read()

            required_vars = ["DEEPSEEK_API_KEY", "MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD"]
            missing_vars = []

            for var in required_vars:
                if f"{var}=" in content:
                    print(f"[OK] 环境变量 {var} 已配置")
                else:
                    print(f"[WARN] 环境变量 {var} 未配置")
                    missing_vars.append(var)

            if missing_vars:
                print(f"[INFO] 需要配置的环境变量: {', '.join(missing_vars)}")

        except Exception as e:
            print(f"[FAIL] 读取.env文件失败: {e}")
    else:
        print("[FAIL] .env文件不存在")
        print("[INFO] 请从 config/.env.example 复制创建")

    # 统计结果
    total_checks = len(dir_results) + len(file_results)
    passed_checks = sum(dir_results) + sum(file_results)

    print("\n" + "="*60)
    print("验证结果汇总")
    print("="*60)

    print(f"目录检查: {sum(dir_results)}/{len(dir_results)} 通过")
    print(f"文件检查: {sum(file_results)}/{len(file_results)} 通过")
    print(f"总计: {passed_checks}/{total_checks} 通过 ({passed_checks/total_checks*100:.1f}%)")

    # 显示项目摘要
    print("\n" + "="*60)
    print("项目摘要")
    print("="*60)

    # 统计Python文件
    python_files = []
    for root, dirs, files in os.walk("."):
        for file in files:
            if file.endswith(".py"):
                python_files.append(os.path.join(root, file))

    print(f"Python文件数量: {len(python_files)}")

    # 显示主要文件
    print("\n主要文件:")
    main_files = [
        "app/main.py - FastAPI应用入口",
        "app/api/endpoints.py - API路由",
        "app/agents/crypto_agent.py - LangChain智能体",
        "app/services/data_service.py - 数据服务",
        "config/settings.py - 配置管理"
    ]

    for file in main_files:
        print(f"  * {file}")

    # 下一步建议
    print("\n" + "="*60)
    print("下一步建议")
    print("="*60)

    if passed_checks == total_checks:
        print("""
1. 安装依赖:
   pip install -r requirements.txt

2. 启动服务:
   python -m app.main

3. 测试API:
   python examples/api_examples.py

4. 访问文档:
   http://localhost:8000/docs
   http://localhost:8000/redoc
        """)
    else:
        print("""
1. 修复失败的检查项
2. 确保所有必需的文件都存在
3. 配置正确的环境变量
4. 然后重新运行验证
        """)

    return passed_checks == total_checks

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n未预期的错误: {e}")
        sys.exit(1)