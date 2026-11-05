"""P3 季財報收集：綜合損益表（上市＋上櫃，各產業別）。

來源
  歷史  MOPS 彙總頁 ajax_t163sb04（POST，一次一個市場一個季別，全部公司；只含現在仍在上市櫃者）
  每日  TWSE / TPEx OpenAPI t187ap06（各產業別，只有「最新一季」，公司陸續公告就陸續出現）
單位：千元（每股盈餘為元）。**數字是年初至該季的累計值**（Q2 = 上半年），單季值 = 本季累計 − 上季累計（在分析層計算）。
published_at：OpenAPI 為實際抓取日；MOPS 回補為法定申報期限（Q1 5/15、Q2 8/14、Q3 11/14、年報次年 3/31），
是「最晚公告日」的保守估計；之後若 OpenAPI 抓到更早的實際日期，取較早者。

用法：
    python -m collectors.financials daily
    python -m collectors.financials backfill [--since 2020Q1]
    python -m collectors.financials check
"""
import io
import re
import sys
import argparse
import logging
from datetime import date

import pandas as pd

from core.config import load_config
from core import db
from core.http import PoliteSession

log = logging.getLogger("financials")

# 標準項目 -> 各產業別可能的欄位名稱（欄位名稱已去除空白）
ALIASES = {
    "Revenue": ["營業收入", "收益", "收入"],
    "COGS": ["營業成本"],
    "GrossProfit": ["營業毛利（毛損）"],
    "OpEx": ["營業費用"],
    "OperatingIncome": ["營業利益（損失）", "營業利益"],
    "NonOperating": ["營業外收入及支出"],
    "PreTax": ["稅前淨利（淨損）", "繼續營業單位稅前純益（純損）", "繼續營業單位稅前淨利（淨損）"],
    "Tax": ["所得稅費用（利益）"],
    "NetIncome": ["本期淨利（淨損）", "本期稅後淨利（淨損）"],
    "NetIncomeParent": ["淨利（淨損）歸屬於母公司業主", "淨利（損）歸屬於母公司業主"],
    "EPS": ["基本每股盈餘（元）"],
}
COL2ITEM = {c: item for item, cols in ALIASES.items() for c in cols}

OPENAPI_KEYS = {
    "sii": ["t187ap06_L_ci", "t187ap06_L_basi", "t187ap06_L_bd", "t187ap06_L_fh", "t187ap06_L_ins", "t187ap06_L_mim"],
    "otc": ["mopsfin_t187ap06_O_ci", "mopsfin_t187ap06_O_basi", "mopsfin_t187ap06_O_bd",
            "mopsfin_t187ap06_O_fh", "mopsfin_t187ap06_O_ins", "mopsfin_t187ap06_O_mim"],
}
OPENAPI_BASE = {"sii": "https://openapi.twse.com.tw/v1/opendata/", "otc": "https://www.tpex.org.tw/openapi/v1/"}
MOPS_URL = "https://mopsov.twse.com.tw/mops/web/ajax_t163sb04"
DEADLINE = {1: (5, 15), 2: (8, 14), 3: (11, 14)}   # 第 4 季（年報）為次年 3/31


def to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return None if pd.isna(v) else float(v)
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "--", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def norm(c):
    return re.sub(r"\s+", "", str(c))


def period_of(roc_year, season):
    return f"{int(roc_year) + 1911}Q{int(season)}"


def deadline(period):
    y, q = int(period[:4]), int(period[-1])
    if q == 4:
        return date(y + 1, 3, 31).isoformat()
    m, d = DEADLINE[q]
    return date(y, m, d).isoformat()


def _records(sid, period, mapping):
    out = []
    for col, v in mapping.items():
        item = COL2ITEM.get(norm(col))
        val = to_num(v)
        if item and val is not None:
            out.append((sid, period, item, val))
    return out


def parse_openapi(rows):
    """OpenAPI JSON -> [(stock_id, period, item, value)]；TWSE 與 TPEx 的識別欄位名稱不同。"""
    out = []
    for x in rows:
        sid = str(x.get("公司代號") or x.get("SecuritiesCompanyCode") or "").strip()
        yr, se = x.get("年度") or x.get("Year"), x.get("季別") or x.get("Season")
        if not re.fullmatch(r"\d{4}", sid) or not yr or not se:
            continue
        out += _records(sid, period_of(yr, se), x)
    return out


def parse_mops_html(html, roc_year, season):
    """MOPS 彙總頁（每個產業別一張表）-> [(stock_id, period, item, value)]"""
    period = period_of(roc_year, season)
    out = []
    for t in pd.read_html(io.StringIO(html)):
        cols = [norm(c) for c in t.columns]
        if "公司代號" not in cols:
            continue
        t.columns = cols
        for _, r in t.iterrows():
            sid = str(r["公司代號"]).strip()
            if re.fullmatch(r"\d{4}\.0", sid):
                sid = sid[:-2]
            if re.fullmatch(r"\d{4}", sid):
                out += _records(sid, period, r.to_dict())
    return out


def upsert(conn, recs, source, published_at):
    """寫入；已存在則更新數值（更正），published_at 取較早者。回傳 (新增, 更正)。"""
    new = rev = 0
    for sid, period, item, val in recs:
        old = conn.execute("SELECT value FROM financials WHERE stock_id=? AND period=? AND type='income' AND item=?",
                           (sid, period, item)).fetchone()
        if old is None:
            conn.execute("INSERT INTO financials (stock_id, period, type, item, value, published_at, source) "
                         "VALUES (?,?,?,?,?,?,?)", (sid, period, "income", item, val, published_at, source))
            new += 1
        else:
            if abs(old[0] - val) > 1e-9:
                rev += 1
            conn.execute("UPDATE financials SET value=?, published_at=MIN(published_at, ?), source=? "
                         "WHERE stock_id=? AND period=? AND type='income' AND item=?",
                         (val, published_at, source, sid, period, item))
    return new, rev


def make_session(cfg, mops=False):
    f = cfg["fetch"]
    return PoliteSession(f["user_agent"], f["mops_interval_sec"] if mops else f["min_interval_sec"],
                         f["retries"], f["backoff_sec"], f["timeout_sec"])


def run_daily(conn, cfg):
    sess = make_session(cfg)
    today = date.today().isoformat()
    total = 0
    with db.job_run(conn, "financials_daily") as info:
        for market, keys in OPENAPI_KEYS.items():
            for k in keys:
                recs = parse_openapi(sess.get(OPENAPI_BASE[market] + k).json())
                new, rev = upsert(conn, recs, "twse_openapi" if market == "sii" else "tpex_openapi", today)
                conn.commit()
                log.info("%s：新增 %d、更正 %d", k, new, rev)
                total += new + rev
        info["rows"] = total


def periods_since(since, today=None):
    """從 since（如 2020Q1）到最近一個應已公告的季別。"""
    t = today or date.today()
    y, q = int(since[:4]), int(since[-1])
    out = []
    while True:
        if date.fromisoformat(deadline(f"{y}Q{q}")) > t:
            break
        out.append(f"{y}Q{q}")
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def fetch_mops(sess, market, period):
    y, q = int(period[:4]), int(period[-1])
    data = {"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": market,
            "year": str(y - 1911), "season": f"{q:02d}"}
    r = sess.post(MOPS_URL, data=data)
    r.encoding = "utf-8"
    return parse_mops_html(r.text, y - 1911, q)


def run_history(conn, cfg, since="2020Q1", refresh_last=2):
    sess = make_session(cfg, mops=True)
    total = 0
    with db.job_run(conn, "financials_backfill") as info:
        pers = periods_since(since)
        for i, period in enumerate(pers):
            for market in ("sii", "otc"):
                n = conn.execute("SELECT COUNT(DISTINCT f.stock_id) FROM financials f JOIN companies c USING(stock_id) "
                                 "WHERE f.period=? AND f.item='EPS' AND c.market=?", (period, market)).fetchone()[0]
                if n > 300 and i < len(pers) - refresh_last:
                    continue
                try:
                    recs = fetch_mops(sess, market, period)
                except RuntimeError as e:
                    log.error("%s %s 抓取失敗：%s", market, period, e)
                    continue
                if not recs:
                    log.error("%s %s 解析不到資料", market, period)
                    continue
                new, rev = upsert(conn, recs, "mops_html", deadline(period))
                conn.commit()
                log.info("%s %s：新增 %d、更正 %d", market, period, new, rev)
                total += new + rev
        info["rows"] = total


def check(conn):
    rows = conn.execute("SELECT f.period, c.market, COUNT(DISTINCT f.stock_id) FROM financials f "
                        "LEFT JOIN companies c USING(stock_id) WHERE f.item='EPS' GROUP BY 1,2 ORDER BY 1,2").fetchall()
    for p, m, n in rows:
        print(f"{p} {m}: {n} 家有 EPS")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "backfill", "check"])
    ap.add_argument("--since", default="2020Q1")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conn, cfg = db.connect(), load_config()
    if args.cmd == "daily":
        run_daily(conn, cfg)
    elif args.cmd == "backfill":
        run_history(conn, cfg, args.since)
    else:
        check(conn)


if __name__ == "__main__":
    sys.exit(main())
