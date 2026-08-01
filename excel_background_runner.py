#!/usr/bin/env python3
"""
excel_background_runner.py - Excel 后台静默运行 + Token 定时刷新 + 退出自动关闭

🔑 隔离原则: 只管理自己启动的 Excel 实例, 绝不影响用户的其他 Excel
  - 使用 Dispatch 创建全新的 Excel 进程 (不连接已有实例)
  - 只扫描自己 PID 的内存 (不影响其他 Excel 进程)
  - 只关闭自己启动的 Excel (Quit 或 TerminateProcess 指定 PID)

工作原理:
  1. Dispatch 启动全新 Excel 实例 (自己的进程, 自己的 PID)
  2. 用户在新 Excel 中打开 Copilot 并发送一条消息 (初始化 AugLoop)
  3. 隐藏 Excel 窗口 (SW_HIDE) — Copilot WebView2 在隐藏时继续运行
  4. Excel 自动刷新 JWE Token (~4分钟周期), 无需用户操作
  5. 每 N 分钟 (默认 50 分钟) 扫描 [自己的 PID] 内存, 验证 Token, 保存
  6. 脚本退出时 (Ctrl+C / atexit / signal) 只关闭自己启动的 Excel

用法:
  # 持续后台运行, 每 50 分钟刷新一次 Token (默认)
  python excel_background_runner.py

  # 自定义刷新间隔 (例如 30 分钟)
  python excel_background_runner.py --interval 1800

  # 扫描一次后退出
  python excel_background_runner.py --once

  # 非交互模式 (不等待用户, 直接隐藏, 适合 Copilot 已初始化的情况)
  python excel_background_runner.py --no-wait

  # 作为模块导入:
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

# keybd_event (用于在前台窗口模拟键盘)
user32.keybd_event.argtypes = [ctypes.c_ushort, ctypes.c_ushort, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
user32.keybd_event.restype = None

# SendInput (用于输入文字)
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

# Win32 常量
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

# ShowWindow 命令
SW_HIDE = 0
SW_SHOWMINNOACTIVE = 7
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOW = 5

# 全局标志: Excel 是否被隐藏 (augloop_ws_client 会检查此标志)
EXCEL_HIDDEN_FLAG = os.environ.get("EXCEL_HIDDEN", "0") == "1"


class ExcelBackgroundRunner:
    """Excel 后台静默运行管理器

    - 隐藏 Excel 窗口, Copilot WebView2 继续后台运行
    - 定期扫描内存, 验证并保存有效 JWE Token
    - 退出时自动关闭 Excel
    """

    XLMAIN_CLASS = "XLMAIN"

    def __init__(
        self,
        hide_mode: str = "hide",
        auto_close: bool = True,
        scan_interval: float = 3000.0,  # 默认 50 分钟
        save_token: bool = True,
        validate: bool = True,          # 是否验证 Token 有效性
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
        self._cycle = 0  # 扫描周期计数

    # ── 窗口查找 ──

    def _find_hwnd_for_pid(self, pid: int) -> int:
        """查找指定 PID 的 Excel 主窗口 (只找自己的, 不碰其他 Excel)"""
        found_hwnd = 0

        def enum_callback(hwnd, lparam):
            nonlocal found_hwnd
            # 检查窗口类名是否为 XLMAIN
            class_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
            if class_buf.value != self.XLMAIN_CLASS:
                return True
            # 检查窗口所属进程
            window_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
            if window_pid.value == pid:
                found_hwnd = hwnd
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                logger.info("找到自己的 Excel 窗口: HWND=%d, PID=%d, Title=%s",
                            hwnd, pid, title.value[:50])
            return True

        user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
        return found_hwnd

    # ── COM 连接 (永远 Dispatch 新实例, 绝不连接已有实例) ──

    def _connect_excel(self):
        """启动全新的 Excel 实例 (不影响用户已有的 Excel)"""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            logger.error("需要 pywin32: pip install pywin32")
            raise

        # 🔑 记录已有的 Excel PID (避免误连用户的 Excel)
        from memory_token_scanner import find_excel_processes
        existing_pids = set(find_excel_processes())
        if existing_pids:
            logger.info("检测到已有 Excel 进程 (不影响它们): PID=%s", existing_pids)

        # 🔑 永远 Dispatch 新实例, 绝不 GetActiveObject 连接已有实例
        logger.info("启动全新 Excel 实例 (Dispatch)...")
        self._excel = win32com.client.Dispatch("Excel.Application")
        self._excel.Visible = True       # 先可见, 让用户打开 Copilot
        self._excel.DisplayAlerts = False
        try:
            self._excel.Workbooks.Add()
        except Exception:
            pass
        self._our_instance = True
        version = self._excel.Version
        logger.info("已启动新 Excel 实例 (版本 %s)", version)

        # 等待 Excel 完全初始化
        time.sleep(3)

        # 获取 HWND — 方法 1: 从 COM 对象获取 (带重试)
        self._hwnd = 0
        for attempt in range(10):
            try:
                self._hwnd = int(self._excel.Hwnd)
                if self._hwnd:
                    break
            except Exception:
                pass
            time.sleep(1)

        # 方法 2: 通过新出现的 PID 查找窗口
        if not self._hwnd:
            new_pids = set(find_excel_processes()) - existing_pids
            if new_pids:
                self._excel_pid = new_pids.pop()
                logger.info("通过 PID 差异找到新 Excel: PID=%d", self._excel_pid)
                self._hwnd = self._find_hwnd_for_pid(self._excel_pid)

        if not self._hwnd:
            raise RuntimeError("无法找到新 Excel 的窗口")

        # 获取 PID
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(self._hwnd, ctypes.byref(pid))
        self._excel_pid = pid.value
        if not self._excel_pid:
            raise RuntimeError("无法获取 Excel PID")

        logger.info("自己的 Excel: HWND=%d, PID=%d (只管理此实例)", self._hwnd, self._excel_pid)

        self._was_visible = True   # 我们启动的, 初始可见
        self._was_minimized = False

    # ── 窗口隐藏/恢复 ──

    def _hide_excel(self):
        if self.hide_mode == "hide":
            logger.info("隐藏 Excel 窗口 (SW_HIDE)...")
            user32.ShowWindow(self._hwnd, SW_HIDE)
        elif self.hide_mode == "minimize":
            logger.info("最小化 Excel 窗口 (SW_SHOWMINNOACTIVE)...")
            user32.ShowWindow(self._hwnd, SW_SHOWMINNOACTIVE)
        elif self.hide_mode == "offscreen":
            rect = wintypes.RECT()
            user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
            self._original_rect = (rect.left, rect.top, rect.right, rect.bottom)
            SWP_NOSIZE = 0x0001
            SWP_NOZORDER = 0x0004
            user32.SetWindowPos(self._hwnd, 0, -32000, -32000, 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER)
            logger.info("已将 Excel 窗口移到屏幕外")
        else:
            logger.warning("未知 hide_mode: %s", self.hide_mode)

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

    # ── 关闭 Excel ──

    def _close_excel(self):
        """关闭 Excel (只关自己启动的 PID, 不影响其他 Excel)"""
        # 方法 1: COM Quit (优雅关闭)
        if self._excel is not None:
            try:
                logger.info("正在关闭 Excel (Quit, PID=%d)...", self._excel_pid)
                self._excel.Quit()
                logger.info("Excel.Quit() 已调用")
            except Exception as e:
                logger.warning("Excel.Quit() 失败: %s", e)
            finally:
                # 释放 COM 对象
                try:
                    import win32com.client
                    self._excel = None
                except Exception:
                    pass

        # 等待进程退出 (最多 5 秒)
        if self._excel_pid:
            import subprocess
            for _ in range(5):
                time.sleep(1)
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {self._excel_pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True
                )
                if str(self._excel_pid) not in result.stdout:
                    logger.info("Excel 进程已退出 (PID=%d)", self._excel_pid)
                    return

            # 方法 2: Quit 后进程仍在, 强制终止 (只杀自己的 PID)
            logger.warning("Quit 后进程仍存活, 强制终止 PID=%d...", self._excel_pid)
            try:
                PROCESS_TERMINATE = 0x0001
                handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, self._excel_pid)
                if handle:
                    kernel32.TerminateProcess(handle, 0)
                    kernel32.CloseHandle(handle)
                    logger.info("Excel 进程已强制终止 (PID=%d)", self._excel_pid)
                else:
                    # 回退: taskkill
                    subprocess.run(["taskkill", "/F", "/PID", str(self._excel_pid)],
                                 capture_output=True)
                    logger.info("Excel 进程已通过 taskkill 终止 (PID=%d)", self._excel_pid)
            except Exception as e:
                logger.error("终止 Excel 进程失败: %s", e)

    # ── Token 验证 ──

    @staticmethod
    def _validate_jwe(token: str) -> bool:
        """通过 get_prompts API 验证 JWE Token 是否有效"""
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
            logger.debug("Token 验证异常: %s", e)
            return False

    # ── 内存扫描 (只扫自己的 PID, 不碰其他 Excel) ──

    def scan_validate_and_save(self) -> dict:
        """扫描 [自己的 PID] 内存 → 验证 → 保存有效 Token

        🔑 只扫描 self._excel_pid 的内存, 不影响其他 Excel 进程

        Returns:
            {"jwe": "valid_token", "jwt": "...", "jwe_valid": True} 或空 dict
        """
        from memory_token_scanner import scan_process_memory

        self._cycle += 1
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        logger.info("[周期 #%d @ %s] 扫描自己的 Excel (PID=%d)...", self._cycle, ts, self._excel_pid)

        # 🔑 只扫自己的 PID, 不用 scan_once() (那会扫所有 Excel)
        tokens = scan_process_memory(self._excel_pid, find_all=True)
        jwe_list = tokens.get("jwe_list", [])
        jwt_list = tokens.get("jwt_list", [])

        logger.info("[周期 #%d] 内存扫描: %d JWE, %d JWT", self._cycle, len(jwe_list), len(jwt_list))

        if not jwe_list:
            logger.warning("[周期 #%d] 内存中无 JWE Token (Excel Copilot 可能未运行)", self._cycle)
            return {}

        # 验证 JWE Token (从最新的开始)
        valid_jwe = None
        if self.validate:
            # 去重
            unique = list(dict.fromkeys(jwe_list))
            logger.info("[周期 #%d] 验证 %d 个唯一 JWE Token...", self._cycle, len(unique))

            for i, token in enumerate(reversed(unique)):
                idx = len(unique) - i
                ok = self._validate_jwe(token)
                if ok:
                    valid_jwe = token
                    logger.info("[周期 #%d] JWE #%d 验证通过 (len=%d) ✓", self._cycle, idx, len(token))
                    break
                else:
                    logger.info("[周期 #%d] JWE #%d 已过期 (len=%d) ✗", self._cycle, idx, len(token))

            if not valid_jwe:
                logger.warning("[周期 #%d] 所有 JWE Token 已过期! 触发 Excel 刷新 Token...", self._cycle)
                # 🔑 不保存过期 Token! 触发 Excel Copilot 刷新以生成新 Token
                self._trigger_copilot_refresh()
                # 返回空结果, 下次周期重新扫描
                result = {"jwe": "", "jwe_valid": False}
                if jwt_list:
                    result["jwt"] = jwt_list[-1]
                return result
        else:
            valid_jwe = jwe_list[-1]

        # 保存 (清除旧失效 Token, 只保留新的有效 Token)
        result = {"jwe": valid_jwe, "jwe_valid": True}
        if jwt_list:
            result["jwt"] = jwt_list[-1]

        if self.save_token and valid_jwe:
            from memory_token_scanner import clear_stale_tokens
            clear_stale_tokens(new_jwe=valid_jwe, new_jwt=result.get("jwt"))
            logger.info("[周期 #%d] 新 JWE Token 已保存, 旧失效 Token 已清除 (%d chars)", self._cycle, len(valid_jwe))

        # 同步到 config.yaml
        self._sync_config_yaml(valid_jwe)

        return result

    def _trigger_copilot_refresh(self):
        """触发 Excel Copilot 刷新 Token (发送 Alt+Y + 消息)"""
        try:
            from excel_trigger import trigger_excel_token_refresh
            logger.info("[周期 #%d] 触发 Excel Copilot 刷新...", self._cycle)
            trigger_excel_token_refresh(wait_seconds=5)
            logger.info("[周期 #%d] Excel Copilot 刷新已触发, 等待下次周期扫描", self._cycle)
        except Exception as e:
            logger.warning("[周期 #%d] 触发 Excel 刷新失败: %s", self._cycle, e)

    def _sync_config_yaml(self, jwe_token: str):
        """同步 JWE Token 到 proxy/config.yaml"""
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
            logger.debug("同步 config.yaml 失败: %s", e)

    # ── 自动初始化 Copilot (一键启动用) ──

    def _bring_to_foreground(self) -> bool:
        """将 Excel 窗口带到前台 (keybd_event 需要前台窗口)"""
        if not self._hwnd:
            return False
        # 如果最小化了, 先恢复
        if user32.IsIconic(self._hwnd):
            user32.ShowWindow(self._hwnd, SW_RESTORE)
            time.sleep(0.5)
        # Alt 键 trick 绕过前台限制
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
        """发送 Alt+Y 打开 Copilot 侧栏 (需要窗口在前台)"""
        logger.info("  发送 Alt+Y...")
        user32.keybd_event(VK_MENU, 0, 0, None)       # Alt down
        time.sleep(0.05)
        user32.keybd_event(VK_Y, 0, 0, None)          # Y down
        time.sleep(0.05)
        user32.keybd_event(VK_Y, 0, 0x0002, None)    # Y up
        time.sleep(0.05)
        user32.keybd_event(VK_MENU, 0, 0x0002, None)  # Alt up
        time.sleep(0.5)

    def _send_text(self, text: str):
        """用 SendInput 输入文本 (Unicode)"""
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
        """发送 Enter 键"""
        user32.keybd_event(VK_RETURN, 0, 0, None)
        time.sleep(0.05)
        user32.keybd_event(VK_RETURN, 0, 0x0002, None)

    def auto_init_copilot(self, message: str = "hello", wait: int = 20) -> bool:
        """自动初始化 Copilot: 打开侧栏 → 发消息 → 等待 Token 生成

        在隐藏 Excel 之前调用, 需要 Excel 在前台。

        Args:
            message: 发送给 Copilot 的消息
            wait: 发送后等待秒数 (让 Copilot 生成 JWE Token)

        Returns:
            True 如果扫描到 Token
        """
        logger.info("=" * 40)
        logger.info("自动初始化 Copilot...")
        logger.info("=" * 40)

        # 1. 带到前台
        logger.info("[1/4] 将 Excel 带到前台...")
        if not self._bring_to_foreground():
            logger.warning("无法将 Excel 带到前台, 尝试直接发送...")
        time.sleep(1)

        # 2. 发送 Alt+Y 打开 Copilot
        logger.info("[2/4] 打开 Copilot 侧栏 (Alt+Y)...")
        self._send_alt_y()
        time.sleep(5)  # 等 Copilot 面板加载

        # 3. 发送消息
        logger.info("[3/4] 发送消息 '%s'...", message)
        self._send_text(message)
        time.sleep(0.5)
        self._send_enter()

        # 4. 等待 Token 生成
        logger.info("[4/4] 等待 %d 秒让 Copilot 生成 JWE Token...", wait)
        time.sleep(wait)

        # 扫描内存
        from memory_token_scanner import scan_process_memory
        tokens = scan_process_memory(self._excel_pid, find_all=True)
        jwe_list = tokens.get("jwe_list", [])
        jwt_list = tokens.get("jwt_list", [])
        logger.info("  扫描结果: %d JWE, %d JWT", len(jwe_list), len(jwt_list))

        if jwe_list:
            logger.info("  ✓ 已检测到 JWE Token (len=%d)", len(jwe_list[-1]))
            return True
        else:
            logger.warning("  ✗ 未检测到 JWE Token, 可能需要更长时间")
            return False

    # ── 兼容旧接口 ──

    def scan(self, find_all: bool = True) -> dict:
        """扫描自己的 Excel 内存 (只扫 self._excel_pid)"""
        from memory_token_scanner import scan_process_memory
        if self._excel_pid:
            return scan_process_memory(self._excel_pid, find_all=find_all)
        return {}

    def scan_and_save(self) -> dict:
        return self.scan_validate_and_save()

    # ── 生命周期 ──

    def start(self, wait_for_user: bool = True, auto_init: bool = False):
        """启动: Dispatch 新 Excel → (可选) 自动/手动打开 Copilot → 隐藏

        Args:
            wait_for_user: True 时启动 Excel 后等待用户打开 Copilot 并按 Enter 确认
            auto_init: True 时自动发送 Alt+Y + 消息初始化 Copilot (无需用户操作)
        """
        if self._started:
            logger.warning("已经启动过了")
            return self

        logger.info("=" * 60)
        logger.info("Excel 后台运行器启动 (隔离模式: 只管理自己的实例)")
        logger.info("  隐藏模式: %s", self.hide_mode)
        logger.info("  自动关闭: %s", self.auto_close)
        logger.info("  扫描间隔: %.0f 秒 (%.1f 分钟)", self.scan_interval, self.scan_interval / 60)
        logger.info("  Token 验证: %s", self.validate)
        logger.info("  自动初始化: %s", auto_init)
        logger.info("=" * 60)

        # 启动全新 Excel 实例
        self._connect_excel()

        if auto_init:
            # 🤖 全自动: 脚本自动打开 Copilot 并发消息
            self.auto_init_copilot(message="hello", wait=20)
        elif wait_for_user:
            # 👤 手动: 提示用户操作
            print()
            print("─" * 60)
            print("🔑 新 Excel 已打开 (PID=%d)" % self._excel_pid)
            print("   这是脚本自己启动的 Excel, 不会影响你其他 Excel")
            print()
            print("   请在这个新 Excel 窗口中:")
            print("   1. 按 Alt+Y 打开 Copilot 侧栏")
            print("   2. 发送一条消息 (如 'hello') 初始化 AugLoop")
            print("   3. 等 Copilot 回复后, 回到这里按 Enter 继续")
            print("─" * 60)
            try:
                input("\n>>> 按 Enter 继续隐藏 Excel 并开始扫描 <<< ")
            except (EOFError, KeyboardInterrupt):
                print("\n跳过等待, 直接隐藏...")

        # 隐藏窗口
        self._hide_excel()

        # 设置全局隐藏标志 (augloop_ws_client 会检查)
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
        logger.info("Excel (PID=%d) 已在后台隐藏运行", self._excel_pid)
        logger.info("Copilot WebView2 将继续后台自动刷新 JWE Token (~4min 周期)")
        logger.info("每 %.0f 分钟扫描验证并保存新 Token", self.scan_interval / 60)
        logger.info("退出时只关闭 PID=%d 的 Excel", self._excel_pid)
        return self

    def _signal_handler(self, signum, frame):
        logger.info("收到退出信号 (%s), 正在清理...", signum)
        self.stop()
        sys.exit(0)

    def stop(self):
        if self._stopped:
            return
        self._stopped = True

        logger.info("正在停止 Excel 后台运行器 (只关闭 PID=%d)...", self._excel_pid)

        # 清除隐藏标志
        os.environ["EXCEL_HIDDEN"] = "0"
        os.environ.pop("EXCEL_BG_PID", None)
        global EXCEL_HIDDEN_FLAG
        EXCEL_HIDDEN_FLAG = False

        # 恢复窗口 (仅自己的)
        if self._excel is not None:
            try:
                self._excel.Visible = True
            except Exception:
                pass
        self._restore_excel()

        # 关闭 Excel (仅自己启动的实例)
        if self.auto_close:
            self._close_excel()

        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            pass

        logger.info("Excel 后台运行器已停止 (共 %d 个周期, PID=%d 已关闭)",
                    self._cycle, self._excel_pid)

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    # ── 守护循环 ──

    def run_forever(self, scan_callback=None):
        """持续运行, 每个周期扫描验证并保存 Token

        Args:
            scan_callback: 可选回调, 接收扫描结果, 返回 True 停止循环
        """
        logger.info("进入守护循环 (Ctrl+C 退出)...")
        logger.info("首次扫描立即执行, 后续每 %.0f 分钟一次", self.scan_interval / 60)

        try:
            while True:
                # 扫描 + 验证 + 保存
                result = self.scan_validate_and_save()

                if result and result.get("jwe"):
                    logger.info("[周期 #%d] ✓ 有效 JWE Token 已就绪 (len=%d)",
                                self._cycle, len(result["jwe"]))
                else:
                    logger.warning("[周期 #%d] ✗ 未获取到有效 Token, 等待下个周期", self._cycle)

                # 下次扫描时间
                next_time = time.time() + self.scan_interval
                next_str = time.strftime("%H:%M:%S", time.localtime(next_time))
                logger.info("[周期 #%d] 下次扫描: %s (约 %.0f 分钟后)",
                            self._cycle, next_str, self.scan_interval / 60)

                # 调用回调
                if scan_callback and scan_callback(result):
                    logger.info("回调返回 True, 停止循环")
                    break

                # 等待下次扫描
                time.sleep(self.scan_interval)

        except KeyboardInterrupt:
            logger.info("用户中断 (Ctrl+C)")
        finally:
            self.stop()


# ── 独立运行入口 ────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Excel 后台静默运行 + Token 定时刷新")
    parser.add_argument("--mode", choices=["hide", "minimize", "offscreen"],
                        default="hide", help="隐藏模式 (默认 hide)")
    parser.add_argument("--interval", type=float, default=3000.0,
                        help="扫描间隔秒数 (默认 3000 = 50 分钟)")
    parser.add_argument("--once", action="store_true",
                        help="扫描一次后退出")
    parser.add_argument("--no-auto-close", action="store_true",
                        help="退出时不关闭 Excel")
    parser.add_argument("--no-validate", action="store_true",
                        help="跳过 Token 验证 (直接保存最新的)")
    parser.add_argument("--no-wait", action="store_true",
                        help="不等待用户, 直接隐藏 Excel (非交互模式)")
    parser.add_argument("--auto-init", action="store_true",
                        help="自动打开 Copilot 并发消息初始化 (无需用户操作)")
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
            print("\n[X] 未找到有效 Token")
    else:
        runner.start(wait_for_user=not args.no_wait and not args.auto_init, auto_init=args.auto_init)
        runner.run_forever()


if __name__ == "__main__":
    main()
