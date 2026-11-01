"""每日排程入口（由 Windows 工作排程器呼叫，也可手動執行）。

順序：重訊（只有當天資料，最優先）-> 月營收增量 -> 營收更正檢查（週日）-> 股數更新（週一）-> 股價增量 -> 月度指標 -> 篩選。
各步驟獨立：某一步失敗會記錄並繼續後面的步驟，最後以非 0 結束碼回報。
每步驟本身都可重複執行，電腦關機漏跑的日子下次開機補跑即可（股價與營收皆從資料庫最後狀態接續）。

用法：
    python -m jobs.daily               # 全部
    python -m jobs.daily --only metrics screen
    python -m jobs.daily --force-weekly  # 不論星期幾，也跑週任務
"""
import argparse
import logging
import os
import sys
import traceback
from datetime import date, datetime

from core import db
from core.config import ROOT, load_config

LOCK = ROOT / "data" / "daily.lock"
log = logging.getLogger("daily")


def _events(cfg, conn):
    from collectors import events
    events.run_daily(conn, cfg)


def _revenue(cfg, conn):
    from collectors import revenue
    revenue.run_daily(conn, cfg)


def _recheck(cfg, conn):
    from collectors import revenue
    revenue.run_history(conn, cfg, 3, skip_existing=False, job="revenue_recheck")


def _shares(cfg, conn):
    from collectors import prices
    prices.update_shares(conn, cfg)


def _prices(cfg, conn):
    from collectors import prices
    total, failed = prices.run(conn, cfg)
    if failed:
        log.warning("股價失敗 %d 檔（下次執行會重試）", len(failed))


def _metrics(cfg, conn):
    from analysis import metrics
    metrics.run(conn, cfg)


def _screen(cfg, conn):
    from analysis import screen
    screen.run(conn, cfg)


def steps(today: date, force_weekly=False):
    wd = today.weekday()  # 週一 = 0
    out = [("events", _events), ("revenue", _revenue)]
    if force_weekly or wd == 6:
        out.append(("recheck", _recheck))
    if force_weekly or wd == 0:
        out.append(("shares", _shares))
    out += [("prices", _prices), ("metrics", _metrics), ("screen", _screen)]
    return out


def _acquire_lock():
    if LOCK.exists():
        age = datetime.now().timestamp() - LOCK.stat().st_mtime
        if age < 6 * 3600:   # 6 小時內的鎖視為仍在執行
            return False
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()))
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", help="只跑指定步驟")
    ap.add_argument("--force-weekly", action="store_true")
    args = ap.parse_args(argv)

    logdir = ROOT / "logs"
    logdir.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(logdir / f"daily_{date.today():%Y%m%d}.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])
    if not _acquire_lock():
        log.error("已有另一個每日排程在執行（%s），結束", LOCK)
        return 2
    cfg, failed = load_config(), []
    try:
        conn = db.connect()
        for name, fn in steps(date.today(), args.force_weekly):
            if args.only and name not in args.only:
                continue
            log.info("開始：%s", name)
            try:
                fn(cfg, conn)
                log.info("完成：%s", name)
            except Exception:
                conn.rollback()
                failed.append(name)
                log.error("失敗：%s\n%s", name, traceback.format_exc())
    finally:
        LOCK.unlink(missing_ok=True)
    if failed:
        log.error("以下步驟失敗：%s", ", ".join(failed))
        return 1
    log.info("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
