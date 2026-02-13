#!/bin/bash

# 加密货币分析助手启动脚本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 函数：打印带颜色的消息
print_message() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查Python版本
check_python_version() {
    print_message "检查Python版本..."
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    required_version="3.9"

    if [ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" = "$required_version" ]; then
        print_success "Python版本满足要求: $python_version"
    else
        print_error "Python版本过低: $python_version，需要 >= $required_version"
        exit 1
    fi
}

# 检查依赖
check_dependencies() {
    print_message "检查系统依赖..."

    # 检查pip
    if command -v pip3 &> /dev/null; then
        print_success "pip3 已安装"
    else
        print_error "pip3 未安装"
        exit 1
    fi

    # 检查virtualenv（可选）
    if command -v virtualenv &> /dev/null; then
        print_success "virtualenv 已安装"
    else
        print_warning "virtualenv 未安装，将使用系统Python环境"
    fi
}

# 设置虚拟环境
setup_venv() {
    print_message "设置Python虚拟环境..."

    if [ ! -d "venv" ]; then
        python3 -m venv venv
        print_success "虚拟环境创建成功"
    else
        print_success "虚拟环境已存在"
    fi

    # 激活虚拟环境
    if [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
        print_success "虚拟环境已激活"
    else
        print_error "虚拟环境激活失败"
        exit 1
    fi
}

# 安装依赖
install_dependencies() {
    print_message "安装Python依赖..."

    if [ -f "requirements.txt" ]; then
        pip install --upgrade pip
        pip install -r requirements.txt
        print_success "依赖安装完成"
    else
        print_error "requirements.txt 文件不存在"
        exit 1
    fi
}

# 检查环境变量
check_env() {
    print_message "检查环境变量..."

    if [ -f ".env" ]; then
        print_success ".env 文件存在"

        # 检查必要的环境变量
        required_vars=("DEEPSEEK_API_KEY" "MYSQL_HOST" "MYSQL_USER" "MYSQL_PASSWORD")

        for var in "${required_vars[@]}"; do
            if grep -q "^$var=" .env; then
                value=$(grep "^$var=" .env | cut -d'=' -f2-)
                if [ -n "$value" ] && [ "$value" != "your_"* ]; then
                    print_success "$var 已配置"
                else
                    print_warning "$var 需要配置实际值"
                fi
            else
                print_error "$var 未在 .env 中配置"
                exit 1
            fi
        done
    else
        print_error ".env 文件不存在，请从 .env.example 复制并配置"
        cp config/.env.example .env
        print_warning "已创建 .env 文件，请编辑并配置必要的环境变量"
        exit 1
    fi
}

# 运行测试
run_tests() {
    print_message "运行集成测试..."

    if [ -f "test_integration.py" ]; then
        python test_integration.py
        if [ $? -eq 0 ]; then
            print_success "测试通过"
        else
            print_warning "测试失败，但继续启动服务"
        fi
    else
        print_warning "测试文件不存在，跳过测试"
    fi
}

# 启动服务
start_service() {
    print_message "启动加密货币分析助手服务..."

    # 显示启动信息
    echo ""
    echo "=========================================="
    echo "  加密货币分析助手服务启动中..."
    echo "=========================================="
    echo ""

    # 启动服务
    python -m app.main
}

# 主函数
main() {
    echo ""
    echo "=========================================="
    echo "  加密货币分析助手 - 启动脚本"
    echo "=========================================="
    echo ""

    # 执行各个步骤
    check_python_version
    check_dependencies

    # 询问是否使用虚拟环境
    read -p "是否使用虚拟环境？(y/n, 默认y): " use_venv
    use_venv=${use_venv:-y}

    if [[ $use_venv =~ ^[Yy]$ ]]; then
        setup_venv
    else
        print_warning "使用系统Python环境"
    fi

    install_dependencies
    check_env
    run_tests
    start_service
}

# 处理命令行参数
case "$1" in
    "test")
        check_python_version
        check_dependencies
        check_env
        run_tests
        ;;
    "install")
        check_python_version
        check_dependencies
        setup_venv
        install_dependencies
        check_env
        ;;
    "docker")
        print_message "使用Docker启动..."
        if command -v docker-compose &> /dev/null; then
            docker-compose up -d
            print_success "Docker服务已启动"
            echo "服务地址: http://localhost:8000"
        else
            print_error "docker-compose 未安装"
            exit 1
        fi
        ;;
    "help"|"-h"|"--help")
        echo "用法: $0 [命令]"
        echo ""
        echo "命令:"
        echo "  (无)     启动服务"
        echo "  test     运行测试"
        echo "  install  安装依赖"
        echo "  docker   使用Docker启动"
        echo "  help     显示帮助"
        ;;
    *)
        main
        ;;
esac