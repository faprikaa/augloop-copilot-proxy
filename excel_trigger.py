#!/usr/bin/env python3
"""
excel_trigger.py - 自动触发 Excel Copilot 刷新 JWE Token

当内存中的 JWE Token 过期时，通过 Windows UI Automation 触发
Excel Copilot 侧栏，使 Excel 自动向微软请求新的 JWE Token。

原理:
  1. 找到 Excel 主窗口
  2. 将 Excel 带到前台
  3. 发送 Copilot 快捷键 (Alt+Y) 打开 Copilot 侧栏
  4. 等待 Excel 刷新 Token (~3-5 秒)
  5. Token 刷新后可通过 memory_token_scanner 重新扫描

用法:
  from excel_trigger import trigger_excel_token_refresh
  trigger_excel_token_refresh()  # 触发刷新，等待新 Token
"""

import ctypes
import ctypes.wintypes as wintypes
import logging
import time
from pathlib import Path

logger = logging.getLogger("excel_trigger")

# ── Windows API ─────────────────────────────────────────────────────────────

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# SetForegroundWindow 需要
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL

user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_longlong

# FindWindowEx
user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND

# EnumWindows
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

# SW_RESTORE = 9, SW_SHOW = 5
SW_RESTORE = 9
SW_SHOW = 5

# WM_KEYDOWN = 0x0100, WM_KEYUP = 0x0101, WM_SYSKEYDOWN = 0x0104, WM_SYSKEYUP = 0x0105
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

# VK_MENU (Alt) = 0x12, VK_Y = 0x59
VK_MENU = 0x12
VK_Y = 0x59


def find_excel_window() -> int:
    """查找 Excel 主窗口句柄"""
    excel_hwnd = None

    def enum_callback(hwnd, lparam):
        nonlocal excel_hwnd
        # 检查窗口标题
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if "excel" in title.value.lower():
            # 检查窗口所属进程
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                excel_hwnd = hwnd
                logger.info("找到 Excel 窗口: HWND=%d, Title=%s, PID=%d",
                           hwnd, title.value[:50], pid.value)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return excel_hwnd or 0


def bring_to_foreground(hwnd: int) -> bool:
    """将窗口带到前台"""
    # 如果最小化了，先恢复
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.5)

    # 尝试设置前台
    fg_hwnd = user32.GetForegroundWindow()
    if fg_hwnd == hwnd:
        return True

    # 使用 ALT 键 trick 绕过前台限制
    user32.PostMessageW(hwnd, WM_SYSKEYDOWN, VK_MENU, 0)
    user32.PostMessageW(hwnd, WM_SYSKEYUP, VK_MENU, 0)
    time.sleep(0.1)

    result = user32.SetForegroundWindow(hwnd)
    if not result:
        logger.warning("SetForegroundWindow 失败, 尝试 ShowWindow")
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.5)
        user32.SetForegroundWindow(hwnd)

    time.sleep(0.5)
    return user32.GetForegroundWindow() == hwnd


def send_copilot_shortcut(hwnd: int):
    """发送 Copilot 快捷键 (Alt+Y) 打开 Copilot 侧栏"""
    logger.info("发送 Alt+Y 快捷键打开 Copilot...")

    # 方法 1: PostMessage (不阻塞, 但可能不工作如果窗口不在前台)
    user32.PostMessageW(hwnd, WM_SYSKEYDOWN, VK_MENU, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_SYSKEYDOWN, VK_Y, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_SYSKEYUP, VK_Y, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_SYSKEYUP, VK_MENU, 0)

    logger.info("快捷键已发送")


def trigger_excel_token_refresh(wait_seconds: int = 5) -> bool:
    """
    触发 Excel 刷新 JWE Token

    通过发送 Alt+Y 快捷键打开 Copilot 侧栏，
    使 Excel 连接 AugLoop 服务并刷新 Token。

    Args:
        wait_seconds: 发送快捷键后等待的秒数

    Returns:
        True 如果成功触发 (不保证 Token 已刷新)
    """
    hwnd = find_excel_window()
    if not hwnd:
        logger.error("未找到 Excel 窗口! 请先启动 Excel。")
        return False

    logger.info("找到 Excel 窗口 (HWND=%d), 尝试带到前台...", hwnd)

    # 带到前台
    fg_ok = bring_to_foreground(hwnd)
    if not fg_ok:
        logger.warning("无法将 Excel 带到前台, 尝试直接发送快捷键...")

    # 发送 Copilot 快捷键
    send_copilot_shortcut(hwnd)

    # 等待 Excel 刷新 Token
    logger.info("等待 %d 秒让 Excel 刷新 Token...", wait_seconds)
    time.sleep(wait_seconds)

    logger.info("[OK] 触发完成, Excel 应已刷新 Token")
    return True


def trigger_and_rescan(wait_seconds: int = 5) -> dict:
    """
    触发 Excel 刷新 Token 并重新扫描内存

    Returns:
        {"jwe": "token...", "jwt": "token..."} 或空 dict
    """
    from memory_token_scanner import scan_once

    # 先记录当前 Token
    old_tokens = scan_once(find_all=False)
    old_jwe = old_tokens.get("jwe", "")

    # 触发 Excel
    success = trigger_excel_token_refresh(wait_seconds)
    if not success:
        return {}

    # 重新扫描
    logger.info("重新扫描内存...")
    new_tokens = scan_once(find_all=False)

    new_jwe = new_tokens.get("jwe", "")
    if new_jwe and new_jwe != old_jwe:
        logger.info("[★] 检测到新 JWE Token! (%d chars)", len(new_jwe))
    else:
        logger.info("JWE Token 未变化 (可能 Excel 未刷新或刷新后 Token 相同)")

    return new_tokens


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=== Excel Token 刷新触发器 ===")
    print("将发送 Alt+Y 快捷键到 Excel 以触发 Copilot...")
    print()

    result = trigger_and_rescan(wait_seconds=5)
    if result:
        if result.get("jwe"):
            print(f"\n[★] JWE Token: {result['jwe'][:80]}...")
            print(f"    Length: {len(result['jwe'])}")
        if result.get("jwt"):
            print(f"\n[★] JWT Token: {result['jwt'][:80]}...")
            print(f"    Length: {len(result['jwt'])}")
    else:
        print("\n[X] 未找到 Token")
