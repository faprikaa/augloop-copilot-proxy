#!/usr/bin/env python3
"""
import_token.py - Import tokens from a JSON bundle file into config.yaml

Use this when you manually copy a token bundle (from sync_token.py --export) to the server.

Usage:
  python import_token.py token_bundle.json
  python import_token.py token_bundle.json --restart
"""

import json
import logging
import sys
from pathlib import Path

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [import] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
TOKEN_FILE = SCRIPT_DIR / ".augloop_token"


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_token.py <token_bundle.json>")
        sys.exit(1)

    bundle_path = Path(sys.argv[1])
    if not bundle_path.exists():
        logger.error("File not found: %s", bundle_path)
        sys.exit(1)

    with open(bundle_path, "r", encoding="utf-8") as f:
        tokens = json.load(f)

    # Load or create config
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    else:
        # Copy from example if available
        example = SCRIPT_DIR / "config.example.yaml"
        if example.exists():
            with open(example, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
        else:
            config = {}

    aug = config.setdefault("augloop", {})

    # Update tokens
    updated = []

    bearer = tokens.get("bearer_token", "")
    if bearer and len(bearer) > 20:
        aug["bearer_token"] = bearer
        TOKEN_FILE.write_text(bearer, encoding="utf-8")
        updated.append(f"bearer_token ({len(bearer)} chars)")

    auth = tokens.get("auth_token", "")
    if auth:
        aug["auth_token"] = auth
        updated.append(f"auth_token ({len(auth)} chars)")

    meta = tokens.get("x_client_metadata", "")
    if meta:
        aug["x_client_metadata"] = meta
        updated.append("x_client_metadata")

    session = tokens.get("x_office_session_id", "")
    if session:
        aug["x_office_session_id"] = session
        updated.append("x_office_session_id")

    # Save config
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("Tokens imported successfully!")
    for item in updated:
        logger.info("  ✓ %s", item)

    if not updated:
        logger.warning("No valid tokens found in bundle file!")
        sys.exit(1)

    logger.info("")
    logger.info("Restart the server to apply: python run_server.py")
    logger.info("Or if already running, tokens will be picked up on next request via /token/sync")


if __name__ == "__main__":
    main()
