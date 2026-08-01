@echo off
chcp 65001 >nul 2>&1
title Excel Copilot 反代服务器 (AugLoop Proxy v2)

echo ════════════════════════════════════════════════════
echo   Excel Copilot 反代服务器 - 一键启动 (v2)
echo ════════════════════════════════════════════════════
echo.

cd /d "%~dp0"

:: 1. 关闭占用 8080 端口的旧进程
echo [1/4] 清理 8080 端口...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    echo   终止旧进程 PID=%%a
    taskkill /F /PID %%a >nul 2>&1
)
echo   OK
echo.

:: 2. 检查 Python
echo [2/4] 检查 Python 环境...
python --version >nul 2>&1
if errorlevel 1 (
    echo   [错误] 未找到 Python, 请先安装 Python 3.10+
    echo   下载: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo   OK
echo.

:: 3. 检查依赖
echo [3/4] 检查依赖...
python -c "import fastapi, uvicorn, httpx, aiohttp, yaml" >nul 2>&1
if errorlevel 1 (
    echo   安装依赖...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo   [错误] 依赖安装失败, 请手动运行: pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo   OK
echo.

:: 4. 启动
echo [4/4] 启动服务器...
echo.
echo   ╔═══════════════════════════════════════════════╗
echo   ║  1. Excel 会自动打开并初始化 Copilot          ║
echo   ║  2. 获取 Token 后 Excel 隐藏到后台           ║
echo   ║  3. 反代服务器启动                           ║
echo   ║                                               ║
echo   ║  服务器地址: http://127.0.0.1:8080            ║
echo   ║  API 端点:   POST /v1/chat/completions         ║
echo   ║  状态查询:   GET  /status                     ║
echo   ║  Token刷新:  POST /token/auto                 ║
echo   ║                                               ║
echo   ║  Ctrl+C 退出 (会自动关闭 Excel)               ║
echo   ╚═══════════════════════════════════════════════╝
echo.

python run.py --auto-init %*

echo.
echo [*] 已退出, Excel 已自动关闭。
pause
