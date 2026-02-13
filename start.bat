@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

REM 加密货币分析助手启动脚本（Windows）

echo.
echo ==========================================
echo   加密货币分析助手 - 启动脚本
echo ==========================================
echo.

REM 检查Python版本
echo [INFO] 检查Python版本...
python --version > nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python未安装或不在PATH中
    pause
    exit /b 1
)

for /f "tokens=2" %%i in ('python --version 2^>^&1') do set python_version=%%i
echo [SUCCESS] Python版本: !python_version!

REM 检查虚拟环境
echo.
echo [INFO] 检查虚拟环境...
if exist venv\Scripts\activate.bat (
    echo [SUCCESS] 虚拟环境已存在
    call venv\Scripts\activate.bat
    echo [SUCCESS] 虚拟环境已激活
) else (
    echo [INFO] 创建虚拟环境...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] 虚拟环境创建失败
        pause
        exit /b 1
    )
    call venv\Scripts\activate.bat
    echo [SUCCESS] 虚拟环境创建并激活
)

REM 安装依赖
echo.
echo [INFO] 安装Python依赖...
pip install --upgrade pip
if %errorlevel% neq 0 (
    echo [ERROR] pip升级失败
    pause
    exit /b 1
)

if exist requirements.txt (
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] 依赖安装失败
        pause
        exit /b 1
    )
    echo [SUCCESS] 依赖安装完成
) else (
    echo [ERROR] requirements.txt文件不存在
    pause
    exit /b 1
)

REM 检查环境变量
echo.
echo [INFO] 检查环境变量...
if exist .env (
    echo [SUCCESS] .env文件存在

    REM 检查必要的环境变量
    set required_vars=DEEPSEEK_API_KEY MYSQL_HOST MYSQL_USER MYSQL_PASSWORD
    set all_good=1

    for %%v in (%required_vars%) do (
        findstr /b "%%v=" .env > nul
        if !errorlevel! equ 0 (
            for /f "tokens=2 delims==" %%i in ('findstr /b "%%v=" .env') do set value=%%i
            if "!value!" neq "" (
                echo !value! | findstr /r "^your_.*" > nul
                if !errorlevel! equ 0 (
                    echo [WARNING] %%v 需要配置实际值
                ) else (
                    echo [SUCCESS] %%v 已配置
                )
            ) else (
                echo [WARNING] %%v 值为空
            )
        ) else (
            echo [ERROR] %%v 未在.env中配置
            set all_good=0
        )
    )

    if !all_good! equ 0 (
        echo [ERROR] 环境变量配置不完整
        echo 请编辑.env文件并配置必要的环境变量
        pause
        exit /b 1
    )
) else (
    echo [ERROR] .env文件不存在
    if exist config\.env.example (
        copy config\.env.example .env
        echo [INFO] 已从config\.env.example复制创建.env文件
        echo 请编辑.env文件并配置必要的环境变量
    )
    pause
    exit /b 1
)

REM 运行测试（可选）
echo.
set /p run_test="是否运行测试？(y/n, 默认n): "
if /i "!run_test!" equ "y" (
    echo [INFO] 运行集成测试...
    if exist test_integration.py (
        python test_integration.py
        if !errorlevel! neq 0 (
            echo [WARNING] 测试失败，但继续启动服务
        ) else (
            echo [SUCCESS] 测试通过
        )
    ) else (
        echo [WARNING] 测试文件不存在，跳过测试
    )
)

REM 启动服务
echo.
echo ==========================================
echo   加密货币分析助手服务启动中...
echo ==========================================
echo.

python -m app.main

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] 服务启动失败
    pause
)

endlocal