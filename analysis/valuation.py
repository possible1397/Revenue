"""現況合理價：近四季（TTM）EPS × 該公司自己的歷史本益比。股價低於合理價的股票，之後有沒有比較好？

在看結果之前先寫下的設計與判斷標準（2026-11-07，之後不因結果調整）：

定義
  選股日 = 每月 11 日（與 price_test 相同，直接使用其存下的報酬資料 data/price_test_frame.pkl）。
  當日已公告的最近一季 L = 法定申報期限 <= 該日的最後一季（Q1 5/15、Q2 8/14、Q3 11/14、Q4 次年 3/31）。
  TTM EPS = 單季 EPS（由累計值相減）L-3 ~ L 四季合計，需四季齊全；TTM EPS <= 0 不估（沒有意義的本益比）。
  本益比 PE = 當日（或之前 10 天內最近）收盤價 / TTM EPS（皆為當時的實際數字，未事後還原）。
  歷史本益比 = 該公司「前面最多 36 個月」每月選股日的 PE，至少 18 個有效值；中位數 = 中位本益比，25 / 75 百分位 = 低 / 高。
  合理價（中）= TTM EPS × 中位本益比；低 / 高 = 用 25 / 75 百分位。
  折價幅度 discount = 現價 / 合理價(中) − 1（負 = 低於合理價）。
  報酬 = 相對同產業的超額報酬（同 price_test），3 個月為主、6 個月為輔。

假設（t 值為 Newey-West）
  V1  全市場：「−折價幅度」與 3 個月超額報酬的 Spearman IC 平均 > 0，t > 2。
  V2  只看月度成長股：同上。
  V3  每月依折價幅度分五組，最便宜組 − 最貴組的 3 個月超額報酬差 > 0，t > 2。
  V4  月度成長股中，折價 <= −20% 的組別，3 個月超額報酬 > 其餘成長股，t > 2。
  V1~V4 任一不成立就停下來與 Terry 討論，不把合理價放進儀表板當依據。

限制：存活者偏誤殘餘；歷史 PE 只有 2021 年之後；Yahoo 收盤價已調整股票分割，但配股造成的 EPS 股數變動未處理；
  景氣循環股在獲利高峰時 PE 很低，「低於合理價」可能是價值陷阱（本測試不處理，只在報告中列出）；未計成本與流動性。
"""
import sys

import numpy as np
import pandas as pd

from core import db
from core.config import ROOT
from analysis.eps_estimate import load_financials, load_prices, price_at, pid
from analysis.price_test import nw_tstat
from analysis.unpriced_study import rank_corr

ORIGIN_DAY = 11
MAX_HIST, MIN_HIST = 36, 18


def deadline_ok(year, q, d):
    m, dd = {1: (5, 15), 2: (8, 14), 3: (11, 14)}.get(q, (None, None))
    dl = f"{year + 1}-03-31" if q == 4 else f"{year}-{m:02d}-{dd:02d}"
    return dl <= d


def latest_filed(d):
    """日期 d 當天已公告的最近一季 (year, q)。"""
    y = int(d[:4])
    for yy in (y, y - 1):
        for q in (4, 3, 2, 1):
            if deadline_ok(yy, q, d):
                return yy, q


def month_dates(first="2021-04", last=None):
    last = last or pd.Timestamp.today().strftime("%Y-%m")
    return [f"{p}-{ORIGIN_DAY:02d}" for p in pd.period_range(first, last, freq="M").astype(str)]


def ttm_eps(single_eps, d):
    y, q = latest_filed(d)
    pL = pid(y, q)
    cols = [c for c in range(pL - 3, pL + 1)]
    if not set(cols) <= set(single_eps.columns):
        return pd.Series(dtype=float)
    return single_eps[cols].sum(axis=1, min_count=4)


def rolling_pe_stats(pe: pd.DataFrame):
    """pe: index = 月份順序的日期, columns = stock。回傳 (median, p25, p75, n)，只用「之前」的最多 36 個月。"""
    hist = pe.shift(1).rolling(MAX_HIST, min_periods=MIN_HIST)
    return (hist.median(), hist.quantile(0.25), hist.quantile(0.75), hist.count())


def build_panel(conn, first="2021-04"):
    _, single = load_financials(conn)
    prices = load_prices(conn)
    dates = month_dates(first)
    eps_rows, px_rows = {}, {}
    for d in dates:
        e = ttm_eps(single["EPS"], d)
        if e.empty:
            continue
        eps_rows[d] = e
        px_rows[d] = price_at(prices, d)
    eps = pd.DataFrame(eps_rows).T
    px = pd.DataFrame(px_rows).T.reindex(columns=eps.columns)
    pe = (px / eps.where(eps > 0))
    med, p25, p75, n = rolling_pe_stats(pe)
    ok = n >= MIN_HIST
    long = pd.DataFrame({"ttm_eps": eps.stack(), "ttm_eps_ly": eps.shift(12).stack(), "price": px.stack(), "pe": pe.stack(),
                         "pe_med": med.where(ok).stack(), "pe_p25": p25.where(ok).stack(),
                         "pe_p75": p75.where(ok).stack(), "n_hist": n.stack()}).dropna(subset=["pe", "pe_med"])
    long.index.names = ["as_of", "stock_id"]
    long = long.reset_index()
    long["fair_mid"] = long["ttm_eps"] * long["pe_med"]
    long["fair_low"] = long["ttm_eps"] * long["pe_p25"]
    long["fair_high"] = long["ttm_eps"] * long["pe_p75"]
    long["discount"] = long["price"] / long["fair_mid"] - 1
    return long


def ic_series(d, col, y):
    return pd.Series({m: rank_corr(-g[col], g[y]) for m, g in d.groupby("as_of")})


def report(d):
    out = ["# 現況合理價驗證（TTM EPS × 自身歷史本益比）", "",
           f"選股日 {d['as_of'].min()} ~ {d['as_of'].max()}，共 {d['as_of'].nunique()} 個月，平均每月 "
           f"{d.groupby('as_of').size().mean():.0f} 檔有合理價。設計與判斷標準見 `analysis/valuation.py` 開頭。", ""]
    g = d[d["is_growth"]]
    out += ["## V1 / V2：「−折價幅度」與後續超額報酬的 IC（Spearman）", ""]
    rows = []
    for name, x in (("全市場", d), ("月度成長股", g)):
        for h, lag in (("3個月", 2), ("6個月", 5)):
            s = ic_series(x, "discount", f"ex_{h}")
            rows.append({"樣本": name, "持有": h, "平均IC": round(s.mean(), 4), "t值(NW)": round(nw_tstat(s, lag), 2),
                         "IC>0的月份": f"{(s > 0).mean():.0%}", "月數": int(s.notna().sum())})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]

    out += ["## V3：依折價幅度分五組（1 = 最便宜）的平均超額報酬（%）", ""]
    d = d.copy()
    d["q"] = d.groupby("as_of")["discount"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False) + 1)
    rows = []
    for h, lag in (("3個月", 2), ("6個月", 5)):
        m = d.groupby(["as_of", "q"])[f"ex_{h}"].mean().unstack()
        spread = m[1] - m[5]
        rows.append({"持有": h, **{f"第{k}組": round(m[k].mean(), 2) for k in range(1, 6)},
                     "最便宜−最貴": round(spread.mean(), 2), "t值(NW)": round(nw_tstat(spread, lag), 2)})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]

    out += ["### 事後補充（看到 V3 結果後才加，不作為判定依據）：同樣分組，改用中位數與縮尾平均（1% / 99%）", ""]
    rows = []
    for h in ("3個月", "6個月"):
        c = f"ex_{h}"
        lo, hi = d[c].quantile(0.01), d[c].quantile(0.99)
        med = d.groupby("q")[c].median()
        win = d.assign(w=d[c].clip(lo, hi)).groupby("q")["w"].mean()
        rows.append({"持有": h, "統計": "中位數", **{f"第{k}組": round(med[k], 2) for k in range(1, 6)}})
        rows.append({"持有": h, "統計": "縮尾平均", **{f"第{k}組": round(win[k], 2) for k in range(1, 6)}})
    out += [pd.DataFrame(rows).to_markdown(index=False), "",
            f"（超額報酬最大值 {d['ex_3個月'].max():.0f}%，極端值會讓一般平均失真。）", ""]

    out += ["## V4：月度成長股中，折價 <= −20% 的組別 vs 其餘成長股（%）", ""]
    rows = []
    for h, lag in (("3個月", 2), ("6個月", 5)):
        a = g[g["discount"] <= -0.2].groupby("as_of")[f"ex_{h}"].mean()
        b = g[g["discount"] > -0.2].groupby("as_of")[f"ex_{h}"].mean()
        diff = (a - b).dropna()
        rows.append({"持有": h, "折價<=-20%": round(a.mean(), 2), "其餘成長股": round(b.mean(), 2),
                     "差": round(diff.mean(), 2), "t值(NW)": round(nw_tstat(diff, lag), 2),
                     "平均每月檔數": round(g[g["discount"] <= -0.2].groupby("as_of").size().mean(), 1)})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]

    out += ["## 補充：價值陷阱檢查（折價 <= −20% 的公司，TTM EPS 相對一年前）", ""]
    rows = []
    for name, x in (("全市場", d), ("月度成長股", g)):
        c = x[x["discount"] <= -0.2].dropna(subset=["ttm_eps_ly"])
        rows.append({"樣本": name, "筆數": len(c), "TTM EPS 較一年前下降": f"{(c['ttm_eps'] < c['ttm_eps_ly']).mean():.0%}",
                     "對照：所有公司下降": f"{(x['ttm_eps'] < x['ttm_eps_ly']).mean():.0%}"})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]
    return "\n".join(out)


def main():
    conn = db.connect()
    panel = build_panel(conn)
    panel.to_pickle(ROOT / "data" / "valuation_panel.pkl")
    frame = pd.read_pickle(ROOT / "data" / "price_test_frame.pkl")
    d = frame.merge(panel, on=["stock_id", "as_of"], how="inner")
    text = report(d)
    (ROOT / "docs" / "valuation_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
