#!/usr/bin/env python3
"""
excel_background_runner.py - Excel background silent runner + periodic Token refresh + auto shutdown on exit

🔑 Isolation Principle: Only manages the Excel instance it starts; never affects other user Excel windows.
  - Uses Dispatch to create a brand-new Excel process (does not attach to existing instances)
  - Only scans memory of its own PID (does not affect other Excel processes)
  - Only closes the Excel process it started (Quit or TerminateProcess by PID)

Working Principle:
  1. Dispatch launches a new Excel instance (own process, own PID)
  2. User opens Copilot in the new Excel and sends a message (initializes AugLoop)
  3. Hide Excel window (SW_HIDE) — Copilot WebView2 continues running in the background
  4. Excel automatically refreshes JWE Token (~4-minute cycle) without user interaction
  5. Every N minutes (default 50 min), scans [own PID] memory, verifies Token, and saves it
  6. On script exit (Ctrl+C / atexit / signal), closes only its own started Excel process

Usage:
  # Run continuously in background, refresh Token every 50 minutes (default)
  python excel_background_runner.py

  # Custom refresh interval (e.g. 30 minutes)
  python excel_background_runner.py --interval 1800

  # Scan once and exit
  python excel_background_runner.py --once

  # Non-interactive mode (hide directly without waiting, suitable when Copilot is already initialized)
  python excel_background_runner.py --no-wait

  # Import as module:
  from excel_background_runner import ExcelBackgroundRunner
  with ExcelBackgroundRunner(scan_interval=3000) as runner:
      tokens = runner.scan()
"""

import atexit
import ctypes
import ctypes.wintypes as wintypes
import logging
import os
import signal
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

logger = logging.getLogger("bg_runner")

# ── Windows API ─────────────────────────────────────────────────────────────

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

# keybd_event (used to simulate keyboard in foreground window)
user32.keybd_event.argtypes = [ctypes.c_ushort, ctypes.c_ushort, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
user32.keybd_event.restype = None

# SendInput (used for typing text)
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

user32.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = ctypes.c_uint

# Win32 Constants
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_CHAR = 0x0102
VK_MENU = 0x12   # Alt
VK_Y = 0x59      # Y
VK_RETURN = 0x0D
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL

# ShowWindow Commands
SW_HIDE = 0
SW_SHOWMINNOACTIVE = 7
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5

# Global flag: Whether Excel is hidden (checked by augloop_ws_client)
EXCEL_HIDDEN_FLAG = os.environ.get("EXCEL_HIDDEN", "0") == "1"


class ExcelBackgroundRunner:
    """Excel background silent runner manager

    - Hides Excel window; Copilot WebView2 continues running in background
    - Periodically scans memory, validates and saves valid JWE Token
    - Closes Excel automatically on exit
    """

    XLMAIN_CLASS = "XLMAIN"

    def __init__(
        self,
        hide_mode: str = "hide",
        auto_close: bool = True,
        scan_interval: float = 3000.0,  # Default 50 minutes
        save_token: bool = True,
        validate: bool = True,          # Whether to validate Token validity
    ):
        self.hide_mode = hide_mode
        self.auto_close = auto_close
        self.scan_interval = scan_interval
        self.save_token = save_token
        self.validate = validate

        self._excel = None
        self._excel_pid = 0
        self._hwnd = 0
        self._was_visible = True
        self._was_minimized = False
        self._our_instance = False
        self._started = False
        self._stopped = False
        self._original_rect = None
        self._cycle = 0  # Scan cycle count

    # ── Window Search ──

    def _find_hwnd_for_pid(self, pid: int) -> int:
        """Find the main Excel window for specified PID (only our own instance)"""
        found_hwnd = 0

        def enum_callback(hwnd, lparam):
            nonlocal found_hwnd
            # Check window class name is XLMAIN
            class_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
            if class_buf.value != self.XLMAIN_CLASS:
                return True
            # Check window process ID
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                found_hwnd = hwnd
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                logger.info("Found our Excel window: HWND=%d, PID=%d, Title=%s",
                            hwnd, pid, title.value[:50])
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return found_hwnd

    # ── COM Connection (Always Dispatch new instance, never connect to existing) ──

    def _connect_excel(self):
        """Start a brand-new Excel instance (does not affect existing user instances)"""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            logger.error("pywin32 is required: pip install pywin32")
            raise

        # 🔑 Record existing Excel PIDs (avoid accidentally connecting to user's Excel)
        from memory_token_scanner import find_excel_processes
        existing_pids = set(find_excel_processes())
        if existing_pids:
            logger.info("Existing Excel processes detected (will not touch them): PID=%s", existing_pids)

        # 🔑 Always Dispatch new instance; never use GetActiveObject
        logger.info("Launching fresh Excel instance (Dispatch)...")
        # DispatchEx forces a separate process; plain Dispatch may attach to a running Excel
        try:
            self._excel = win32com.client.gencache.EnsureDispatchEx("Excel.Application")
        except Exception:
            self._excel = win32com.client.DispatchEx("Excel.Application")

        # Excel may still be starting up; retry property sets that fail transiently
        for attempt in range(10):
            try:
                self._excel.Visible = True   # Visible initially for user to open Copilot
                self._excel.DisplayAlerts = False
                break
            except Exception as e:
                if attempt == 9:
                    raise
                logger.info("Excel not ready yet (%s), retrying...", e)
                time.sleep(1)
        try:
            self._excel.Workbooks.Add()
        except Exception:
            pass
        self._our_instance = True
        version = self._excel.Version
        logger.info("Launched new Excel instance (Version %s)", version)

        # Wait for Excel to fully initialize
        time.sleep(3)

        # Obtain HWND — Method 1: Get from COM object (with retries)
        self._hwnd = 0
        for attempt in range(10):
            try:
                self._hwnd = int(self._excel.Hwnd)
                if self._hwnd:
                    break
            except Exception:
                pass
            time.sleep(1)

        # Method 2: Find window via PID difference
        if not self._hwnd:
            new_pids = set(find_excel_processes()) - existing_pids
            if new_pids:
                self._excel_pid = new_pids.pop()
                logger.info("Identified new Excel by PID difference: PID=%d", self._excel_pid)
                self._hwnd = self._find_hwnd_for_pid(self._excel_pid)

        if not self._hwnd:
            raise RuntimeError("Unable to locate window for new Excel instance")

        # Get PID
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self._hwnd, ctypes.byref(pid))
        self._excel_pid = pid.value
        if not self._excel_pid:
            raise RuntimeError("Unable to obtain Excel PID")

        logger.info("Our Excel instance: HWND=%d, PID=%d (only managing this instance)", self._hwnd, self._excel_pid)

        self._was_visible = True   # Started by us, visible initially
        self._was_minimized = False

    # ── Window Hide / Restore ──

    def _hide_excel(self):
        if self.hide_mode == "hide":
            logger.info("Hiding Excel window (SW_HIDE)...")
            user32.ShowWindow(self._hwnd, SW_HIDE)
        elif self.hide_mode == "minimize":
            logger.info("Minimizing Excel window (SW_SHOWMINNOACTIVE)...")
            user32.ShowWindow(self._hwnd, SW_SHOWMINNOACTIVE)
        elif self.hide_mode == "offscreen":
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            self._original_rect = (rect.left, rect.top, rect.right, rect.bottom)
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            user32.SetWindowPos(self._hwnd, 0, -32000, -32000, 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER)
            logger.info("Moved Excel window off-screen")
        else:
            logger.warning("Unknown hide_mode: %s", self.hide_mode)

    def _restore_excel(self):
        if not self._hwnd:
            return
        if self.hide_mode == "hide":
            user32.ShowWindow(self._hwnd, SW_SHOW)
        elif self.hide_mode == "minimize":
            if not self._was_minimized:
                user32.ShowWindow(self._hwnd, SW_RESTORE)
        elif self.hide_mode == "offscreen" and self._original_rect:
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            left, top, _, _ = self._original_rect
            user32.SetWindowPos(self._hwnd, 0, left, top, 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER)
        if self._was_visible:
            user32.SetForegroundWindow(self._hwnd)

    # ── Close Excel ──

    def _close_excel(self):
        """Close Excel (only terminates the PID we started; never touches others)"""
        # Method 1: COM Quit (graceful shutdown)
        if self._excel is not None:
            try:
                logger.info("Closing Excel (Quit, PID=%d)...", self._excel_pid)
                self._excel.Quit()
                logger.info("Excel.Quit() called")
            except Exception as e:
                logger.warning("Excel.Quit() failed: %s", e)
            finally:
                # Release COM object
                try:
                    import win32com.client
                    self._excel = None
                except Exception:
                    pass

        # Wait for process to exit (up to 5 seconds)
        if self._excel_pid:
            import subprocess
            for _ in range(5):
                time.sleep(1)
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {self._excel_pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True
                )
                if str(self._excel_pid) not in result.stdout:
                    logger.info("Excel process exited (PID=%d)", self._excel_pid)
                    return

            # Method 2: Process still alive after Quit, force terminate our PID only
            logger.warning("Process still alive after Quit, force terminating PID=%d...", self._excel_pid)
            try:
                PROCESS_TERMINATE = 0x0001
                handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, self._excel_pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.CloseHandle(handle)
                    logger.info("Excel process force-terminated (PID=%d)", self._excel_pid)
                else:
                    # Fallback: taskkill
                    subprocess.run(["taskkill", "/F", "/PID", str(self._excel_pid)],
                                 capture_output=True)
                    logger.info("Excel process terminated via taskkill (PID=%d)", self._excel_pid)
            except Exception as e:
                logger.error("Failed to terminate Excel process: %s", e)

    # ── Token Validation ──

    @staticmethod
    def _validate_jwe(token: str) -> bool:
        """Validate if JWE Token is active via get_prompts API"""
        try:
            import httpx
            import uuid
            url = (
                "https://augloop.svc.cloud.microsoft/workflows/"
                "OfficeCopilotOrchestrationWorkflow"
                "?includeMetadata=true&tryResolveUpstreamDependencies=true"
                "&outputTypes=AugLoop_OfficeCopilotOrchestration_CopilotPrompts"
            )
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "Microsoft Office/16.0",
                "x-client-metadata": '{"appName":"Excel","appPlatform":"Win32"}',
            }
            body = {
                "promptType": "DynamicActionButton",
                "suggestionContext": {"documentState": "Blank"},
                "copilotLicenseType": "ConsumerPro",
                "requestId": str(uuid.uuid4()),
                "H_": {
                    "T_": "AugLoop_OfficeCopilotOrchestration_CopilotPromptsSignal",
                    "B_": ["AugLoop_Signals_Signal"],
                },
            }
            with httpx.Client(timeout=10, verify=True) as client:
                resp = client.post(url, json=body, headers=headers)
                return resp.status_code == 200
        except Exception as e:
            logger.debug("Token validation exception: %s", e)
            return False

    # ── Memory Scanning (Scans own PID only, never touches other Excel processes) ──

    def scan_validate_and_save(self) -> dict:
        """Scan [own PID] memory -> validate -> save valid Token

        🔑 Only scans self._excel_pid memory, no effect on other Excel processes.

        Returns:
            {"jwe": "valid_token", "jwt": "...", "jwe_valid": True} or empty dict
        """
        from memory_token_scanner import scan_process_memory

        self._cycle += 1
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("[Cycle #%d @ %s] Scanning our Excel process (PID=%d)...", self._cycle, ts, self._excel_pid)

        # 🔑 Scan only our PID
        tokens = scan_process_memory(self._excel_pid, find_all=True)
        jwe_list = tokens.get("jwe_list", [])
        jwt_list = tokens.get("jwt_list", [])

        logger.info("[Cycle #%d] Memory scan: %d JWE, %d JWT", self._cycle, len(jwe_list), len(jwt_list))

        if not jwe_list:
            logger.warning("[Cycle #%d] No JWE Token in memory (Excel Copilot might not be running)", self._cycle)
            return {}

        # Validate JWE Token (starting from newest)
        valid_jwe = None
        if self.validate:
            # Deduplicate
            unique = list(dict.fromkeys(jwe_list))
            logger.info("[Cycle #%d] Validating %d unique JWE Token(s)...", self._cycle, len(unique))

            for i, token in enumerate(reversed(unique)):
                idx = len(unique) - i
                ok = self._validate_jwe(token)
                if ok:
                    valid_jwe = token
                    logger.info("[Cycle #%d] JWE #%d verified valid (len=%d) ✓", self._cycle, idx, len(token))
                    break
                else:
                    logger.info("[Cycle #%d] JWE #%d expired (len=%d) ✗", self._cycle, idx, len(token))

            if not valid_jwe:
                logger.warning("[Cycle #%d] All JWE Tokens expired! Triggering Excel Copilot refresh...", self._cycle)
                # 🔑 Do not save expired token! Trigger Excel Copilot refresh to generate new token
                self._trigger_copilot_refresh()
                # Return empty result, rescan next cycle
                result = {"jwe": "", "jwe_valid": False}
                if jwt_list:
                    result["jwt"] = jwt_list[-1]
                return result
        else:
            valid_jwe = jwe_list[-1]

        # Save (clear stale/invalid tokens, keeping only new valid tokens)
        result = {"jwe": valid_jwe, "jwe_valid": True}
        if jwt_list:
            result["jwt"] = jwt_list[-1]

        if self.save_token and valid_jwe:
            from memory_token_scanner import clear_stale_tokens
            clear_stale_tokens(new_jwe=valid_jwe, new_jwt=result.get("jwt"))
            logger.info("[Cycle #%d] New JWE Token saved, stale tokens cleared (%d chars)", self._cycle, len(valid_jwe))

        # Sync to config.yaml
        self._sync_config_yaml(valid_jwe)

        return result

    def _trigger_copilot_refresh(self):
        """Trigger Excel Copilot to refresh Token (sends Alt+Y + message)"""
        try:
            from excel_trigger import trigger_excel_token_refresh
            logger.info("[Cycle #%d] Triggering Excel Copilot refresh...", self._cycle)
            trigger_excel_token_refresh(wait_seconds=5)
            logger.info("[Cycle #%d] Excel Copilot refresh triggered, awaiting next cycle scan", self._cycle)
        except Exception as e:
            logger.warning("[Cycle #%d] Failed to trigger Excel refresh: %s", self._cycle, e)

    def _sync_config_yaml(self, jwe_token: str):
        """Sync JWE Token to proxy/config.yaml"""
        try:
            import yaml
            config_path = SCRIPT_DIR / "config.yaml"
            if not config_path.exists():
                return
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("augloop", {})["bearer_token"] = jwe_token
            config_path.write_text(
                yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("Failed to sync config.yaml: %s", e)

    # ── Auto Initialize Copilot (One-click launch) ──

    def _bring_to_foreground(self) -> bool:
        """Bring Excel window to foreground (keybd_event requires foreground window)"""
        if not self._hwnd:
            return False
        # If minimized, restore first
        if user32.IsIconic(self._hwnd):
            user32.ShowWindow(self._hwnd, SW_RESTORE)
            time.sleep(0.5)
        # Alt key trick to bypass foreground restrictions
        user32.PostMessageW(self._hwnd, WM_SYSKEYDOWN, VK_MENU, 0)
        user32.PostMessageW(self._hwnd, WM_SYSKEYUP, VK_MENU, 0)
        time.sleep(0.1)
        result = user32.SetForegroundWindow(self._hwnd)
        if not result:
            user32.ShowWindow(self._hwnd, SW_SHOW)
            time.sleep(0.3)
            user32.SetForegroundWindow(self._hwnd)
        time.sleep(0.5)
        return user32.GetForegroundWindow() == self._hwnd

    def _send_alt_y(self):
        """Send Alt+Y to open Copilot sidebar (requires window in foreground)"""
        logger.info("  Sending Alt+Y...")
        user32.keybd_event(VK_MENU, 0, 0, None)       # Alt down
        time.sleep(0.05)
        user32.keybd_event(VK_Y, 0, 0, None)          # Y down
        time.sleep(0.05)
        user32.keybd_event(VK_Y, 0, 0x0002, None)    # Y up
        time.sleep(0.05)
        user32.keybd_event(VK_MENU, 0, 0x0002, None)  # Alt up
        time.sleep(0.5)

    def _send_text(self, text: str):
        """Type text using SendInput (Unicode)"""
        for ch in text:
            inputs = (INPUT * 2)()
            inputs[0].type = INPUT_KEYBOARD
            inputs[0].ki.wScan = ord(ch)
            inputs[0].ki.dwFlags = KEYEVENTF_UNICODE
            inputs[1].type = INPUT_KEYBOARD
            inputs[1].ki.wScan = ord(ch)
            inputs[1].ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            user32.SendInput(2, ctypes.cast(inputs, ctypes.POINTER(INPUT)), ctypes.sizeof(INPUT))
            time.sleep(0.03)

    def _send_enter(self):
        """Send Enter key"""
        user32.keybd_event(VK_RETURN, 0, 0, None)
        time.sleep(0.05)
        user32.keybd_event(VK_RETURN, 0, 0x0002, None)

    def auto_init_copilot(self, message: str = "hello", wait: int = 20) -> bool:
        """Automatically initialize Copilot: Open sidebar -> send message -> wait for Token generation

        Called before hiding Excel; requires Excel in foreground.

        Args:
            message: Message sent to Copilot
            wait: Wait time in seconds after sending (allows Copilot to generate JWE Token)

        Returns:
            True if Token is detected
        """
        logger.info("=" * 40)
        logger.info("Auto-initializing Copilot...")
        logger.info("=" * 40)

        # 1. Bring to foreground
        logger.info("[1/4] Bringing Excel to foreground...")
        if not self._bring_to_foreground():
            logger.warning("Unable to bring Excel to foreground, attempting direct input...")
        time.sleep(1)

        # 2. Send Alt+Y to open Copilot
        logger.info("[2/4] Opening Copilot sidebar (Alt+Y)...")
        self._send_alt_y()
        time.sleep(5)  # Wait for Copilot panel to load

        # 3. Send message
        logger.info("[3/4] Sending message '%s'...", message)
        self._send_text(message)
        time.sleep(0.5)
        self._send_enter()

        # 4. Wait for Token generation
        logger.info("[4/4] Waiting %d seconds for Copilot to generate JWE Token...", wait)
        time.sleep(wait)

        # Scan memory
        from memory_token_scanner import scan_process_memory
        tokens = scan_process_memory(self._excel_pid, find_all=True)
        jwe_list = tokens.get("jwe_list", [])
        jwt_list = tokens.get("jwt_list", [])
        logger.info("  Scan results: %d JWE, %d JWT", len(jwe_list), len(jwt_list))

        if jwe_list:
            logger.info("  ✓ JWE Token detected (len=%d)", len(jwe_list[-1]))
            return True
        else:
            logger.warning("  ✗ JWE Token not detected, may require more time")
            return False

    # ── Legacy compatibility ──

    def scan(self, find_all: bool = True) -> dict:
        """Scan our Excel memory (scans self._excel_pid only)"""
        from memory_token_scanner import scan_process_memory
        if self._excel_pid:
            return scan_process_memory(self._excel_pid, find_all=find_all)
        return {}

    def scan_and_save(self) -> dict:
        return self.scan_validate_and_save()

    # ── Lifecycle ──

    def start(self, wait_for_user: bool = True, auto_init: bool = False):
        """Start: Dispatch new Excel -> (optional) auto/manual open Copilot -> hide

        Args:
            wait_for_user: If True, waits for user to open Copilot and press Enter to confirm
            auto_init: If True, automatically sends Alt+Y + message to initialize Copilot
        """
        if self._started:
            logger.warning("Already started")
            return self

        logger.info("=" * 60)
        logger.info("Excel Background Runner starting (Isolation Mode: only manages our instance)")
        logger.info("  Hide Mode: %s", self.hide_mode)
        logger.info("  Auto Close: %s", self.auto_close)
        logger.info("  Scan Interval: %.0f sec (%.1f min)", self.scan_interval, self.scan_interval / 60)
        logger.info("  Token Validation: %s", self.validate)
        logger.info("  Auto Initialization: %s", auto_init)
        logger.info("=" * 60)

        # Start brand new Excel instance
        self._connect_excel()

        if auto_init:
            # Fully automated: script opens Copilot and sends message
            self.auto_init_copilot(message="hello", wait=20)
        elif wait_for_user:
            # Manual prompt
            print()
            print("─" * 60)
            print("🔑 New Excel instance opened (PID=%d)" % self._excel_pid)
            print("   This Excel was started by the script and will not affect your other Excel windows.")
            print()
            print("   Please perform the following in this new Excel window:")
            print("   1. Press Alt+Y to open the Copilot sidebar")
            print("   2. Send a message (e.g. 'hello') to initialize AugLoop")
            print("   3. Once Copilot replies, return here and press Enter to continue")
            print("─" * 60)
            try:
                input("\n>>> Press Enter to hide Excel and start scanning <<< ")
            except (EOFError, KeyboardInterrupt):
                print("\nSkipping wait, hiding immediately...")

        # Hide window
        self._hide_excel()

        # Set global hidden flags (checked by augloop_ws_client)
        os.environ["EXCEL_HIDDEN"] = "1"
        os.environ["EXCEL_BG_PID"] = str(self._excel_pid)
        global EXCEL_HIDDEN_FLAG
        EXCEL_HIDDEN_FLAG = True

        if self.auto_close:
            atexit.register(self.stop)
            signal.signal(signal.SIGINT, self._signal_handler)
            try:
                signal.signal(signal.SIGTERM, self._signal_handler)
            except (ValueError, AttributeError):
                pass

        self._started = True
        logger.info("Excel (PID=%d) is now hidden and running in background", self._excel_pid)
        logger.info("Copilot WebView2 will continue refreshing JWE Token automatically (~4min cycle)")
        logger.info("Scanning, validating, and saving new Tokens every %.0f minutes", self.scan_interval / 60)
        logger.info("On exit, only PID=%d Excel will be closed", self._excel_pid)
        return self

    def _signal_handler(self, signum, frame):
        logger.info("Received exit signal (%s), cleaning up...", signum)
        self.stop()
        sys.exit(0)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True

        logger.info("Stopping Excel Background Runner (closing PID=%d only)...", self._excel_pid)

        # Clear hidden flags
        os.environ["EXCEL_HIDDEN"] = "0"
        os.environ.pop("EXCEL_BG_PID", None)
        global EXCEL_HIDDEN_FLAG
        EXCEL_HIDDEN_FLAG = False

        # Restore window (our instance only)
        if self._excel is not None:
            try:
                self._excel.Visible = True
            except Exception:
                pass
        self._restore_excel()

        # Close Excel (our instance only)
        if self.auto_close:
            self._close_excel()

        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

        logger.info("Excel Background Runner stopped (%d cycles completed, PID=%d closed)",
                    self._cycle, self._excel_pid)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # ── Daemon Loop ──

    def run_forever(self, scan_callback=None):
        """Run continuously, scanning, validating, and saving Tokens on each cycle

        Args:
            scan_callback: Optional callback receiving scan results, returns True to stop loop
        """
        logger.info("Entering daemon loop (Press Ctrl+C to exit)...")
        logger.info("Initial scan executes immediately; subsequent scans every %.0f minutes", self.scan_interval / 60)

        try:
            while True:
                # Scan + validate + save
                result = self.scan_validate_and_save()

                if result and result.get("jwe"):
                    logger.info("[Cycle #%d] ✓ Valid JWE Token ready (len=%d)",
                                self._cycle, len(result["jwe"]))
                else:
                    logger.warning("[Cycle #%d] ✗ No valid Token acquired, awaiting next cycle", self._cycle)

                # Next scan time
                next_time = time.time() + self.scan_interval
                next_str = time.strftime("%H:%M:%S", time.localtime(next_time))
                logger.info("[Cycle #%d] Next scan at: %s (in ~%.0f minutes)",
                            self._cycle, next_str, self.scan_interval / 60)

                # Call callback
                if scan_callback and scan_callback(result):
                    logger.info("Callback returned True, stopping loop")
                    break

                # Wait for next scan
                time.sleep(self.scan_interval)

        except KeyboardInterrupt:
            logger.info("Interrupted by user (Ctrl+C)")
        finally:
            self.stop()


# ── Standalone CLI Entrypoint ────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Excel Background Silent Runner + Periodic Token Refresh")
    parser.add_argument("--mode", choices=["hide", "minimize", "offscreen"],
                        default="hide", help="Window hide mode (default: hide)")
    parser.add_argument("--interval", type=float, default=3000.0,
                        help="Scan interval in seconds (default: 3000 = 50 minutes)")
    parser.add_argument("--once", action="store_true",
                        help="Scan once and exit")
    parser.add_argument("--no-auto-close", action="store_true",
                        help="Do not close Excel on exit")
    parser.add_argument("--no-validate", action="store_true",
                        help="Skip Token validation (save newest directly)")
    parser.add_argument("--no-wait", action="store_true",
                        help="Do not wait for user, hide Excel immediately (non-interactive)")
    parser.add_argument("--auto-init", action="store_true",
                        help="Automatically open Copilot and send message to initialize")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = ExcelBackgroundRunner(
        hide_mode=args.mode,
        auto_close=not args.no_auto_close,
        scan_interval=args.interval,
        validate=not args.no_validate,
    )

    if args.once:
        runner.start(wait_for_user=not args.no_wait and not args.auto_init, auto_init=args.auto_init)
        result = runner.scan_validate_and_save()
        runner.stop()
        if result and result.get("jwe"):
            jwe = result["jwe"]
            print(f"\n[★] JWE Token: {jwe[:80]}...")
            print(f"    Length: {len(jwe)}")
            print(f"    Valid: {result.get('jwe_valid', False)}")
        else:
            print("\n[X] No valid Token found")
    else:
        runner.start(wait_for_user=not args.no_wait and not args.auto_init, auto_init=args.auto_init)
        runner.run_forever()


if __name__ == "__main__":
    main()
