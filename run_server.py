#!/usr/bin/env python3
"""
run_server.py - Linux/Headless Server Launcher (no Windows dependencies)

Starts only the proxy server. Tokens must be provided via:
  1. config.yaml (bearer_token / auth_token fields)
  2. .augloop_token file
  3. POST /token/sync endpoint (pushed from Windows machine)

Usage:
  python run_server.py                         # Default: 0.0.0.0:8080
  python run_server.py --port 8080             # Custom port
  python run_server.py --host 0.0.0.0          # Listen on all interfaces
  python run_server.py --sync-key SECRET       # Set API key for token sync endpoint
"""

import argparse
import logging
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run_server")


def main():
    parser = argparse.ArgumentParser(description="AugLoop Copilot Proxy - Linux/Headless Server")
    parser.add_argument("--port", type=int, default=8080, help="Server port (default: 8080)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--sync-key", default="", help="API key for /token/sync endpoint (optional)")
    args = parser.parse_args()

    # Set sync key as env var for server.py to pick up
    if args.sync_key:
        os.environ["TOKEN_SYNC_KEY"] = args.sync_key

    print()
    print("=" * 60)
    print("  AugLoop Copilot Proxy - Linux/Headless Server")
    print("=" * 60)
    print()
    print(f"  Server URL:       http://{args.host}:{args.port}")
    print(f"  API Endpoint:     http://{args.host}:{args.port}/v1/chat/completions")
    print(f"  Token Sync:       POST http://{args.host}:{args.port}/token/sync")
    print(f"  Status:           GET  http://{args.host}:{args.port}/status")
    print()

    # Check if token exists
    config_path = SCRIPT_DIR / "config.yaml"
    token_file = SCRIPT_DIR / ".augloop_token"
    has_token = False

    if token_file.exists():
        tok = token_file.read_text(encoding="utf-8").strip()
        if tok and len(tok) > 20:
            has_token = True
            logger.info("Token found in .augloop_token (%d chars)", len(tok))

    if config_path.exists():
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        tok = cfg.get("augloop", {}).get("bearer_token", "")
        if tok and len(tok) > 20:
            has_token = True
            logger.info("Token found in config.yaml (%d chars)", len(tok))

    if not has_token:
        logger.warning("=" * 60)
        logger.warning("  NO TOKEN FOUND!")
        logger.warning("  The server will start but cannot process requests.")
        logger.warning("  Push tokens from Windows via:")
        logger.warning("    python sync_token.py --target http://<this-server>:%d", args.port)
        logger.warning("  Or manually copy config.yaml / .augloop_token from Windows.")
        logger.warning("=" * 60)

    try:
        import uvicorn
        from server import app

        logger.info("Starting proxy server...")
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except ImportError as e:
        logger.error("Missing dependency: %s", e)
        logger.error("Run: pip install -r requirements-linux.txt")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Interrupted.")
        sys.exit(0)
