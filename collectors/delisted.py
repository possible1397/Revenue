"""補抓已下市公司的月營收與股價，消除存活者偏誤（資料窗口 2021-11 起）。

為什麼需要：MOPS 歷史彙總頁只列「現在仍在上市櫃」的公司，Yahoo 對下市股也多半沒有資料，
所以主流程的資料庫不含下市公司，回測會偏樂觀。

來源（FinMind 免費版，單一公司查詢，間隔 >= 1 秒）：
  名單    TaiwanStockDelisting（FinMind）聯集 TWSE OpenAPI 終止上市公司；只取 4 碼股票
  營收    TaiwanStockMonthRevenue -> 自行計算 MoM / YoY / 累計；單位元 -> 千元
  股價    TaiwanStockPrice（未還原）+ TaiwanStockDividendResult 的除權息前後參考價 -> 自行還原
產業別與市場別取自 TaiwanStockInfo。

限制：還原只考慮除權息，未處理減資等其他事件；營收的公告日用估計值（次月 10 日）。
用法：python -m collectors.delisted
"""
import re
import sys
import logging
from datetime import date

import pandas as pd

from core.config import load_config
from core import db
from core.http import PoliteSession
from collectors.revenue import upsert_companies, upsert_revenue, estimate_published

log = logging.getLogger("delisted")
FM = "https://api.finmindtrade.com/api/v4/data"
WINDOW_START = "2021-11"


def fm_get(sess, dataset, **params):
    j = sess.get(FM, params={"dataset": dataset, **params}).json()
    if j.get("status") not in (200, None) and not j.get("data"):
        raise RuntimeError(f"FinMind {dataset} {params}: {j.get('msg')}")
    return j.get("data", [])


def roc_slash_to_iso(s):
    y, m, d = (int(x) for x in s.split("/"))
    return date(y + 1911, m, d).isoformat()


def delisted_list(sess, cfg):
    """回傳 {stock_id: (name, delisted_date)}，FinMind 與 TWSE 官方名單聯集。"""
    out = {}
    for x in fm_get(sess, "TaiwanStockDelisting"):
        if re.fullmatch(r"\d{4}", x["stock_id"]) and x["date"] >= WINDOW_START + "-01":
            out[x["stock_id"]] = (x["stock_name"], x["date"])
    twse = sess.get(cfg["sources"]["twse_delisted"]).json()
    only_twse = 0
    for x in twse:
        try:
            d = roc_slash_to_iso(x["DelistingDate"])
        except (ValueError, KeyError):
            continue
        if re.fullmatch(r"\d{4}", x["Code"]) and d >= WINDOW_START + "-01" and x["Code"] not in out:
            out[x["Code"]] = (x["Company"], d)
            only_twse += 1
    log.info("下市名單：%d 檔（其中僅 TWSE 官方名單有：%d）", len(out), only_twse)
    return out


def build_revenue_rows(sid, fm_rows):
    """FinMind 月營收 -> monthly_revenue 欄位（千元），只輸出 WINDOW_START 之後的月份。"""
    if not fm_rows:
        return []
    s = pd.Series({f"{r['revenue_year']}-{int(r['revenue_month']):02d}": r["revenue"] / 1000.0 for r in fm_rows})
    s.index = pd.PeriodIndex(s.index, freq="M")
    s = s.sort_index()
    full = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq="M"))
    prev, ly = full.shift(1), full.shift(12)
    ytd = full.groupby(full.index.year).cumsum(skipna=False)
    ytd_ly = ytd.shift(12)
    rows = []
    for p in full.index:
        ym = str(p)
        if ym < WINDOW_START or pd.isna(full[p]):
            continue
        pct = lambda a, b: (a / b - 1) * 100 if pd.notna(a) and pd.notna(b) and b else None
        num = lambda v: None if pd.isna(v) else int(round(v))
        rows.append({"stock_id": sid, "ym": ym, "revenue": num(full[p]), "revenue_prev_month": num(prev[p]),
                     "revenue_last_year": num(ly[p]), "mom": pct(full[p], prev[p]), "yoy": pct(full[p], ly[p]),
                     "cum_revenue": num(ytd[p]), "cum_last_year": num(ytd_ly[p]), "cum_yoy": pct(ytd[p], ytd_ly[p]),
                     "note": None})
    return rows


def adjust_prices(prices, events):
    """prices: [(date, close, volume)]；events: [(ex_date, before_price, after_price)]。
    回傳 [(date, close, adj_close, volume)]；adj = close × 之後所有除權息的 (after/before) 連乘。"""
    ev = sorted((d, a / b) for d, b, a in events if b and a)
    out = []
    for d, close, vol in prices:
        f = 1.0
        for ex, ratio in ev:
            if ex > d:
                f *= ratio
        out.append((d, close, close * f, vol))
    return out


def save_prices(conn, sid, rows):
    conn.execute("DELETE FROM prices WHERE stock_id=?", (sid,))
    conn.executemany("INSERT INTO prices (stock_id, date, close, adj_close, volume) VALUES (?,?,?,?,?)",
                     [(sid, d, c, a, v) for d, c, a, v in rows])


def run(conn, cfg):
    f = cfg["fetch"]
    sess = PoliteSession(f["user_agent"], f["min_interval_sec"], f["retries"], f["backoff_sec"], f["timeout_sec"])
    pub_day = cfg["published_at"]["estimate_day_of_next_month"]
    failed, n_rev, n_px = [], 0, 0
    with db.job_run(conn, "delisted_backfill") as info:
        lst = delisted_list(sess, cfg)
        meta = {x["stock_id"]: x for x in fm_get(sess, "TaiwanStockInfo")}
        for sid, (name, dd) in sorted(lst.items()):
            try:
                mi = meta.get(sid, {})
                market = "otc" if mi.get("type") == "tpex" else "sii"
                rev = build_revenue_rows(sid, fm_get(sess, "TaiwanStockMonthRevenue", data_id=sid,
                                                     start_date="2020-01-01"))
                px = [(x["date"], x["close"], x["Trading_Volume"]) for x in
                      fm_get(sess, "TaiwanStockPrice", data_id=sid, start_date="2021-10-01") if x["close"]]
                if not rev or not px:
                    raise RuntimeError(f"資料不足（營收 {len(rev)} 筆、股價 {len(px)} 筆）")
                ev = [(x["date"], x["before_price"], x["after_price"]) for x in
                      fm_get(sess, "TaiwanStockDividendResult", data_id=sid, start_date="2021-10-01")]
                upsert_companies(conn, [{"stock_id": sid, "name": name, "market": market,
                                         "industry": mi.get("industry_category")}])
                conn.execute("UPDATE companies SET delisted_date=? WHERE stock_id=?", (dd, sid))
                for r in rev:
                    upsert_revenue(conn, [r], "finmind", estimate_published(r["ym"], pub_day), True)
                save_prices(conn, sid, adjust_prices(px, ev))
                conn.commit()
                n_rev += len(rev)
                n_px += len(px)
            except Exception as e:
                conn.rollback()
                failed.append(sid)
                log.warning("%s %s 失敗：%r", sid, name, e)
        info["rows"] = n_rev + n_px
    log.info("完成：營收 %d 筆、股價 %d 筆，失敗 %d 檔 %s", n_rev, n_px, len(failed), failed)
    return lst, failed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run(db.connect(), load_config())
    sys.exit(0)
