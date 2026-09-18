#!/bin/bash
# start.sh - Linux Server Quick Start
# Usage: ./start.sh [--port 8080] [--sync-key SECRET]

set -e

echo "════════════════════════════════════════════════════"
echo "  AugLoop Copilot Proxy - Linux Server"
echo "════════════════════════════════════════════════════"
echo

cd "$(dirname "$0")"

# 1. Check Python
echo "[1/3] Checking Python..."
if ! command -v python3 &>/dev/null; then
    echo "  [Error] Python3 not found. Install with:"
    echo "    sudo apt install python3 python3-pip python3-venv"
    exit 1
fi
PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python $PYTHON_VERSION OK"
echo

# 2. Setup venv & install deps
echo "[2/3] Checking dependencies..."
if [ ! -d ".venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate

pip install -q -r requirements-linux.txt 2>/dev/null
echo "  OK"
echo

# 3. Start server
echo "[3/3] Starting server..."
echo
echo "  ╔═══════════════════════════════════════════════╗"
echo "  ║  Server:    http://0.0.0.0:8080               ║"
echo "  ║  API:       POST /v1/chat/completions          ║"
echo "  ║  Status:    GET  /status                       ║"
echo "  ║  Token Sync: POST /token/sync                  ║"
echo "  ║                                                ║"
echo "  ║  Push tokens from Windows:                     ║"
echo "  ║  python sync_token.py --target http://IP:8080  ║"
echo "  ║                                                ║"
echo "  ║  Press Ctrl+C to exit                          ║"
echo "  ╚═══════════════════════════════════════════════╝"
echo

python3 run_server.py "$@"
