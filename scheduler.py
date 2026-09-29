# -*- coding: utf-8 -*-
"""定时调度器：交易日自动运行 6 个会话（系统时区需为北京时间）
  - 08:55 盘前预热（更新历史日线缓存）
  - 10:00 早盘观察
  - 10:30 盘中观察
  - 11:30 午间复盘
  - 14:00 午后观察
  - 14:30 尾盘观察
  - 15:00 收盘报告
运行方式：python scheduler.py（或双击 start_scheduler.bat），保持窗口开启即可。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: E402

from config import LOG_DIR, SESSIONS  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402
from src.pipeline import run_session  # noqa: E402

log = setup_logger("scheduler", LOG_DIR)


def job(session):
    log.info("定时任务触发: %s %s", session, SESSIONS[session]["name"])
    try:
        run_session(session)
    except Exception:
        log.exception("会话 %s 运行失败", session)


def warmup():
    """盘前预热：确认交易日并提前缓存历史日线，让 10:00 会话秒出报告"""
    try:
        from src.data.market_data import MarketDataService
        svc = MarketDataService()
        if svc.is_trading_day():
            log.info("盘前预热：更新历史日线缓存…")
            svc.update_history()
        else:
            log.info("今日非交易日，跳过预热")
    except Exception:
        log.exception("盘前预热失败")


def main():
    sched = BlockingScheduler()
    sched.add_job(warmup, "cron", hour=8, minute=55, day_of_week="mon-fri",
                  id="warmup", misfire_grace_time=300, coalesce=True)
    for t in SESSIONS:
        h, m = t.split(":")
        sched.add_job(job, "cron", hour=int(h), minute=int(m), day_of_week="mon-fri",
                      args=[t], id=f"session_{h}{m}", misfire_grace_time=600, coalesce=True)
    log.info("A股短线交易助手调度器已启动：交易日 08:55预热 / 10:00 / 10:30 / 11:30 / 14:00 / 14:30 / 15:00")
    log.info("保持本窗口运行即可；按 Ctrl+C 退出。")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("调度器已停止")


if __name__ == "__main__":
    main()
