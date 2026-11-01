"""M1 月營收收集：OpenAPI 每日增量、MOPS 歷史回補、更正值覆寫、缺漏檢查。

單位：千元。來源與欄位說明見 docs/data_sources.md。
用法：
    python -m collectors.revenue daily            # 上市 + 上櫃最新一月
    python -m collectors.revenue backfill [--months 60]
    python -m collectors.revenue recheck          # 重抓近 3 個月歷史頁，偵測更正
    python -m collectors.revenue check            # 缺漏檢查
"""
import re
import sys
import argparse
import logging
from datetime import date

from core.config import load_config
from core import db
from core.http import PoliteSession

log = logging.getLogger("revenue")

FIELDS = ("revenue", "revenue_prev_month", "revenue_last_year", "mom", "yoy",
          "cum_revenue", "cum_last_year", "cum_yoy")


def to_num(s, integer=False):
    s = (s or "").replace(",", "").replace("&nbsp;", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return int(round(v)) if integer else v


def roc_to_ym(s):
    """民國 YYYMM 轉西元 YYYY-MM，例如 11509 -> 2026-09"""
    s = str(s).strip()
    return f"{int(s[:-2]) + 1911}-{s[-2:]}"


def estimate_published(ym, day=10):
    y, m = int(ym[:4]), int(ym[5:])
    y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return date(y, m, day).isoformat()


# ---------- 解析 ----------

def parse_openapi(rows, market):
    """TWSE / TPEx OpenAPI JSON -> (companies, revenue records)"""
    companies, recs = [], []
    for x in rows:
        sid = str(x["公司代號"]).strip()
        ym = roc_to_ym(x["資料年月"])
        companies.append({"stock_id": sid, "name": x.get("公司名稱", "").strip(), "market": market,
                          "industry": (x.get("產業別") or "").strip() or None})
        recs.append({
            "stock_id": sid, "ym": ym,
            "revenue": to_num(x.get("營業收入-當月營收"), True),
            "revenue_prev_month": to_num(x.get("營業收入-上月營收"), True),
            "revenue_last_year": to_num(x.get("營業收入-去年當月營收"), True),
            "mom": to_num(x.get("營業收入-上月比較增減(%)")),
            "yoy": to_num(x.get("營業收入-去年同月增減(%)")),
            "cum_revenue": to_num(x.get("累計營業收入-當月累計營收"), True),
            "cum_last_year": to_num(x.get("累計營業收入-去年累計營收"), True),
            "cum_yoy": to_num(x.get("累計營業收入-前期比較增減(%)")),
            "note": (x.get("備註") or "").strip() or None,
        })
    return companies, recs


def parse_mops_html(html, ym, market):
    """MOPS 歷史彙總頁 -> (companies, revenue records)。每列 11 欄。"""
    companies, recs, seen = [], [], set()
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, flags=re.S | re.I):
        c = [re.sub(r"<[^>]+>", "", x).replace("&nbsp;", "").strip()
             for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, flags=re.S | re.I)]
        if len(c) != 11 or not re.fullmatch(r"\d{4,6}[A-Z]?", c[0]) or c[0] in seen:
            continue
        seen.add(c[0])
        companies.append({"stock_id": c[0], "name": c[1], "market": market, "industry": None})
        recs.append({
            "stock_id": c[0], "ym": ym,
            "revenue": to_num(c[2], True), "revenue_prev_month": to_num(c[3], True),
            "revenue_last_year": to_num(c[4], True), "mom": to_num(c[5]), "yoy": to_num(c[6]),
            "cum_revenue": to_num(c[7], True), "cum_last_year": to_num(c[8], True), "cum_yoy": to_num(c[9]),
            "note": c[10] or None,
        })
    return companies, recs


# ---------- 寫入 ----------

def upsert_companies(conn, companies):
    now = db.now_iso()
    conn.executemany(
        """INSERT INTO companies (stock_id, name, market, industry, updated_at) VALUES (?,?,?,?,?)
           ON CONFLICT(stock_id) DO UPDATE SET name=excluded.name, market=excluded.market,
             industry=COALESCE(excluded.industry, companies.industry), updated_at=excluded.updated_at""",
        [(c["stock_id"], c["name"], c["market"], c["industry"], now) for c in companies])


def upsert_revenue(conn, recs, source, published_at, is_estimate):
    """寫入月營收。回傳 (新增, 更正, 不變)。

    - 新資料：直接插入。
    - 數值不同：舊值寫入 revenue_history 後覆寫（更正值）。
    - 數值相同：不動，除非原本的公告日是估計值且這次抓到的日期更早（實際值）。
    """
    now = db.now_iso()
    added = revised = same = 0
    for r in recs:
        old = conn.execute("SELECT * FROM monthly_revenue WHERE stock_id=? AND ym=?",
                           (r["stock_id"], r["ym"])).fetchone()
        if old is None:
            conn.execute(
                """INSERT INTO monthly_revenue (stock_id, ym, revenue, revenue_prev_month, revenue_last_year,
                   mom, yoy, cum_revenue, cum_last_year, cum_yoy, note, published_at, published_is_estimate,
                   source, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (r["stock_id"], r["ym"], *[r[f] for f in FIELDS], r["note"], published_at,
                 int(is_estimate), source, now))
            added += 1
            continue
        if any(old[f] != r[f] for f in ("revenue", "revenue_last_year", "cum_revenue")):
            conn.execute(
                "INSERT INTO revenue_history (stock_id, ym, revenue, revenue_last_year, source, superseded_at) "
                "VALUES (?,?,?,?,?,?)", (old["stock_id"], old["ym"], old["revenue"], old["revenue_last_year"],
                                         old["source"], now))
            conn.execute(
                """UPDATE monthly_revenue SET revenue=?, revenue_prev_month=?, revenue_last_year=?, mom=?, yoy=?,
                   cum_revenue=?, cum_last_year=?, cum_yoy=?, note=?, source=?, updated_at=?
                   WHERE stock_id=? AND ym=?""",
                (*[r[f] for f in FIELDS], r["note"], source, now, r["stock_id"], r["ym"]))
            revised += 1
        else:
            if old["published_is_estimate"] and not is_estimate and published_at < old["published_at"]:
                conn.execute("UPDATE monthly_revenue SET published_at=?, published_is_estimate=0 "
                             "WHERE stock_id=? AND ym=?", (published_at, r["stock_id"], r["ym"]))
            same += 1
    return added, revised, same


# ---------- 抓取 ----------

def make_session(cfg, mops=False):
    f = cfg["fetch"]
    return PoliteSession(f["user_agent"], f["mops_interval_sec"] if mops else f["min_interval_sec"],
                         f["retries"], f["backoff_sec"], f["timeout_sec"])


def run_daily(conn, cfg):
    sess = make_session(cfg)
    today = date.today().isoformat()
    total = 0
    with db.job_run(conn, "revenue_daily") as info:
        for market, key in (("sii", "twse_revenue"), ("otc", "tpex_revenue")):
            rows = sess.get(cfg["sources"][key]).json()
            companies, recs = parse_openapi(rows, market)
            if not recs:
                raise RuntimeError(f"{market} OpenAPI 回傳空資料")
            upsert_companies(conn, companies)
            a, rv, s = upsert_revenue(conn, recs, f"{'twse' if market == 'sii' else 'tpex'}_openapi", today, False)
            log.info("%s %s：新增 %d、更正 %d、不變 %d", market, recs[0]["ym"], a, rv, s)
            total += a + rv
            conn.commit()
        info["rows"] = total


def months_back(n, today=None):
    """回傳最近 n 個已結束月份 (year, month)，由近到遠；本月營收尚未公告，從上月起算。"""
    t = today or date.today()
    y, m = t.year, t.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append((y, m))
    return out


def fetch_mops_month(sess, cfg, market, y, m):
    url = cfg["sources"]["mops_history"].format(market=market, roc_year=y - 1911, month=m)
    raw = sess.get(url).content
    try:
        html = raw.decode("big5hkscs")
    except UnicodeDecodeError:
        html = raw.decode("big5hkscs", errors="replace")
    return parse_mops_html(html, f"{y}-{m:02d}", market)


def run_history(conn, cfg, months, skip_existing=True, job="revenue_backfill"):
    sess = make_session(cfg, mops=True)
    day = cfg["published_at"]["estimate_day_of_next_month"]
    total = 0
    with db.job_run(conn, job) as info:
        for (y, m) in months_back(months):
            ym = f"{y}-{m:02d}"
            for market in ("sii", "otc"):
                if skip_existing:
                    n = conn.execute("SELECT COUNT(*) FROM monthly_revenue WHERE ym=? AND stock_id IN "
                                     "(SELECT stock_id FROM companies WHERE market=?)", (ym, market)).fetchone()[0]
                    if n > 500:
                        continue
                try:
                    companies, recs = fetch_mops_month(sess, cfg, market, y, m)
                except RuntimeError as e:
                    log.error("%s %s 抓取失敗：%s", market, ym, e)
                    continue
                if not recs:
                    log.error("%s %s 解析不到資料", market, ym)
                    continue
                upsert_companies(conn, companies)
                a, rv, s = upsert_revenue(conn, recs, "mops_html", estimate_published(ym, day), True)
                conn.commit()
                log.info("%s %s：新增 %d、更正 %d、不變 %d", market, ym, a, rv, s)
                total += a + rv
        info["rows"] = total


def check(conn):
    """缺漏檢查：各月各市場筆數；與前一個月相比減少超過 5% 者列出。"""
    rows = conn.execute(
        "SELECT r.ym, c.market, COUNT(*) n FROM monthly_revenue r JOIN companies c USING(stock_id) "
        "GROUP BY r.ym, c.market ORDER BY c.market, r.ym").fetchall()
    issues, prev = [], {}
    for r in rows:
        p = prev.get(r["market"])
        if p and r["n"] < p * 0.95:
            issues.append(f"{r['market']} {r['ym']} 只有 {r['n']} 筆（前月 {p}）")
        prev[r["market"]] = r["n"]
    return rows, issues


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "backfill", "recheck", "check"])
    ap.add_argument("--months", type=int, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    cfg = load_config()
    conn = db.connect()
    if args.cmd == "daily":
        run_daily(conn, cfg)
    elif args.cmd == "backfill":
        run_history(conn, cfg, args.months or cfg["fetch"]["history_months"])
    elif args.cmd == "recheck":
        run_history(conn, cfg, args.months or 3, skip_existing=False, job="revenue_recheck")
    else:
        rows, issues = check(conn)
        for r in rows:
            print(r["market"], r["ym"], r["n"])
        print("異常：", issues or "無")


if __name__ == "__main__":
    sys.exit(main())
