#!/usr/bin/env python3
"""
excel_trigger.py - Automatically trigger Excel Copilot to refresh JWE Token

When the JWE Token in memory expires, this triggers the Excel Copilot sidebar
via Windows UI Automation, causing Excel to automatically request a new JWE Token from Microsoft.

Mechanism:
  1. Locate the Excel main window
  2. Bring Excel to the foreground
  3. Send Copilot shortcut (Alt+Y) to open Copilot sidebar
  4. Wait for Excel to refresh Token (~3-5 seconds)
  5. Rescan memory via memory_token_scanner once Token is refreshed

Usage:
  from excel_trigger import trigger_excel_token_refresh
  trigger_excel_token_refresh()  # Trigger refresh and wait for new Token
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

# SetForegroundWindow requirements
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
    """Find Excel main window handle (HWND)"""
    excel_hwnd = None

    def enum_callback(hwnd, lparam):
        nonlocal excel_hwnd
        # Check window title
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title, 256)
        if "excel" in title.value.lower():
            # Check window process ID
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                excel_hwnd = hwnd
                logger.info("Found Excel window: HWND=%d, Title=%s, PID=%d",
                           hwnd, title.value[:50], pid.value)
        return True

    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return excel_hwnd or 0


def bring_to_foreground(hwnd: int) -> bool:
    """Bring window to foreground"""
    # Restore if minimized
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.5)

    # Attempt to set foreground
    fg_hwnd = user32.GetForegroundWindow()
    if fg_hwnd == hwnd:
        return True

    # Use ALT key trick to bypass foreground lock restrictions
    user32.PostMessageW(hwnd, WM_SYSKEYDOWN, VK_MENU, 0)
    user32.PostMessageW(hwnd, WM_SYSKEYUP, VK_MENU, 0)
    time.sleep(0.1)

    result = user32.SetForegroundWindow(hwnd)
    if not result:
        logger.warning("SetForegroundWindow failed, attempting ShowWindow")
        user32.ShowWindow(hwnd, SW_SHOW)
        time.sleep(0.5)
        user32.SetForegroundWindow(hwnd)

    time.sleep(0.5)
    return user32.GetForegroundWindow() == hwnd


def send_copilot_shortcut(hwnd: int):
    """Send Copilot shortcut (Alt+Y) to open Copilot sidebar"""
    logger.info("Sending Alt+Y shortcut to open Copilot...")

    # Method 1: PostMessage (non-blocking)
    user32.PostMessageW(hwnd, WM_SYSKEYDOWN, VK_MENU, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_SYSKEYDOWN, VK_Y, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_SYSKEYUP, VK_Y, 0)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_SYSKEYUP, VK_MENU, 0)

    logger.info("Shortcut sent")


def trigger_excel_token_refresh(wait_seconds: int = 5) -> bool:
    """
    Trigger Excel to refresh JWE Token

    Sends Alt+Y shortcut to open Copilot sidebar,
    prompting Excel to connect to AugLoop service and refresh its Token.

    Args:
        wait_seconds: Seconds to wait after sending shortcut

    Returns:
        True if successfully triggered (does not guarantee Token is refreshed)
    """
    hwnd = find_excel_window()
    if not hwnd:
        logger.error("Excel window not found! Please start Excel first.")
        return False

    logger.info("Found Excel window (HWND=%d), attempting to bring to foreground...", hwnd)

    # Bring to foreground
    fg_ok = bring_to_foreground(hwnd)
    if not fg_ok:
        logger.warning("Could not bring Excel to foreground, attempting direct shortcut send...")

    # Send Copilot shortcut
    send_copilot_shortcut(hwnd)

    # Wait for Excel to refresh Token
    logger.info("Waiting %d seconds for Excel to refresh Token...", wait_seconds)
    time.sleep(wait_seconds)

    logger.info("[OK] Trigger complete, Excel should have refreshed Token")
    return True


def trigger_and_rescan(wait_seconds: int = 5) -> dict:
    """
    Trigger Excel to refresh Token and rescan memory

    Returns:
        {"jwe": "token...", "jwt": "token..."} or empty dict
    """
    from memory_token_scanner import scan_once

    # Record current Token
    old_tokens = scan_once(find_all=False)
    old_jwe = old_tokens.get("jwe", "")

    # Trigger Excel
    success = trigger_excel_token_refresh(wait_seconds)
    if not success:
        return {}

    # Rescan
    logger.info("Rescanning memory...")
    new_tokens = scan_once(find_all=False)

    new_jwe = new_tokens.get("jwe", "")
    if new_jwe and new_jwe != old_jwe:
        logger.info("[★] Detected new JWE Token! (%d chars)", len(new_jwe))
    else:
        logger.info("JWE Token unchanged (Excel may not have refreshed or token is identical)")

    return new_tokens


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    print("=== Excel Token Refresh Trigger ===")
    print("Sending Alt+Y shortcut to Excel to trigger Copilot...")
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
        print("\n[X] No Token found")
