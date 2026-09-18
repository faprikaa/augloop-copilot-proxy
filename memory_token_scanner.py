#!/usr/bin/env python3
"""
memory_token_scanner.py - Pure Python memory scanner for JWE Token (no Frida required)

Uses Windows API (ctypes) to directly read Excel process memory,
scanning for JWE Token (eyJhbGciOiJkaXIi...) and JWT Token (eyJhbGciOiJSUzI1NiI...).

Mechanism:
  1. OpenProcess opens Excel process (requires PROCESS_VM_READ permissions)
  2. VirtualQueryEx enumerates readable memory regions
  3. ReadProcessMemory reads memory contents
  4. Regex matches JWE/JWT Tokens

Advantages:
  - No need to install Frida
  - Excel doesn't need to send active network requests (as long as token is in memory)
  - Fast (direct memory scan, no script injection overhead)
  - Can run automatically in the background

Usage:
  python memory_token_scanner.py              # Scan once and output Token
  python memory_token_scanner.py --daemon     # Background daemon mode
  python memory_token_scanner.py --once       # Scan once and exit
"""

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import re
import sys
import time
from pathlib import Path

logger = logging.getLogger("scanner")

# ── Windows API Constants ───────────────────────────────────────────────────

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x00001000
PAGE_READWRITE = 0x04
PAGE_READONLY = 0x02
PAGE_EXECUTE_READ = 0x20
PAGE_WRITECOPY = 0x08

# ── Windows API Structures ──────────────────────────────────────────────────

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]

class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]

# ── Windows API Functions ───────────────────────────────────────────────────

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
psapi = ctypes.WinDLL('psapi', use_last_error=True)

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

kernel32.VirtualQueryEx.restype = ctypes.c_size_t
kernel32.VirtualQueryEx.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p,
    ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t
]

kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t)
]

kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

kernel32.Process32First.restype = wintypes.BOOL
kernel32.Process32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]

kernel32.Process32Next.restype = wintypes.BOOL
kernel32.Process32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32)]

# TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPPROCESS = 0x2

# ── Token Regex ─────────────────────────────────────────────────────────────

# JWE Token: eyJhbGciOiJkaXIi... (alg=dir, JWE encrypted)
JWE_PATTERN = re.compile(rb'(eyJhbGciOiJkaXIi[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)')

# JWT Token (RS256): eyJhbGciOiJSUzI1NiI... (anonymousToken)
JWT_PATTERN = re.compile(rb'(eyJhbGciOiJSUzI1NiI[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)')

# Minimum Token length (filter short matches)
MIN_TOKEN_LEN = 200


def find_excel_processes() -> list[int]:
    """Find PIDs of all Excel processes"""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wintypes.HANDLE(-1).value or snapshot == 0:
        return []

    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)

    pids = []
    if kernel32.Process32First(snapshot, ctypes.byref(pe)):
        while True:
            name = pe.szExeFile.decode('utf-8', errors='replace').lower()
            if 'excel' in name and name.endswith('.exe'):
                pids.append(pe.th32ProcessID)
                logger.info("Found Excel process: PID=%d (%s)", pe.th32ProcessID, name)
            if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                break

    kernel32.CloseHandle(snapshot)
    return pids


def scan_process_memory(pid: int, find_all: bool = False) -> dict:
    """
    Scan process memory for JWE and JWT Tokens

    Args:
        pid: Process ID
        find_all: If True, returns all unique tokens found

    Returns:
        find_all=False: {"jwe": "token...", "jwt": "token..."} or empty dict
        find_all=True:  {"jwe_list": ["t1","t2"], "jwt_list": ["t1"]} or empty dict
    """
    process = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not process:
        err = ctypes.get_last_error()
        logger.error("OpenProcess failed (PID=%d, error=%d) - Administrator privileges may be required", pid, err)
        return {}

    try:
        if find_all:
            results = {"jwe_list": [], "jwt_list": []}
            jwe_seen = set()
            jwt_seen = set()
        else:
            results = {"jwe": None, "jwt": None}

        mbi = MEMORY_BASIC_INFORMATION()
        address = 0
        max_addr = 0x7FFFFFFFFFFF  # 64-bit user space limit

        region_count = 0
        total_scanned = 0
        max_region_size = 64 * 1024 * 1024  # Skip regions larger than 64MB

        while address < max_addr:
            result = kernel32.VirtualQueryEx(
                process,
                ctypes.c_void_p(address),
                ctypes.byref(mbi),
                ctypes.sizeof(mbi)
            )

            if result == 0:
                break

            # BaseAddress might be None (NULL)
            base_addr = mbi.BaseAddress
            if base_addr is None:
                # NULL region, skip
                address += mbi.RegionSize if mbi.RegionSize else 0x1000
                continue

            # Only scan committed readable memory
            if (mbi.State == MEM_COMMIT and
                mbi.Protect in (PAGE_READWRITE, PAGE_READONLY, PAGE_EXECUTE_READ, PAGE_WRITECOPY) and
                mbi.RegionSize <= max_region_size):

                region_size = mbi.RegionSize

                # Chunked reading (avoids allocating large memory at once)
                chunk_size = min(region_size, 4 * 1024 * 1024)  # 4MB chunks
                offset = 0

                while offset < region_size:
                    read_size = min(chunk_size, region_size - offset)
                    buf = ctypes.create_string_buffer(read_size)
                    bytes_read = ctypes.c_size_t(0)

                    if kernel32.ReadProcessMemory(
                        process,
                        ctypes.c_void_p(base_addr + offset),
                        buf, read_size, ctypes.byref(bytes_read)
                    ):
                        data = buf.raw[:bytes_read.value]
                        total_scanned += len(data)

                        if find_all:
                            # Collect all unique Tokens
                            for m in JWE_PATTERN.finditer(data):
                                token = m.group(0).decode('ascii', errors='replace')
                                if len(token) > MIN_TOKEN_LEN and token not in jwe_seen:
                                    jwe_seen.add(token)
                                    results["jwe_list"].append(token)
                                    logger.info("[★] JWE Token #%d: %d chars", len(results["jwe_list"]), len(token))
                        elif not results["jwe"]:
                            for m in JWE_PATTERN.finditer(data):
                                token = m.group(0).decode('ascii', errors='replace')
                                if len(token) > MIN_TOKEN_LEN:
                                    results["jwe"] = token
                                    logger.info("[★] JWE Token found! (%d chars)", len(token))
                                    break

                        # Search for JWT Token
                        if find_all:
                            for m in JWT_PATTERN.finditer(data):
                                token = m.group(0).decode('ascii', errors='replace')
                                if len(token) > MIN_TOKEN_LEN and token not in jwt_seen:
                                    jwt_seen.add(token)
                                    results["jwt_list"].append(token)
                                    logger.info("[★] JWT Token #%d: %d chars", len(results["jwt_list"]), len(token))
                        elif not results["jwt"]:
                            for m in JWT_PATTERN.finditer(data):
                                token = m.group(0).decode('ascii', errors='replace')
                                if len(token) > MIN_TOKEN_LEN:
                                    results["jwt"] = token
                                    logger.info("[★] JWT Token found! (%d chars)", len(token))
                                    break

                        # If not find_all and both tokens found, exit early
                        if not find_all and results["jwe"] and results["jwt"]:
                            logger.info("Both tokens found!")
                            return results

                    offset += read_size

                region_count += 1

            # Move to next region
            next_addr = base_addr + mbi.RegionSize
            if next_addr <= address:
                break
            address = next_addr

        logger.info("Scan completed: %d regions, %.1f MB memory", region_count, total_scanned / (1024*1024))

        if find_all:
            return {k: v for k, v in results.items() if v}
        return {k: v for k, v in results.items() if v}

    finally:
        kernel32.CloseHandle(process)


def scan_once(find_all: bool = False) -> dict:
    """Scan all Excel processes once"""
    pids = find_excel_processes()
    if not pids:
        logger.error("Excel process not found! Please start Excel first.")
        return {}

    for pid in pids:
        logger.info("Scanning Excel (PID=%d)...", pid)
        results = scan_process_memory(pid, find_all=find_all)
        if results:
            return results

    return {}


def validate_jwe_token(token: str) -> bool:
    """Validate if JWE Token is valid via AugLoop HealthCheck API"""
    try:
        import httpx
        url = "https://augloop.svc.cloud.microsoft/"
        body = {
            "payload": {},
            "payloadSchema": {"category": 1, "schema": {"name": "HealthCheckRequest"}},
            "requestedSchema": {"category": 1, "schema": {"name": "HealthCheckResponse"}},
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=body, headers=headers)
            if resp.status_code == 200:
                logger.info("[✓] JWE Token verified successfully (200 OK)")
                return True
            else:
                logger.debug("[✗] JWE Token verification failed (%d)", resp.status_code)
                return False
    except Exception as e:
        logger.debug("[✗] JWE Token verification exception: %s", e)
        return False


def find_valid_jwe_token() -> str | None:
    """
    Scan all JWE Tokens and find the currently valid one

    There may be multiple stale JWE Tokens in memory; only the newest is valid.
    Validates token via HealthCheck API to find the active one.
    """
    logger.info("Scanning all JWE Tokens and validating...")
    results = scan_once(find_all=True)

    jwe_list = results.get("jwe_list", [])
    jwt_list = results.get("jwt_list", [])

    if not jwe_list and not jwt_list:
        logger.warning("No tokens found")
        return None

    logger.info("Found %d JWE Token(s), %d JWT Token(s)", len(jwe_list), len(jwt_list))

    # Validate each JWE Token, prioritizing the last found (usually newest memory allocation)
    valid_jwe = None
    for i, token in enumerate(reversed(jwe_list)):
        idx = len(jwe_list) - i
        logger.info("Validating JWE Token #%d (%d chars)...", idx, len(token))
        if validate_jwe_token(token):
            valid_jwe = token
            break

    if not valid_jwe and jwe_list:
        # If none pass HealthCheck, fallback to the last one (could be caching issue)
        valid_jwe = jwe_list[-1]
        logger.warning("All JWE Tokens failed validation, using the last one (may be expired)")

    return valid_jwe


def clear_stale_tokens(new_jwe: str | None = None, new_jwt: str | None = None):
    """Clear stale/invalid tokens, keeping only new valid tokens

    Args:
        new_jwe: New valid JWE Token (None clears old file)
        new_jwt: New valid JWT Token
    """
    token_file = Path(__file__).parent / ".augloop_token"
    config_path = Path(__file__).parent / "config.yaml"

    # 1. Overwrite .augloop_token (keep new JWE, clear old)
    if new_jwe:
        token_file.write_text(new_jwe, encoding="utf-8")
        logger.info("[CLEAR] .augloop_token overwritten with new Token (len=%d), stale token cleared", len(new_jwe))
    elif token_file.exists():
        token_file.write_text("", encoding="utf-8")
        logger.info("[CLEAR] .augloop_token cleared (no valid token)")

    # 2. Update config.yaml (keep new bearer_token + auth_token)
    try:
        import yaml
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            aug = cfg.setdefault("augloop", {})
            if new_jwe:
                aug["bearer_token"] = new_jwe
            if new_jwt:
                aug["auth_token"] = new_jwt
            config_path.write_text(
                yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            logger.info("[CLEAR] config.yaml updated, old token references replaced")
    except Exception as e:
        logger.warning("[CLEAR] Failed to update config.yaml: %s", e)

    # 3. Set cache reset flag (augloop_ws_client will detect it)
    import os
    os.environ["JWE_CACHE_RESET"] = "1"
    logger.info("[CLEAR] JWE cache reset flag set (next WebSocket connection will force refresh)")


def save_tokens(tokens: dict, token_file: Path | None = None):
    """Save tokens to file"""
    if token_file is None:
        token_file = Path(__file__).parent / ".augloop_token"

    if tokens.get("jwe"):
        token_file.write_text(tokens["jwe"], encoding="utf-8")
        logger.info("[OK] JWE Token saved to %s (%d chars)", token_file, len(tokens["jwe"]))

    if tokens.get("jwt"):
        # Save JWT to config
        import yaml
        config_path = Path(__file__).parent / "config.yaml"
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("augloop", {})["auth_token"] = tokens["jwt"]
            config_path.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            logger.info("[OK] JWT Token saved to config.yaml (%d chars)", len(tokens["jwt"]))


def daemon_mode(interval: int = 3000):
    """Daemon mode: periodically scan and update Token"""
    logger.info("=== Memory Token Scanner Daemon ===")
    logger.info("Scan interval: %d seconds", interval)
    logger.info("Press Ctrl+C to exit\n")

    last_jwe = None
    last_jwt = None

    while True:
        try:
            tokens = scan_once()
            if tokens:
                changed = False
                if tokens.get("jwe") and tokens["jwe"] != last_jwe:
                    last_jwe = tokens["jwe"]
                    changed = True
                    logger.info("[REFRESH] JWE Token updated (%d chars)", len(last_jwe))
                if tokens.get("jwt") and tokens["jwt"] != last_jwt:
                    last_jwt = tokens["jwt"]
                    changed = True
                    logger.info("[REFRESH] JWT Token updated (%d chars)", len(last_jwt))
                if changed:
                    save_tokens(tokens)
            else:
                logger.warning("No token found")

        except KeyboardInterrupt:
            logger.info("\nExiting daemon")
            break
        except Exception as e:
            logger.error("Scan exception: %s", e)

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Pure Python Memory Token Scanner (no Frida required)")
    parser.add_argument("--once", action="store_true", help="Scan once and exit")
    parser.add_argument("--daemon", action="store_true", help="Daemon mode")
    parser.add_argument("--interval", type=int, default=3000, help="Daemon scan interval (seconds)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--save", action="store_true", help="Save to file")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.daemon:
        daemon_mode(args.interval)
        return

    # Single scan
    tokens = scan_once()

    if args.json:
        output = {}
        if tokens.get("jwe"):
            output["jwe_token"] = tokens["jwe"][:50] + "..."
            output["jwe_length"] = len(tokens["jwe"])
        if tokens.get("jwt"):
            output["jwt_token"] = tokens["jwt"][:50] + "..."
            output["jwt_length"] = len(tokens["jwt"])
        print(json.dumps(output, indent=2))
    else:
        if tokens:
            if tokens.get("jwe"):
                print(f"\n[★] JWE Token: {tokens['jwe'][:80]}...")
                print(f"    Length: {len(tokens['jwe'])}")
            if tokens.get("jwt"):
                print(f"\n[★] JWT Token: {tokens['jwt'][:80]}...")
                print(f"    Length: {len(tokens['jwt'])}")
        else:
            print("\n[X] No token found")

    if args.save and tokens:
        save_tokens(tokens)


if __name__ == "__main__":
    main()
