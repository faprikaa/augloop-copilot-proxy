@echo off
chcp 65001 >nul 2>&1
title Excel Copilot Proxy Server (AugLoop Proxy v2)

echo ════════════════════════════════════════════════════
echo   Excel Copilot Proxy Server - Quick Start (v2)
echo ════════════════════════════════════════════════════
echo.

cd /d "%~dp0"

:: 1. Kill old processes using port 8080
echo [1/4] Cleaning port 8080...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    echo   Terminating old process PID=%%a
    taskkill /F /PID %%a >nul 2>&1
)
echo   OK
echo.

:: 2. Check Python
echo [2/4] Checking Python environment...
python --version >nul 2>&1
if errorlevel 1 (
    echo   [Error] Python not found, please install Python 3.10+
    echo   Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo   OK
echo.

:: 3. Check dependencies
echo [3/4] Checking dependencies...
python -c "import fastapi, uvicorn, httpx, aiohttp, yaml" >nul 2>&1
if errorlevel 1 (
    echo   Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo   [Error] Dependency installation failed, please run manually: pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo   OK
echo.

:: 4. Start
echo [4/4] Starting server...
echo.
echo   ╔═══════════════════════════════════════════════╗
echo   ║  1. Excel will open & initialize Copilot      ║
echo   ║  2. Excel hides to background after token get ║
echo   ║  3. Proxy server starts                       ║
echo   ║                                               ║
echo   ║  Server URL:    http://127.0.0.1:8080         ║
echo   ║  API Endpoint:  POST /v1/chat/completions     ║
echo   ║  Status Query:  GET  /status                  ║
echo   ║  Token Refresh: POST /token/auto              ║
echo   ║                                               ║
echo   ║  Press Ctrl+C to exit (closes Excel auto)     ║
echo   ╚═══════════════════════════════════════════════╝
echo.

python run.py --auto-init %*

echo.
echo [*] Exited, Excel has been closed automatically.
pause
