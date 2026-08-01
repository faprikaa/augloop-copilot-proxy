#!/usr/bin/env python3
"""
token_manager.py - 统一 Token 管理

聚合 4 种 Token 获取方案，提供统一的 Token 管理接口:
  方案 A: MITM 代理 (.augloop_token 文件)
  方案 B: HAR 文件提取 (har_extractor)
  方案 C: Frida 守护进程 (frida_daemon, 内存扫描)
  方案 D: WAM 静默获取 (wam_token_provider, MSAL.NET broker)

功能:
  1. 按优先级自动尝试所有方案获取 Token
  2. Token 有效性检查 (JWE header 解码 + 过期时间)
  3. 自动刷新 (后台定时检查)
  4. 状态查询 (哪个方案可用、Token 预览、过期时间)

用法:
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
    """统一 Token 管理器"""

    def __init__(self, config: dict | None = None, config_path: str | None = None):
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        self.config = config or self._load_config()

        # 运行时状态
        self._token: str = ""
        self._source: str = ""  # mitm / har / frida / wam / config
        self._obtained_at: float = 0
        self._expires_at: float = 0
        self._auto_refresh_task: asyncio.Task | None = None
        self._refresh_interval: int = self.config.get("token_manager", {}).get("refresh_interval", 300)
        self._preemptive_refresh_threshold: int = self.config.get("token_manager", {}).get("preemptive_refresh_threshold", 600)

        # 初始化: 尝试加载已有 token
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
        """从各种来源加载已有 token"""
        # 1. .augloop_token 文件 (MITM 代理自动抓取)
        if TOKEN_FILE.exists():
            tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
            if tok and len(tok) > 20:
                self._token = tok
                self._source = "mitm"
                self._obtained_at = time.time()
                self._parse_expiry()
                logger.info("Token loaded from .augloop_token (MITM)")
                return

        # 2. config.yaml 中的 token
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
        """尝试从 JWE token header 解析过期时间"""
        if not self._token:
            return

        try:
            parts = self._token.split(".")
            if len(parts) < 1:
                return

            # JWE header (第一部分)
            header_b64 = parts[0]
            # 补齐 padding
            padding = 4 - len(header_b64) % 4
            if padding != 4:
                header_b64 += "=" * padding

            header_data = json.loads(base64.urlsafe_b64decode(header_b64))

            # JWE header 通常不包含 exp，但尝试一下
            if "exp" in header_data:
                self._expires_at = float(header_data["exp"])
                logger.info("Token expires at: %s", time.ctime(self._expires_at))
            else:
                # AugLoop token 通常 1 小时有效期
                self._expires_at = self._obtained_at + 3600
                logger.info("Token estimated expiry: %s (1h from load)", time.ctime(self._expires_at))

        except Exception as e:
            logger.debug("Could not parse token expiry: %s", e)
            # 默认 1 小时
            self._expires_at = self._obtained_at + 3600

    # ── 公开属性 ────────────────────────────────────────────────────────────

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
            return False  # 未知过期时间，假设未过期
        return time.time() > self._expires_at - 60  # 提前 1 分钟认为过期

    @property
    def expires_in(self) -> int:
        """剩余有效秒数"""
        if self._expires_at == 0:
            return -1  # 未知
        return max(0, int(self._expires_at - time.time()))

    @property
    def token_preview(self) -> str:
        if not self._token:
            return "(empty)"
        return self._token[:40] + "..." if len(self._token) > 40 else self._token

    # ── Token 获取方案 ──────────────────────────────────────────────────────

    async def get_token(self, force_refresh: bool = False) -> str:
        """
        获取有效 Token，按优先级尝试所有方案

        Args:
            force_refresh: 强制刷新 (忽略缓存)
        """
        if not force_refresh and self.has_token and not self.is_expired:
            return self._token

        logger.info("Token needs refresh (expired=%s, has=%s)", self.is_expired, self.has_token)

        # 按优先级尝试 (auto 策略优先: 内存扫描 + WebSocket Phase 1)
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
        return self._token  # 返回可能过期的 token

    async def _try_auto(self) -> str | None:
        """方案 E: 纯 Python 内存扫描 + WebSocket Phase 1 自动获取

        Copilot UI 禁用后的主策略:
        1. 优先: ctypes 内存扫描 Excel 进程获取 JWE Token
        2. 回退: WebSocket Phase 1 获取 anonymousToken (JWT)
        """
        # 方案 E1: 内存扫描
        try:
            from memory_token_scanner import scan_once as memory_scan_once
            result = await asyncio.to_thread(lambda: memory_scan_once(find_all=True))
            jwe_list = result.get("jwe_list", [])
            if jwe_list:
                # 取最新的 (列表最后一个)
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

        # 方案 E2: WebSocket Phase 1 (由 server.py 的 auto_acquire 处理, 此处仅返回 None 触发回退)
        # Phase 1 自动获取逻辑在 AugLoopWSClient.auto_acquire_auth_token() 中
        # 此处不直接调用, 避免循环依赖
        return None

    async def _try_mitm(self) -> str | None:
        """方案 A: 从 MITM 代理的 .augloop_token 文件读取"""
        if not TOKEN_FILE.exists():
            return None

        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if tok and len(tok) > 20:
            logger.info("Found token in .augloop_token")
            return tok
        return None

    async def _try_frida(self) -> str | None:
        """方案 C: 通过 Frida 从 Excel 内存扫描 token"""
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
            # 简化的内存扫描脚本
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
            await asyncio.sleep(3)  # 等待扫描
            script.unload()
            session.detach()

            if found_token:
                logger.info("Token found via Frida memory scan")
                return found_token

        except Exception as e:
            logger.debug("Frida scan failed: %s", e)

        return None

    async def _try_wam(self) -> str | None:
        """方案 D: 通过 WAM 静默获取 token"""
        try:
            from wam_token_provider import try_all_combinations
        except ImportError:
            logger.debug("wam_token_provider not available, skipping")
            return None

        try:
            # 在线程中运行 (避免阻塞事件循环)
            result = await asyncio.to_thread(try_all_combinations)
            if result and "access_token" in result:
                logger.info("Token obtained via WAM")
                return result["access_token"]
        except Exception as e:
            logger.debug("WAM strategy failed: %s", e)

        return None

    async def _try_har(self) -> str | None:
        """方案 B: 从 HAR 文件提取 token"""
        try:
            from har_extractor import load_har, extract_augloop_token
        except ImportError:
            logger.debug("har_extractor not available, skipping")
            return None

        har_dir = SCRIPT_DIR.parent / "har"
        if not har_dir.exists():
            return None

        # 找最新的 HAR 文件
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
        """更新 config.yaml 中的 token"""
        try:
            self.config.setdefault("augloop", {})["bearer_token"] = token
            self._save_config()
        except Exception as e:
            logger.warning("Failed to save token to config: %s", e)

    # ── 状态查询 ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """获取 Token 管理器状态"""
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

    # ── 手动操作 ────────────────────────────────────────────────────────────

    def set_token(self, token: str, source: str = "manual"):
        """手动设置 Token"""
        self._token = token
        self._source = source
        self._obtained_at = time.time()
        self._parse_expiry()
        self._update_config(token)
        logger.info("Token set manually (source=%s)", source)

    async def refresh(self) -> str:
        """强制刷新 Token"""
        return await self.get_token(force_refresh=True)

    def extract_from_har(self, har_path: str) -> dict:
        """从指定 HAR 文件提取 Token"""
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

            # 更新其他配置
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

    # ── 自动刷新 ────────────────────────────────────────────────────────────

    def start_auto_refresh(self, interval: int = 300):
        """启动后台自动刷新"""
        if self._auto_refresh_task:
            return

        self._refresh_interval = interval
        self._auto_refresh_task = asyncio.create_task(self._refresh_loop())
        logger.info("Auto-refresh started (interval=%ds)", interval)

    async def stop_auto_refresh(self):
        """停止自动刷新"""
        if self._auto_refresh_task:
            self._auto_refresh_task.cancel()
            self._auto_refresh_task = None
            logger.info("Auto-refresh stopped")

    # 提前刷新阈值: 剩余时间少于此时就主动刷新 (默认 600 秒 = 10 分钟)
    _preemptive_refresh_threshold: int = 600

    async def _refresh_loop(self):
        """后台刷新循环 - 提前 10 分钟主动刷新, auto 策略优先, 失败后回退 HTTP /token/auto"""
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                # 提前刷新: 剩余时间 < 阈值 或已过期 或无 token
                should_refresh = (
                    self.is_expired
                    or not self.has_token
                    or (0 < self.expires_in < self._preemptive_refresh_threshold)
                )
                if should_refresh:
                    reason = "expired" if self.is_expired else ("missing" if not self.has_token else f"expiring soon ({self.expires_in}s left)")
                    logger.info("Auto-refresh: %s, refreshing via get_token()...", reason)
                    new_token = await self.get_token(force_refresh=True)

                    # 如果 get_token() 所有策略都失败, 尝试 HTTP 调用 /token/auto
                    if (not new_token or self.is_expired) and self._auto_fallback_enabled:
                        logger.warning("All strategies failed, trying HTTP /token/auto fallback...")
                        await self._http_auto_fallback()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Auto-refresh error: %s", e)

    @property
    def _auto_fallback_enabled(self) -> bool:
        """是否启用 HTTP /token/auto 回退 (当 server.py 在本地运行时)"""
        return True

    async def _http_auto_fallback(self):
        """通过本地 HTTP /token/auto 端点获取 Token (WebSocket Phase 1)"""
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
