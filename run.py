#!/usr/bin/env python3
"""
run.py - 一键启动: Excel 后台 Token 收割 + OpenAI 兼容反代服务器

流程:
  1. Dispatch 启动自己的 Excel 实例 (不影响用户其他 Excel)
  2. 提示用户在新 Excel 中打开 Copilot 并发送消息
  3. 按 Enter 后隐藏 Excel, 进入后台
  4. 启动反代服务器 (http://127.0.0.1:8080)
  5. 每 50 分钟自动扫描 Excel 内存, 验证并更新 Token
  6. Ctrl+C 退出时自动关闭 Excel

用法:
  python run.py                    # 交互模式 (推荐)
  python run.py --no-wait          # 跳过等待, 直接隐藏 Excel
  python run.py --interval 1800    # 30 分钟刷新一次
  python run.py --port 8080       # 指定端口
"""

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

# 确保当前目录在 path 中
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("run")


def main():
    parser = argparse.ArgumentParser(description="Excel 后台 Token 收割 + 反代服务器")
    parser.add_argument("--mode", choices=["hide", "minimize", "offscreen"],
                        default="hide", help="Excel 隐藏模式 (默认 hide)")
    parser.add_argument("--interval", type=float, default=3000.0,
                        help="强制刷新间隔秒数 (默认 3000 = 50 分钟)")
    parser.add_argument("--no-wait", action="store_true",
                        help="不等待用户, 直接隐藏 Excel")
    parser.add_argument("--auto-init", action="store_true",
                        help="自动打开 Copilot 并发消息初始化 (无需用户操作)")
    parser.add_argument("--no-validate", action="store_true",
                        help="跳过 Token 验证")
    parser.add_argument("--port", type=int, default=8080,
                        help="反代服务器端口 (默认 8080)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="反代服务器绑定地址 (默认 127.0.0.1)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Excel Copilot 反代服务器 (后台 Token 收割版)")
    print("=" * 60)
    print()
    print(f"  隐藏模式:   {args.mode}")
    print(f"  刷新间隔:   {args.interval:.0f} 秒 ({args.interval / 60:.0f} 分钟)")
    print(f"  服务器:     http://{args.host}:{args.port}")
    print(f"  API 端点:   http://{args.host}:{args.port}/v1/chat/completions")
    print()

    # ── Step 1: 启动 Excel 后台运行器 ──
    logger.info("Step 1: 启动 Excel 后台 Token 收割器...")
    from excel_background_runner import ExcelBackgroundRunner

    runner = ExcelBackgroundRunner(
        hide_mode=args.mode,
        auto_close=True,
        scan_interval=args.interval,
        validate=not args.no_validate,
    )
    runner.start(wait_for_user=not args.no_wait and not args.auto_init, auto_init=args.auto_init)

    # ── Step 2: 启动后台扫描线程 ──
    logger.info("Step 2: 启动后台 Token 扫描线程...")

    scan_stop = threading.Event()

    def bg_scan_loop():
        """后台扫描循环: 每 interval 秒强制刷新一次 (触发 Excel + 扫描 + 验证)"""
        while not scan_stop.is_set():
            try:
                # 🔑 强制刷新: 先触发 Excel Copilot 生成新 Token
                logger.info("[强制刷新] 触发 Excel Copilot 刷新 Token...")
                try:
                    from excel_trigger import trigger_excel_token_refresh
                    trigger_excel_token_refresh(wait_seconds=10)
                except Exception as e:
                    logger.warning("[强制刷新] 触发 Excel 失败: %s", e)

                # 扫描 + 验证 + 保存
                result = runner.scan_validate_and_save()
                if result and result.get("jwe"):
                    logger.info("[强制刷新] JWE Token 已更新 (len=%d)", len(result["jwe"]))
                else:
                    logger.warning("[强制刷新] 未获取到有效 Token, 等待下个周期")
            except Exception as e:
                logger.error("[强制刷新] 异常: %s", e)

            # 等待下次扫描 (可被 stop 事件中断)
            scan_stop.wait(args.interval)

    scan_thread = threading.Thread(target=bg_scan_loop, daemon=True)
    scan_thread.start()
    logger.info("后台扫描线程已启动 (每 %.0f 分钟一次)", args.interval / 60)

    # ── Step 3: 启动反代服务器 ──
    logger.info("Step 3: 启动反代服务器...")

    # 立即扫描一次, 确保 Token 已加载
    logger.info("首次 Token 扫描...")
    runner.scan_validate_and_save()

    try:
        import uvicorn
        # server.py 中 app 对象
        from server import app

        logger.info("=" * 60)
        logger.info("反代服务器启动中: http://%s:%d", args.host, args.port)
        logger.info("Excel 在后台隐藏运行 (PID=%d)", runner._excel_pid)
        logger.info("Ctrl+C 退出 → 自动关闭 Excel", )
        logger.info("=" * 60)

        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )

    except ImportError:
        logger.error("需要 uvicorn: pip install uvicorn fastapi")
        logger.info(" falling back to direct server.py execution...")
        import subprocess
        subprocess.run([sys.executable, str(SCRIPT_DIR / "server.py")],
                       cwd=str(SCRIPT_DIR))

    except KeyboardInterrupt:
        logger.info("用户中断 (Ctrl+C)")

    finally:
        # ── Step 4: 清理 ──
        logger.info("正在清理...")
        scan_stop.set()
        scan_thread.join(timeout=5)
        runner.stop()
        logger.info("已退出, Excel 已关闭")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] Interrupted.")
        sys.exit(0)
