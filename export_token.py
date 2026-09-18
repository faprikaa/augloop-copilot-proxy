#!/usr/bin/env python3
"""
export_token.py - Export tokens from Windows to a file for Linux deployment

Reads tokens from config.yaml / .augloop_token and saves them to a single
JSON file that can be uploaded to the Linux server.

Usage:
  python export_token.py                      # Export to token_bundle.json
  python export_token.py -o my_tokens.json    # Custom output path
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [export] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("export")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
TOKEN_FILE = SCRIPT_DIR / ".augloop_token"


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Export tokens for Linux deployment")
    parser.add_argument("-o", "--output", default="token_bundle.json",
                        help="Output file path (default: token_bundle.json)")
    args = parser.parse_args()

    try:
        import yaml
    except ImportError:
        logger.error("PyYAML required: pip install pyyaml")
        sys.exit(1)

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
            logger.info("bearer_token loaded from .augloop_token (%d chars)", len(tok))

    # 2. From config.yaml
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        aug = cfg.get("augloop", {})

        if not tokens["bearer_token"]:
            bt = aug.get("bearer_token", "")
            if bt and len(bt) > 20:
                tokens["bearer_token"] = bt
                logger.info("bearer_token loaded from config.yaml (%d chars)", len(bt))

        at = aug.get("auth_token", "")
        if at:
            tokens["auth_token"] = at
            logger.info("auth_token loaded (%d chars)", len(at))

        meta = aug.get("x_client_metadata", "")
        if meta:
            tokens["x_client_metadata"] = meta
            logger.info("x_client_metadata loaded")

        sid = aug.get("x_office_session_id", "")
        if sid:
            tokens["x_office_session_id"] = sid
            logger.info("x_office_session_id loaded")

    # Validate
    if not tokens["bearer_token"]:
        logger.error("No bearer_token found!")
        logger.error("Make sure the proxy is running and has acquired a token first.")
        sys.exit(1)

    # Save
    out = Path(args.output)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 55)
    print("  Token Export Successful")
    print("=" * 55)
    print()
    print(f"  File:           {out.absolute()}")
    print(f"  bearer_token:   {len(tokens['bearer_token'])} chars")
    print(f"  auth_token:     {len(tokens['auth_token'])} chars")
    print(f"  x_client_meta:  {'yes' if tokens['x_client_metadata'] else 'no'}")
    print(f"  session_id:     {'yes' if tokens['x_office_session_id'] else 'no'}")
    print()
    print("  Upload this file to your Linux server, then run:")
    print(f"    python import_token.py {out.name}")
    print()


if __name__ == "__main__":
    main()
