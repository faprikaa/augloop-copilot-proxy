# Linux Server Deployment Guide

Run AugLoop Copilot Proxy on a Linux server, with tokens from your Windows machine.

## Architecture

```
┌─────────────────────────┐         ┌─────────────────────────┐
│     Windows Machine     │  file   │     Linux Server        │
│                         │ upload  │                         │
│  Excel + Copilot        │         │  run_server.py          │
│       ↓                 │ ──────→ │       ↓                 │
│  run.py (token harvest) │  SCP /  │  import_token.py        │
│       ↓                 │  rsync  │       ↓                 │
│  export_token.py        │         │  server.py (proxy)      │
│  (token_bundle.json)    │         │  /v1/chat/completions   │
└─────────────────────────┘         └─────────────────────────┘
```

## Quick Start

### Step 1: Linux Server Setup

```bash
# 1. Clone/copy the project
git clone <repo> augloop-copilot-proxy
cd augloop-copilot-proxy

# 2. Run the start script (auto-creates venv, installs deps)
chmod +x start.sh
./start.sh

# Or manually with pip:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-linux.txt
python3 run_server.py --host 0.0.0.0 --port 8080

# Or with uv (faster):
uv venv
uv pip install -r requirements-linux.txt
uv run run_server.py --host 0.0.0.0 --port 8080
```

The server starts but won't work until tokens are uploaded from Windows.

### Step 2: Export Tokens on Windows

On your Windows machine (where Excel + Copilot is running):

```bash
python export_token.py
# Creates: token_bundle.json
```

### Step 3: Upload & Import on Linux

```bash
# Upload the file (from Windows or wherever you have the file)
scp token_bundle.json user@LINUX_SERVER:~/augloop-copilot-proxy/

# On the Linux server, import it
python3 import_token.py token_bundle.json
```

Or you can just directly copy `config.yaml` and `.augloop_token` from Windows to the Linux server — they work as-is.

### Step 4: Verify

```bash
# Check status
curl http://LINUX_SERVER_IP:8080/status

# Test chat
curl -X POST http://LINUX_SERVER_IP:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}'
```

## Token Refresh

JWE tokens expire after ~1 hour. When your token expires:

1. On Windows: `python export_token.py`
2. Upload `token_bundle.json` to Linux server
3. On Linux: `python3 import_token.py token_bundle.json`

The server picks up the new token immediately (no restart needed if using `/token/manual` or re-import).

### Alternative: Auto-Sync via HTTP (Optional)

If you prefer automatic sync instead of manual upload, the server also supports `POST /token/sync`:

```bash
# On Windows (one-shot push)
python sync_token.py --target http://LINUX_SERVER_IP:8080

# Or daemon mode (auto-pushes when tokens change)
python sync_token.py --target http://LINUX_SERVER_IP:8080 --daemon --interval 120
```

Secure the sync endpoint with a key:

```bash
# Linux server
python3 run_server.py --sync-key MY_SECRET_KEY

# Windows
python sync_token.py --target http://LINUX_SERVER:8080 --sync-key MY_SECRET_KEY
```

## Running as a systemd Service

Create `/etc/systemd/system/copilot-proxy.service`:

```ini
[Unit]
Description=AugLoop Copilot Proxy
After=network.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/path/to/augloop-copilot-proxy
ExecStart=/path/to/augloop-copilot-proxy/.venv/bin/python run_server.py --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable copilot-proxy
sudo systemctl start copilot-proxy
sudo journalctl -u copilot-proxy -f  # View logs
```

## Token Lifecycle

- **JWE Bearer Token**: ~1 hour validity, refreshed by Excel background scanning
- **JWT authToken**: ~24 hour validity, acquired via WebSocket Phase 1

## Files Overview

| File | Platform | Purpose |
|------|----------|---------|
| `export_token.py` | Windows | Export tokens to JSON file |
| `run.py` | Windows | Full launcher with Excel + token harvesting |
| `start.bat` | Windows | Windows quick start |
| `run_server.py` | Linux | Headless server launcher |
| `start.sh` | Linux | Quick start script with venv |
| `requirements-linux.txt` | Linux | Dependencies without Windows libs |
| `import_token.py` | Linux | Import tokens from JSON bundle |
| `sync_token.py` | Both | HTTP push tokens (optional alternative) |

## Troubleshooting

**"NO TOKEN FOUND"**: Upload `token_bundle.json` from Windows, or directly copy `config.yaml` / `.augloop_token`.

**Token expired (401)**: JWE token ~1h lifetime. Re-export from Windows and re-import.

**Connection refused**: Make sure `--host 0.0.0.0` (not `127.0.0.1`) and firewall allows port 8080.

**Import errors on Linux**: Use `requirements-linux.txt`, not `requirements.txt` (the latter includes Windows-only packages).
