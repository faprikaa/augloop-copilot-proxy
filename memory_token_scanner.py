#!/usr/bin/env python3
"""
memory_token_scanner.py - 纯 Python 内存扫描 JWE Token (不依赖 Frida)

使用 Windows API (ctypes) 直接读取 Excel 进程内存，
扫描 JWE Token (eyJhbGciOiJkaXIi...) 和 JWT Token (eyJhbGciOiJSUzI1NiI...)。

原理:
  1. OpenProcess 打开 Excel 进程 (需要 PROCESS_VM_READ 权限)
  2. VirtualQueryEx 枚举可读内存区域
  3. ReadProcessMemory 读取内存内容
  4. 正则匹配 JWE/JWT Token

优势:
  - 不需要安装 Frida
  - 不需要 Excel 发送网络请求 (只要 Token 在内存中)
  - 速度快 (直接内存扫描，无脚本注入开销)
  - 可以后台自动运行

用法:
  python memory_token_scanner.py              # 扫描一次并输出 Token
  python memory_token_scanner.py --daemon     # 后台守护进程模式
  python memory_token_scanner.py --once       # 扫描一次后退出
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

# ── Windows API 常量 ────────────────────────────────────────────────────────

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x00001000
PAGE_READWRITE = 0x04
PAGE_READONLY = 0x02
PAGE_EXECUTE_READ = 0x20
PAGE_WRITECOPY = 0x08

# ── Windows API 结构体 ──────────────────────────────────────────────────────

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

# ── Windows API 函数 ────────────────────────────────────────────────────────

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

# ── Token 正则 ─────────────────────────────────────────────────────────────

# JWE Token: eyJhbGciOiJkaXIi... (alg=dir, JWE encrypted)
JWE_PATTERN = re.compile(rb'(eyJhbGciOiJkaXIi[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)')

# JWT Token (RS256): eyJhbGciOiJSUzI1NiI... (anonymousToken)
JWT_PATTERN = re.compile(rb'(eyJhbGciOiJSUzI1NiI[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)')

# 最小 Token 长度 (过滤短匹配)
MIN_TOKEN_LEN = 200


def find_excel_processes() -> list[int]:
    """查找所有 Excel 进程的 PID"""
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
                logger.info("找到 Excel 进程: PID=%d (%s)", pe.th32ProcessID, name)
            if not kernel32.Process32Next(snapshot, ctypes.byref(pe)):
                break

    kernel32.CloseHandle(snapshot)
    return pids


def scan_process_memory(pid: int, find_all: bool = False) -> dict:
    """
    扫描进程内存，查找 JWE 和 JWT Token

    Args:
        pid: 进程 ID
        find_all: 如果 True, 返回所有找到的唯一 Token 列表

    Returns:
        find_all=False: {"jwe": "token...", "jwt": "token..."} 或空 dict
        find_all=True:  {"jwe_list": ["t1","t2"], "jwt_list": ["t1"]} 或空 dict
    """
    process = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not process:
        err = ctypes.get_last_error()
        logger.error("OpenProcess 失败 (PID=%d, error=%d) - 可能需要管理员权限", pid, err)
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
        max_region_size = 64 * 1024 * 1024  # 跳过大于 64MB 的区域

        while address < max_addr:
            result = kernel32.VirtualQueryEx(
                process,
                ctypes.c_void_p(address),
                ctypes.byref(mbi),
                ctypes.sizeof(mbi)
            )

            if result == 0:
                break

            # BaseAddress 可能是 None (NULL)
            base_addr = mbi.BaseAddress
            if base_addr is None:
                # NULL 区域，跳过
                address += mbi.RegionSize if mbi.RegionSize else 0x1000
                continue

            # 只扫描已提交的可读内存
            if (mbi.State == MEM_COMMIT and
                mbi.Protect in (PAGE_READWRITE, PAGE_READONLY, PAGE_EXECUTE_READ, PAGE_WRITECOPY) and
                mbi.RegionSize <= max_region_size):

                region_size = mbi.RegionSize

                # 分块读取 (避免一次性分配大内存)
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
                            # 收集所有唯一 Token
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
                                    logger.info("[★] JWE Token 找到! (%d chars)", len(token))
                                    break

                        # 搜索 JWT Token
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
                                    logger.info("[★] JWT Token 找到! (%d chars)", len(token))
                                    break

                        # 如果不需要全部且两个都找到了，提前退出
                        if not find_all and results["jwe"] and results["jwt"]:
                            logger.info("两个 Token 都已找到!")
                            return results

                    offset += read_size

                region_count += 1

            # 移动到下一个区域
            next_addr = base_addr + mbi.RegionSize
            if next_addr <= address:
                break
            address = next_addr

        logger.info("扫描完成: %d 个区域, %.1f MB 内存", region_count, total_scanned / (1024*1024))
        if find_all:
            return {k: v for k, v in results.items() if v}
        return {k: v for k, v in results.items() if v}

    finally:
        kernel32.CloseHandle(process)


def scan_once(find_all: bool = False) -> dict:
    """扫描一次所有 Excel 进程"""
    pids = find_excel_processes()
    if not pids:
        logger.error("未找到 Excel 进程! 请先启动 Excel。")
        return {}

    for pid in pids:
        logger.info("正在扫描 Excel (PID=%d)...", pid)
        results = scan_process_memory(pid, find_all=find_all)
        if results:
            return results

    return {}


def validate_jwe_token(token: str) -> bool:
    """通过 AugLoop HealthCheck API 验证 JWE Token 是否有效"""
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
                logger.info("[✓] JWE Token 验证通过 (200 OK)")
                return True
            else:
                logger.debug("[✗] JWE Token 验证失败 (%d)", resp.status_code)
                return False
    except Exception as e:
        logger.debug("[✗] JWE Token 验证异常: %s", e)
        return False


def find_valid_jwe_token() -> str | None:
    """
    扫描所有 JWE Token 并找到当前有效的那个

    内存中可能有多个旧的 JWE Token, 只有最新的是有效的。
    通过 HealthCheck API 验证找到有效的 Token。
    """
    logger.info("扫描所有 JWE Token 并验证...")
    results = scan_once(find_all=True)

    jwe_list = results.get("jwe_list", [])
    jwt_list = results.get("jwt_list", [])

    if not jwe_list and not jwt_list:
        logger.warning("未找到任何 Token")
        return None

    logger.info("找到 %d 个 JWE Token, %d 个 JWT Token", len(jwe_list), len(jwt_list))

    # 验证每个 JWE Token, 优先验证最后找到的 (通常是最新分配的内存)
    valid_jwe = None
    for i, token in enumerate(reversed(jwe_list)):
        idx = len(jwe_list) - i
        logger.info("验证 JWE Token #%d (%d chars)...", idx, len(token))
        if validate_jwe_token(token):
            valid_jwe = token
            break

    if not valid_jwe and jwe_list:
        # 如果都不通过 HealthCheck, 使用最后一个 (可能是缓存问题)
        valid_jwe = jwe_list[-1]
        logger.warning("所有 JWE Token 验证失败, 使用最后一个 (可能已过期)")

    return valid_jwe


def clear_stale_tokens(new_jwe: str | None = None, new_jwt: str | None = None):
    """清除旧的失效 Token, 只保留新的有效 Token

    Args:
        new_jwe: 新的有效 JWE Token (None 则只清空旧文件)
        new_jwt: 新的有效 JWT Token
    """
    token_file = Path(__file__).parent / ".augloop_token"
    config_path = Path(__file__).parent / "config.yaml"

    # 1. 覆写 .augloop_token (只保留新的 JWE, 清除旧的)
    if new_jwe:
        token_file.write_text(new_jwe, encoding="utf-8")
        logger.info("[清除] .augloop_token 已覆写为新 Token (len=%d), 旧 Token 已清除", len(new_jwe))
    elif token_file.exists():
        token_file.write_text("", encoding="utf-8")
        logger.info("[清除] .augloop_token 已清空 (无有效 Token)")

    # 2. 更新 config.yaml (只保留新的 bearer_token + auth_token)
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
            logger.info("[清除] config.yaml 已更新, 旧 Token 引用已替换")
    except Exception as e:
        logger.warning("[清除] 更新 config.yaml 失败: %s", e)

    # 3. 设置缓存重置标志 (augloop_ws_client 会检测)
    import os
    os.environ["JWE_CACHE_RESET"] = "1"
    logger.info("[清除] JWE 缓存重置标志已设置 (下次 WebSocket 连接将强制刷新)")



def save_tokens(tokens: dict, token_file: Path | None = None):
    """保存 Token 到文件"""
    if token_file is None:
        token_file = Path(__file__).parent / ".augloop_token"

    if tokens.get("jwe"):
        token_file.write_text(tokens["jwe"], encoding="utf-8")
        logger.info("[OK] JWE Token 已保存到 %s (%d chars)", token_file, len(tokens["jwe"]))

    if tokens.get("jwt"):
        # JWT 保存到 config
        import yaml
        config_path = Path(__file__).parent / "config.yaml"
        if config_path.exists():
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("augloop", {})["auth_token"] = tokens["jwt"]
            config_path.write_text(yaml.dump(cfg, allow_unicode=True, default_flow_style=False), encoding="utf-8")
            logger.info("[OK] JWT Token 已保存到 config.yaml (%d chars)", len(tokens["jwt"]))


def daemon_mode(interval: int = 3000):
    """守护进程模式: 定期扫描并更新 Token"""
    logger.info("=== 内存 Token 扫描守护进程 ===")
    logger.info("扫描间隔: %d 秒", interval)
    logger.info("按 Ctrl+C 退出\n")

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
                    logger.info("[刷新] JWE Token 已更新 (%d chars)", len(last_jwe))
                if tokens.get("jwt") and tokens["jwt"] != last_jwt:
                    last_jwt = tokens["jwt"]
                    changed = True
                    logger.info("[刷新] JWT Token 已更新 (%d chars)", len(last_jwt))
                if changed:
                    save_tokens(tokens)
            else:
                logger.warning("未找到 Token")

        except KeyboardInterrupt:
            logger.info("\n退出守护进程")
            break
        except Exception as e:
            logger.error("扫描异常: %s", e)

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="纯 Python 内存 Token 扫描器 (不依赖 Frida)")
    parser.add_argument("--once", action="store_true", help="扫描一次后退出")
    parser.add_argument("--daemon", action="store_true", help="守护进程模式")
    parser.add_argument("--interval", type=int, default=3000, help="守护进程扫描间隔 (秒)")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--save", action="store_true", help="保存到文件")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.daemon:
        daemon_mode(args.interval)
        return

    # 单次扫描
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
            print("\n[X] 未找到 Token")

    if args.save and tokens:
        save_tokens(tokens)


if __name__ == "__main__":
    main()
