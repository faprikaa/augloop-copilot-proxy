#!/usr/bin/env python3
"""
run.py - One-Click Launcher: Excel Background Token Harvesting + OpenAI-Compatible Proxy Server

Flow:
  1. Dispatch launches own Excel instance (does not touch other user Excel windows)
  2. Prompts user to open Copilot in the new Excel and send a message
  3. Hides Excel on Enter key, running silently in background
  4. Starts proxy server (http://127.0.0.1:8080)
  5. Automatically scans Excel memory every 50 minutes, verifying and updating Token
  6. On Ctrl+C exit, automatically closes Excel

Usage:
  python run.py                    # Interactive mode (recommended)
  python run.py --no-wait          # Skip prompt, hide Excel immediately
  python run.py --interval 1800    # Refresh every 30 minutes
  python run.py --port 8080        # Specify port
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# Ensure current directory is in path
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run")


def main():
    parser = argparse.ArgumentParser(description="Excel Background Token Harvester + Proxy Server")
    parser.add_argument("--mode", choices=["hide", "minimize", "offscreen"],
                        default="hide", help="Excel window hide mode (default: hide)")
    parser.add_argument("--interval", type=float, default=3000.0,
                        help="Forced refresh interval in seconds (default: 3000 = 50 minutes)")
    parser.add_argument("--no-wait", action="store_true",
                        help="Do not wait for user, hide Excel immediately")
    parser.add_argument("--auto-init", action="store_true",
                        help="Automatically open Copilot and send message to initialize (no user interaction)")
    parser.add_argument("--no-validate", action="store_true",
                        help="Skip Token validation")
    parser.add_argument("--port", type=int, default=8080,
                        help="Proxy server port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Proxy server host address (default: 127.0.0.1)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Excel Copilot Proxy Server (Background Token Harvester)")
    print("=" * 60)
    print()
    print(f"  Hide Mode:        {args.mode}")
    print(f"  Refresh Interval: {args.interval:.0f} sec ({args.interval / 60:.0f} min)")
    print(f"  Server URL:       http://{args.host}:{args.port}")
    print(f"  API Endpoint:     http://{args.host}:{args.port}/v1/chat/completions")
    print()

    # ── Step 1: Start Excel Background Runner ──
    logger.info("Step 1: Starting Excel background Token harvester...")
    from excel_background_runner import ExcelBackgroundRunner

    runner = ExcelBackgroundRunner(
        hide_mode=args.mode,
        auto_close=True,
        scan_interval=args.interval,
        validate=not args.no_validate,
    )
    runner.start(wait_for_user=not args.no_wait and not args.auto_init, auto_init=args.auto_init)

    # ── Step 2: Start Background Scanning Thread ──
    logger.info("Step 2: Starting background Token scanning thread...")

    scan_stop = threading.Event()

    def bg_scan_loop():
        """Background scan loop: force refreshes every interval seconds (trigger Excel + scan + validate)"""
        while not scan_stop.is_set():
            try:
                # 🔑 Force refresh: trigger Excel Copilot to generate fresh Token
                logger.info("[Force Refresh] Triggering Excel Copilot to refresh Token...")
                try:
                    from excel_trigger import trigger_excel_token_refresh
                    trigger_excel_token_refresh(wait_seconds=10)
                except Exception as e:
                    logger.warning("[Force Refresh] Failed to trigger Excel: %s", e)

                # Scan + validate + save
                result = runner.scan_validate_and_save()
                if result and result.get("jwe"):
                    logger.info("[Force Refresh] JWE Token updated (len=%d)", len(result["jwe"]))
                else:
                    logger.warning("[Force Refresh] No valid Token acquired, awaiting next cycle")
            except Exception as e:
                logger.error("[Force Refresh] Exception: %s", e)

            # Wait for next scan (can be interrupted by stop event)
            scan_stop.wait(args.interval)

    scan_thread = threading.Thread(target=bg_scan_loop, daemon=True)
    scan_thread.start()
    logger.info("Background scan thread started (every %.0f min)", args.interval / 60)

    # ── Step 3: Start Proxy Server ──
    logger.info("Step 3: Starting proxy server...")

    # Immediate scan to ensure Token is loaded
    logger.info("Initial Token scan...")
    runner.scan_validate_and_save()

    try:
        import uvicorn
        from server import app

        logger.info("=" * 60)
        logger.info("Proxy server starting at: http://%s:%d", args.host, args.port)
        logger.info("Excel is running hidden in background (PID=%d)", runner._excel_pid)
        logger.info("Press Ctrl+C to exit -> Excel closes automatically")
        logger.info("=" * 60)

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )

    except ImportError:
        logger.error("uvicorn required: pip install uvicorn fastapi")
        logger.info("Falling back to direct server.py execution...")
        import subprocess
        subprocess.run([sys.executable, str(SCRIPT_DIR / "server.py")],
                       cwd=str(SCRIPT_DIR))

    except KeyboardInterrupt:
        logger.info("User interrupted (Ctrl+C)")

    finally:
        # ── Step 4: Cleanup ──
        logger.info("Cleaning up...")
        scan_stop.set()
        scan_thread.join(timeout=5)
        runner.stop()
        logger.info("Exited cleanly, Excel closed")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Interrupted.")
        sys.exit(0)
