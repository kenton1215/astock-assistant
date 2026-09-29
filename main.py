# -*- coding: utf-8 -*-
"""A股短线交易助手 — 单次运行入口
用法：
  python main.py --time 15:00     # 指定时段: 10:00 / 10:30 / 11:30 / 15:00
  python main.py --time auto      # 按当前时间自动选择时段（默认）
  python main.py --force          # 非交易日也强制运行（测试用）
  python main.py --no-llm         # 不调用大模型（纯规则模式）
  python main.py --no-news        # 不检索资讯
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import SESSIONS, LOG_DIR  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402
from src.pipeline import run_session  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="A股短线交易助手")
    ap.add_argument("--time", choices=list(SESSIONS) + ["auto"], default="auto",
                    help="会话时段（默认 auto 按当前时间自动选择）")
    ap.add_argument("--force", action="store_true", help="非交易日也强制运行")
    ap.add_argument("--no-llm", action="store_true", help="不调用大模型（规则模式）")
    ap.add_argument("--no-news", action="store_true", help="不检索资讯")
    args = ap.parse_args()

    if args.time == "auto":
        now = time.strftime("%H:%M")
        if now >= "15:00":
            session = "15:00"
        elif now >= "14:30":
            session = "14:30"
        elif now >= "14:00":
            session = "14:00"
        elif now >= "11:30":
            session = "11:30"
        elif now >= "10:30":
            session = "10:30"
        else:
            session = "10:00"
    else:
        session = args.time

    log = setup_logger("main", LOG_DIR)
    log.info("启动会话: %s(%s)", session, SESSIONS[session]["name"])
    try:
        report_path = run_session(session, force=args.force,
                                  use_llm=not args.no_llm, use_news=not args.no_news)
        if report_path:
            log.info("✅ 报告已生成: %s", report_path)
        else:
            log.info("今日非交易日或数据不可用，未生成报告")
    except KeyboardInterrupt:
        log.info("用户中断")
    except Exception as e:
        log.exception("运行失败: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
