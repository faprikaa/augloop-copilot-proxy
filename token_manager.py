#!/usr/bin/env python3
"""
token_manager.py - Unified Token Manager

Aggregates 4 token acquisition strategies, providing a unified token management interface:
  Strategy A: MITM Proxy (.augloop_token file)
  Strategy B: HAR file extraction (har_extractor)
  Strategy C: Frida daemon (frida_daemon, memory scan)
  Strategy D: WAM silent acquisition (wam_token_provider, MSAL.NET broker)

Features:
  1. Automatically tries all strategies in priority order to acquire token
  2. Token validity verification (JWE header decoding + expiration checking)
  3. Automatic refresh (background periodic checks)
  4. Status inspection (active strategy, token preview, expiration time)

Usage:
    mgr = TokenManager(config)
    token = await mgr.get_token()
    status = mgr.get_status()
"""

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("token_manager")

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.yaml"
TOKEN_FILE = SCRIPT_DIR / ".augloop_token"


class TokenManager:
    """Unified Token Manager"""

    def __init__(self, config: dict | None = None, config_path: str | None = None):
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self.config = config or self._load_config()

        # Runtime state
        self._token: str = ""
        self._source: str = ""  # mitm / har / frida / wam / config
        self._obtained_at: float = 0
        self._expires_at: float = 0
        self._auto_refresh_task: asyncio.Task | None = None
        self._refresh_interval: int = self.config.get("token_manager", {}).get("refresh_interval", 300)
        self._preemptive_refresh_threshold: int = self.config.get("token_manager", {}).get("preemptive_refresh_threshold", 600)

        # Initialization: try loading existing token
        self._load_existing_token()

    def _load_config(self) -> dict:
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _save_config(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def _load_existing_token(self):
        """Load existing token from various sources"""
        # 1. .augloop_token file (MITM proxy capture)
        if TOKEN_FILE.exists():
            tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if tok and len(tok) > 20:
                self._token = tok
                self._source = "mitm"
                self._obtained_at = time.time()
                self._parse_expiry()
                logger.info("Token loaded from .augloop_token (MITM)")
                return

        # 2. Token from config.yaml
        aug = self.config.get("augloop", {})
        tok = aug.get("bearer_token", "")
        if tok and len(tok) > 20:
            self._token = tok
            self._source = "config"
            self._obtained_at = time.time()
            self._parse_expiry()
            logger.info("Token loaded from config.yaml")
            return

        logger.warning("No existing token found")

    def _parse_expiry(self):
        """Attempt to parse expiration time from JWE token header"""
        if not self._token:
            return

        try:
            parts = self._token.split(".")
            if len(parts) < 1:
                return

            # JWE header (first segment)
            header_b64 = parts[0]
            # Pad base64 string
            padding = 4 - len(header_b64) % 4
            if padding != 4:
                header_b64 += "=" * padding

            header_data = json.loads(base64.urlsafe_b64decode(header_b64))

            # JWE header typically does not contain exp, but check just in case
            if "exp" in header_data:
                self._expires_at = float(header_data["exp"])
                logger.info("Token expires at: %s", time.ctime(self._expires_at))
            else:
                # AugLoop tokens are typically valid for 1 hour
                self._expires_at = self._obtained_at + 3600
                logger.info("Token estimated expiry: %s (1h from load)", time.ctime(self._expires_at))

        except Exception as e:
            logger.debug("Could not parse token expiry: %s", e)
            # Default: 1 hour
            self._expires_at = self._obtained_at + 3600

    # ── Public Properties ───────────────────────────────────────────────────

    @property
    def token(self) -> str:
        return self._token

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    @property
    def source(self) -> str:
        return self._source

    @property
    def is_expired(self) -> bool:
        if not self._token:
            return True
        if self._expires_at == 0:
            return False  # Unknown expiration, assume not expired
        return time.time() > self._expires_at - 60  # Treat as expired 1 minute early

    @property
    def expires_in(self) -> int:
        """Remaining valid seconds"""
        if self._expires_at == 0:
            return -1  # Unknown
        return max(0, int(self._expires_at - time.time()))

    @property
    def token_preview(self) -> str:
        if not self._token:
            return "(empty)"
        return self._token[:40] + "..." if len(self._token) > 40 else self._token

    # ── Token Acquisition Strategies ────────────────────────────────────────

    async def get_token(self, force_refresh: bool = False) -> str:
        """
        Get valid Token, trying all strategies in priority order

        Args:
            force_refresh: Force refresh (ignore cache)
        """
        if not force_refresh and self.has_token and not self.is_expired:
            return self._token

        logger.info("Token needs refresh (expired=%s, has=%s)", self.is_expired, self.has_token)

        # Try by priority (auto strategy first: memory scan + WebSocket Phase 1)
        strategies = [
            ("auto", self._try_auto),
            ("mitm", self._try_mitm),
            ("frida", self._try_frida),
            ("wam", self._try_wam),
            ("har", self._try_har),
        ]

        for name, strategy in strategies:
            try:
                logger.info("Trying token strategy: %s", name)
                token = await strategy()
                if token and len(token) > 20:
                    self._token = token
                    self._source = name
                    self._obtained_at = time.time()
                    self._parse_expiry()
                    self._update_config(token)
                    logger.info("[OK] Token obtained via %s", name)
                    return token
            except Exception as e:
                logger.warning("Strategy %s failed: %s", name, e)

        logger.error("All token strategies failed")
        return self._token  # Return potentially expired token

    async def _try_auto(self) -> str | None:
        """Strategy E: Pure Python memory scan + WebSocket Phase 1 auto acquisition

        Primary strategy after Copilot UI deprecation:
        1. Preferred: ctypes memory scan of Excel process to obtain JWE Token
        2. Fallback: WebSocket Phase 1 to obtain anonymousToken (JWT)
        """
        # Strategy E1: Memory scan
        try:
            from memory_token_scanner import scan_once as memory_scan_once
            result = await asyncio.to_thread(lambda: memory_scan_once(find_all=True))
            jwe_list = result.get("jwe_list", [])
            if jwe_list:
                # Take the newest (last in list)
                token = jwe_list[-1]
                if token and len(token) > 20:
                    logger.info("Token found via auto/memory_scan (%d JWE candidates)", len(jwe_list))
                    return token
            else:
                logger.debug("Memory scan: no JWE token found")
        except ImportError:
            logger.debug("memory_token_scanner not available, skipping auto strategy")
        except Exception as e:
            logger.debug("Auto/memory_scan strategy failed: %s", e)

        # Strategy E2: WebSocket Phase 1 (handled by auto_acquire in server.py, returns None here to trigger fallback)
        # Phase 1 auto acquisition logic resides in AugLoopWSClient.auto_acquire_auth_token()
        # Not invoked directly here to avoid circular dependencies
        return None

    async def _try_mitm(self) -> str | None:
        """Strategy A: Read from MITM proxy .augloop_token file"""
        if not TOKEN_FILE.exists():
            return None

        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok and len(tok) > 20:
            logger.info("Found token in .augloop_token")
            return tok
        return None

    async def _try_frida(self) -> str | None:
        """Strategy C: Scan token from Excel memory via Frida"""
        try:
            import frida
        except ImportError:
            logger.debug("Frida not installed, skipping")
            return None

        try:
            device = frida.get_local_device()
            procs = [p for p in device.enumerate_processes() if "excel" in p.name.lower()]
            if not procs:
                logger.debug("Excel not running, Frida strategy skipped")
                return None

            pid = procs[0].pid
            logger.info("Excel found (PID=%d), scanning memory...", pid)

            session = device.attach(pid)
            # Simplified memory scan script
            js_code = """
            var JWE_PATTERN = "65 79 4a 68 62 47 63 69 4f 69 4a 6b 61 58 49 69";
            var ranges = Process.enumerateRanges("rw-");
            var found = null;

            for (var i = 0; i < ranges.length && !found; i++) {
                var range = ranges[i];
                if (range.size > 100 * 1024 * 1024) continue;
                try {
                    Memory.scanSync(range.base, range.size, JWE_PATTERN).forEach(function(match) {
                        if (found) return;
                        try {
                            var data = ptr(match.address).readUtf8String(2000);
                            if (data) {
                                var m = data.match(/(eyJhbGciOiJkaXIi[A-Za-z0-9_\\-\\.]+)/);
                                if (m && m[1].length > 100) {
                                    found = m[1];
                                }
                            }
                        } catch(e) {}
                    });
                } catch(e) {}
            }
            send(found || "NOT_FOUND");
            """

            script = session.create_script(js_code)
            found_token = None

            def on_message(msg, data):
                nonlocal found_token
                if msg["type"] == "send":
                    val = msg["payload"]
                    if val and val != "NOT_FOUND":
                        found_token = val

            script.on("message", on_message)
            script.load()
            await asyncio.sleep(3)  # Wait for scan
            script.unload()
            session.detach()

            if found_token:
                logger.info("Token found via Frida memory scan")
                return found_token

        except Exception as e:
            logger.debug("Frida scan failed: %s", e)

        return None

    async def _try_wam(self) -> str | None:
        """Strategy D: Silently acquire token via WAM"""
        try:
            from wam_token_provider import try_all_combinations
        except ImportError:
            logger.debug("wam_token_provider not available, skipping")
            return None

        try:
            # Run in thread (avoid blocking event loop)
            result = await asyncio.to_thread(try_all_combinations)
            if result and "access_token" in result:
                logger.info("Token obtained via WAM")
                return result["access_token"]
        except Exception as e:
            logger.debug("WAM strategy failed: %s", e)

        return None

    async def _try_har(self) -> str | None:
        """Strategy B: Extract token from HAR file"""
        try:
            from har_extractor import load_har, extract_augloop_token
        except ImportError:
            logger.debug("har_extractor not available, skipping")
            return None

        har_dir = SCRIPT_DIR.parent / "har"
        if not har_dir.exists():
            return None

        # Find newest HAR file
        har_files = sorted(har_dir.glob("*.har"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not har_files:
            return None

        try:
            har = load_har(str(har_files[0]))
            info = extract_augloop_token(har)
            if "bearer_token" in info:
                logger.info("Token extracted from HAR: %s", har_files[0].name)
                return info["bearer_token"]
        except Exception as e:
            logger.debug("HAR extraction failed: %s", e)

        return None

    def _update_config(self, token: str):
        """Update token in config.yaml"""
        try:
            self.config.setdefault("augloop", {})["bearer_token"] = token
            self._save_config()
        except Exception as e:
            logger.warning("Failed to save token to config: %s", e)

    # ── Status Query ────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get Token Manager status"""
        return {
            "has_token": self.has_token,
            "source": self._source,
            "token_preview": self.token_preview,
            "token_length": len(self._token),
            "obtained_at": time.ctime(self._obtained_at) if self._obtained_at else None,
            "expires_at": time.ctime(self._expires_at) if self._expires_at else None,
            "expires_in_seconds": self.expires_in,
            "is_expired": self.is_expired,
            "auto_refresh": self._auto_refresh_task is not None,
            "strategies": {
                "mitm": TOKEN_FILE.exists(),
                "frida": self._check_frida_available(),
                "wam": self._check_wam_available(),
                "har": self._check_har_available(),
            },
        }

    def _check_frida_available(self) -> bool:
        try:
            import frida
            device = frida.get_local_device()
            procs = [p for p in device.enumerate_processes() if "excel" in p.name.lower()]
            return len(procs) > 0
        except Exception:
            return False

    def _check_wam_available(self) -> bool:
        try:
            from wam_token_provider import EXE_PATH
            return EXE_PATH.exists()
        except Exception:
            return False

    def _check_har_available(self) -> bool:
        har_dir = SCRIPT_DIR.parent / "har"
        return har_dir.exists() and any(har_dir.glob("*.har"))

    # ── Manual Operations ───────────────────────────────────────────────────

    def set_token(self, token: str, source: str = "manual"):
        """Manually set Token"""
        self._token = token
        self._source = source
        self._obtained_at = time.time()
        self._parse_expiry()
        self._update_config(token)
        logger.info("Token set manually (source=%s)", source)

    async def refresh(self) -> str:
        """Force refresh Token"""
        return await self.get_token(force_refresh=True)

    def extract_from_har(self, har_path: str) -> dict:
        """Extract Token from specified HAR file"""
        try:
            from har_extractor import load_har, extract_augloop_token, extract_graph_token
        except ImportError:
            return {"error": "har_extractor not available"}

        har = load_har(har_path)
        aug_info = extract_augloop_token(har)
        graph_info = extract_graph_token(har)

        if "bearer_token" in aug_info:
            self._token = aug_info["bearer_token"]
            self._source = "har"
            self._obtained_at = time.time()
            self._parse_expiry()
            self._update_config(aug_info["bearer_token"])

            # Update other configs
            aug_cfg = self.config.setdefault("augloop", {})
            if "x_client_metadata" in aug_info:
                aug_cfg["x_client_metadata"] = aug_info["x_client_metadata"]
            if "x_office_session_id" in aug_info:
                aug_cfg["x_office_session_id"] = aug_info["x_office_session_id"]
            self._save_config()

        return {
            "augloop_token": bool(aug_info.get("bearer_token")),
            "graph_token": bool(graph_info.get("bearer_token")) if graph_info else False,
            "x_client_metadata": bool(aug_info.get("x_client_metadata")),
            "x_office_session_id": bool(aug_info.get("x_office_session_id")),
        }

    # ── Auto Refresh ────────────────────────────────────────────────────────

    def start_auto_refresh(self, interval: int = 300):
        """Start background auto-refresh"""
        if self._auto_refresh_task:
            return

        self._refresh_interval = interval
        self._auto_refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("Auto-refresh started (interval=%ds)", interval)

    async def stop_auto_refresh(self):
        """Stop auto-refresh"""
        if self._auto_refresh_task:
            self._auto_refresh_task.cancel()
            self._auto_refresh_task = None
            logger.info("Auto-refresh stopped")

    # Preemptive refresh threshold: proactively refresh when remaining time is less than this (default 600s = 10m)
    _preemptive_refresh_threshold: int = 600

    async def _refresh_loop(self):
        """Background refresh loop - Proactively refresh 10m early, auto strategy first, fallback to HTTP /token/auto"""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                # Preemptive refresh: remaining time < threshold, or expired, or no token
                should_refresh = (
                    self.is_expired
                    or not self.has_token
                    or (0 < self.expires_in < self._preemptive_refresh_threshold)
                )
                if should_refresh:
                    reason = "expired" if self.is_expired else ("missing" if not self.has_token else f"expiring soon ({self.expires_in}s left)")
                    logger.info("Auto-refresh: %s, refreshing via get_token()...", reason)
                    new_token = await self.get_token(force_refresh=True)

                    # If all get_token() strategies fail, attempt HTTP call to /token/auto
                    if (not new_token or self.is_expired) and self._auto_fallback_enabled:
                        logger.warning("All strategies failed, trying HTTP /token/auto fallback...")
                        await self._http_auto_fallback()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Auto-refresh error: %s", e)

    @property
    def _auto_fallback_enabled(self) -> bool:
        """Whether HTTP /token/auto fallback is enabled (when server.py is running locally)"""
        return True

    async def _http_auto_fallback(self):
        """Acquire Token via local HTTP /token/auto endpoint (WebSocket Phase 1)"""
        try:
            import httpx
            port = self.config.get("server", {}).get("port", 8080)
            host = self.config.get("server", {}).get("host", "127.0.0.1")
            url = f"http://{host}:{port}/token/auto"
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(url, json={})
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("status") == "ok":
                        jwe = data.get("jwe_token", "")
                        jwt = data.get("auth_token", "")
                        if jwe and len(jwe) > 20:
                            self._token = jwe
                            self._source = "http_auto"
                            self._obtained_at = time.time()
                            self._parse_expiry()
                            self._update_config(jwe)
                            logger.info("[OK] Token obtained via HTTP /token/auto fallback")
                        if jwt:
                            self.config.setdefault("augloop", {})["auth_token"] = jwt
                            self._save_config()
                    else:
                        logger.warning("HTTP /token/auto returned: %s", data)
                else:
                    logger.warning("HTTP /token/auto failed: HTTP %d", resp.status_code)
        except Exception as e:
            logger.warning("HTTP /token/auto fallback error: %s", e)
