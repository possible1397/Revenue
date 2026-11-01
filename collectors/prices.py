"""每日股價與市值收集（yfinance）。

- close 為未還原收盤價，adj_close 為還原收盤價（算報酬用）。
- market_cap（千元）= close × 目前已發行股數 / 1000，是估算值；股數取自 TWSE / TPEx 公司基本資料 OpenAPI。
- 增量更新時往回重抓 14 天做比對；若重疊區 adj_close / close 比例與舊資料不同（發生除權息），整檔重抓覆蓋，
  避免新舊還原基準混雜（做法參考 stockfilter/data/fetcher.py，2026-11-01）。

用法：
    python -m collectors.prices shares            # 更新股數
    python -m collectors.prices run [--limit N]   # 全市場增量（無資料者回補 5 年）
"""
import sys
import time
import argparse
import logging
from datetime import date, datetime, timedelta

import pandas as pd

from core.config import load_config
from core import db
from core.http import PoliteSession

log = logging.getLogger("prices")

OVERLAP_DAYS = 14
RATIO_TOLERANCE = 0.005


def yf_ticker(stock_id, market):
    return f"{stock_id}.{'TW' if market == 'sii' else 'TWO'}"


def update_shares(conn, cfg):
    f = cfg["fetch"]
    sess = PoliteSession(f["user_agent"], f["min_interval_sec"], f["retries"], f["backoff_sec"], f["timeout_sec"])
    n = 0
    with db.job_run(conn, "shares_update") as info:
        for key, id_k, sh_k in (("twse_company", "公司代號", "已發行普通股數或TDR原股發行股數"),
                                ("tpex_company", "SecuritiesCompanyCode", "IssueShares")):
            rows = sess.get(cfg["sources"][key]).json()
            data = []
            for r in rows:
                try:
                    data.append((int(str(r[sh_k]).replace(",", "")), str(r[id_k]).strip()))
                except (KeyError, ValueError):
                    continue
            conn.executemany("UPDATE companies SET shares=? WHERE stock_id=?", data)
            n += len(data)
        conn.commit()
        info["rows"] = n
    return n


def _trim_incomplete_today(df, now=None):
    """台股 13:30 收盤；14:00 前抓到的當日資料是盤中快照，丟掉。"""
    if df is None or df.empty:
        return df
    now = now or datetime.now()
    if now.weekday() < 5 and now.hour < 14 and df.index[-1].date() == now.date():
        return df.iloc[:-1]
    return df


def yf_fetch(ticker, start):
    import yfinance as yf
    df = yf.Ticker(ticker).history(start=start, auto_adjust=False)
    if df is None or df.empty:
        return pd.DataFrame()
    df.index = pd.DatetimeIndex([d.date() for d in df.index])
    return df


def _rows(stock_id, df):
    out = []
    for d, r in df.iterrows():
        if pd.isna(r["Close"]):
            continue
        adj = r["Adj Close"] if "Adj Close" in df.columns and not pd.isna(r["Adj Close"]) else r["Close"]
        out.append((stock_id, d.strftime("%Y-%m-%d"), float(r["Close"]), float(adj),
                    int(r["Volume"]) if not pd.isna(r["Volume"]) else None))
    return out


def _save(conn, rows, replace_all_for=None):
    if replace_all_for:
        conn.execute("DELETE FROM prices WHERE stock_id=?", (replace_all_for,))
    conn.executemany(
        "INSERT INTO prices (stock_id, date, close, adj_close, volume) VALUES (?,?,?,?,?) "
        "ON CONFLICT(stock_id, date) DO UPDATE SET close=excluded.close, adj_close=excluded.adj_close, "
        "volume=excluded.volume", rows)


def _adjusted_changed(conn, stock_id, df):
    """重疊區內，新舊 adj_close/close 比例是否不同。"""
    for d, r in df.iterrows():
        old = conn.execute("SELECT close, adj_close FROM prices WHERE stock_id=? AND date=?",
                           (stock_id, d.strftime("%Y-%m-%d"))).fetchone()
        if old and old["close"] and old["adj_close"] and r["Close"]:
            new_ratio = (r["Adj Close"] if not pd.isna(r["Adj Close"]) else r["Close"]) / r["Close"]
            if abs(new_ratio / (old["adj_close"] / old["close"]) - 1) > RATIO_TOLERANCE:
                return True
    return False


def update_one(conn, stock_id, market, years=5, fetch=yf_fetch, now=None):
    """更新單一股票，回傳寫入列數。fetch 可注入以便測試。"""
    t = yf_ticker(stock_id, market)
    last = conn.execute("SELECT MAX(date) FROM prices WHERE stock_id=?", (stock_id,)).fetchone()[0]
    if last is None:
        start = (date.today() - timedelta(days=365 * years + 7)).isoformat()
        df = _trim_incomplete_today(fetch(t, start), now)
        if df is None or df.empty:
            # 首次抓取不該是空的：斷網時 yfinance 只印警告並回傳空表，必須當成失敗才會被計入、重試
            raise RuntimeError(f"{t} 首次抓取無資料（斷網或 Yahoo 無此代號）")
        rows = _rows(stock_id, df)
        _save(conn, rows)
        return len(rows)
    start = (date.fromisoformat(last) - timedelta(days=OVERLAP_DAYS)).isoformat()
    df = _trim_incomplete_today(fetch(t, start), now)
    if df is None or df.empty:
        return 0
    overlap = df[df.index <= pd.Timestamp(last)]
    if _adjusted_changed(conn, stock_id, overlap):
        full_start = (date.today() - timedelta(days=365 * years + 7)).isoformat()
        full = _trim_incomplete_today(fetch(t, full_start), now)
        if full is not None and not full.empty:
            rows = _rows(stock_id, full)
            _save(conn, rows, replace_all_for=stock_id)
            return len(rows)
    rows = _rows(stock_id, df)
    _save(conn, rows)
    return len(rows)


def refresh_market_cap(conn):
    conn.execute("UPDATE prices SET market_cap = close * (SELECT shares FROM companies c WHERE c.stock_id=prices.stock_id) / 1000.0 "
                 "WHERE close IS NOT NULL")
    conn.commit()


def active_companies(conn):
    """最近 3 個營收月內仍有公告的公司（已下市者 Yahoo 多半無資料，先略過）。"""
    return conn.execute(
        "SELECT stock_id, market FROM companies WHERE stock_id IN "
        "(SELECT DISTINCT stock_id FROM monthly_revenue WHERE ym >= (SELECT strftime('%Y-%m', MAX(ym) || '-01', '-2 months') "
        "FROM monthly_revenue)) AND stock_id GLOB '[1-9][0-9][0-9][0-9]' ORDER BY stock_id").fetchall()


def run(conn, cfg, limit=None, fetch=yf_fetch, sleep=None):
    comps = active_companies(conn)
    if limit:
        comps = comps[:limit]
    sleep = cfg["fetch"]["min_interval_sec"] if sleep is None else sleep
    failed = []
    total = 0
    with db.job_run(conn, "prices_daily") as info:
        for i, c in enumerate(comps, 1):
            try:
                total += update_one(conn, c["stock_id"], c["market"], fetch=fetch)
                conn.commit()
            except Exception as e:
                conn.rollback()
                failed.append(c["stock_id"])
                log.warning("%s 失敗：%r", c["stock_id"], e)
            if i % 100 == 0:
                log.info("進度 %d/%d，失敗 %d", i, len(comps), len(failed))
            time.sleep(sleep)
        refresh_market_cap(conn)
        info["rows"] = total
        if comps and len(failed) / len(comps) > 0.2:
            raise RuntimeError(f"失敗比例過高：{len(failed)}/{len(comps)}")
    if failed:
        log.warning("失敗清單（%d）：%s", len(failed), ",".join(failed[:50]))
    return total, failed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["shares", "run"])
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config()
    conn = db.connect()
    if args.cmd == "shares":
        print("更新股數：", update_shares(conn, cfg))
    else:
        total, failed = run(conn, cfg, limit=args.limit)
        print(f"寫入 {total} 列，失敗 {len(failed)} 檔")


if __name__ == "__main__":
    sys.exit(main())
