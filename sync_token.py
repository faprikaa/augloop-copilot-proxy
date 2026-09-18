#!/usr/bin/env python3
"""
sync_token.py - Push tokens from Windows to Linux server

Reads tokens from the local config.yaml / .augloop_token and pushes
them to a remote AugLoop Copilot Proxy server via POST /token/sync.

Can run as:
  1. One-shot: push current tokens once
  2. Daemon: watch for token changes and auto-push

Usage:
  python sync_token.py --target http://linux-server:8080
  python sync_token.py --target http://linux-server:8080 --sync-key SECRET
  python sync_token.py --target http://linux-server:8080 --daemon --interval 120
  python sync_token.py --target http://linux-server:8080 --export token_bundle.json
"""

import argparse
import hashlib
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [sync] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
TOKEN_FILE = SCRIPT_DIR / ".augloop_token"


def load_tokens() -> dict:
    """Load all tokens/metadata from local sources."""
    import yaml

    tokens = {
        "bearer_token": "",
        "auth_token": "",
        "x_client_metadata": "",
        "x_office_session_id": "",
    }

    # 1. From .augloop_token file
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok and len(tok) > 20:
            tokens["bearer_token"] = tok

    # 2. From config.yaml (may override/supplement)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        aug = cfg.get("augloop", {})

        # bearer_token: prefer .augloop_token, fallback to config
        if not tokens["bearer_token"]:
            tokens["bearer_token"] = aug.get("bearer_token", "")

        tokens["auth_token"] = aug.get("auth_token", "")
        tokens["x_client_metadata"] = aug.get("x_client_metadata", "")
        tokens["x_office_session_id"] = aug.get("x_office_session_id", "")

    return tokens


def tokens_hash(tokens: dict) -> str:
    """Hash token values for change detection."""
    content = json.dumps(tokens, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def push_tokens(target_url: str, tokens: dict, sync_key: str = "") -> bool:
    """Push tokens to remote server via HTTP POST."""
    import urllib.request
    import urllib.error

    url = target_url.rstrip("/") + "/token/sync"
    payload = dict(tokens)
    if sync_key:
        payload["sync_key"] = sync_key

    data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("status") == "ok":
                logger.info("[OK] Token sync successful: %s", result.get("message", ""))
                return True
            else:
                logger.error("[FAIL] Server returned: %s", result.get("error", "unknown"))
                return False
    except urllib.error.HTTPError as e:
        logger.error("[FAIL] HTTP %d: %s", e.code, e.read().decode("utf-8", errors="replace")[:200])
        return False
    except urllib.error.URLError as e:
        logger.error("[FAIL] Connection error: %s", e.reason)
        return False
    except Exception as e:
        logger.error("[FAIL] %s", e)
        return False


def export_tokens(tokens: dict, output_path: str):
    """Export tokens to a JSON file (for manual transfer via SCP/USB etc.)."""
    out = Path(output_path)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2, ensure_ascii=False)
    logger.info("Tokens exported to: %s", out.absolute())
    logger.info("  bearer_token: %d chars", len(tokens.get("bearer_token", "")))
    logger.info("  auth_token:   %d chars", len(tokens.get("auth_token", "")))
    logger.info("")
    logger.info("On the Linux server, import with:")
    logger.info("  python import_token.py %s", out.name)
    logger.info("  # or copy to the server and run:")
    logger.info("  curl -X POST http://server:8080/token/sync -H 'Content-Type: application/json' -d @%s", out.name)


def main():
    parser = argparse.ArgumentParser(description="Push tokens from Windows to Linux server")
    parser.add_argument("--target", help="Target server URL (e.g. http://192.168.1.100:8080)")
    parser.add_argument("--sync-key", default="", help="Sync authentication key")
    parser.add_argument("--daemon", action="store_true", help="Run as daemon, auto-push on change")
    parser.add_argument("--interval", type=int, default=120, help="Check interval in seconds for daemon mode (default: 120)")
    parser.add_argument("--export", metavar="FILE", help="Export tokens to JSON file instead of pushing")
    args = parser.parse_args()

    tokens = load_tokens()

    if not tokens["bearer_token"]:
        logger.error("No bearer_token found!")
        logger.error("Make sure the proxy is running on Windows first and has acquired a token.")
        logger.error("Check: config.yaml or .augloop_token")
        sys.exit(1)

    logger.info("Tokens loaded:")
    logger.info("  bearer_token:         %d chars", len(tokens["bearer_token"]))
    logger.info("  auth_token:           %d chars", len(tokens["auth_token"]))
    logger.info("  x_client_metadata:    %s", "yes" if tokens["x_client_metadata"] else "no")
    logger.info("  x_office_session_id:  %s", tokens["x_office_session_id"][:20] + "..." if tokens["x_office_session_id"] else "no")

    # Export mode
    if args.export:
        export_tokens(tokens, args.export)
        return

    # Push mode
    if not args.target:
        logger.error("--target is required (e.g. --target http://192.168.1.100:8080)")
        logger.error("Or use --export to save tokens to a file.")
        sys.exit(1)

    if not args.daemon:
        # One-shot push
        success = push_tokens(args.target, tokens, args.sync_key)
        sys.exit(0 if success else 1)

    # Daemon mode: watch for changes and auto-push
    logger.info("")
    logger.info("Daemon mode: watching for token changes every %ds", args.interval)
    logger.info("Target: %s", args.target)
    logger.info("Press Ctrl+C to stop")
    logger.info("")

    last_hash = ""

    try:
        while True:
            tokens = load_tokens()
            current_hash = tokens_hash(tokens)

            if current_hash != last_hash:
                if last_hash:
                    logger.info("Token change detected! Pushing...")
                else:
                    logger.info("Initial push...")

                success = push_tokens(args.target, tokens, args.sync_key)
                if success:
                    last_hash = current_hash
                else:
                    logger.warning("Push failed, will retry in %ds", args.interval)
            else:
                logger.debug("No changes detected")

            time.sleep(args.interval)

    except KeyboardInterrupt:
        logger.info("Daemon stopped.")


if __name__ == "__main__":
    main()
