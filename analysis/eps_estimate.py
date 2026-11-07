"""EPS 估算準不準？用月營收＋最近財報結構推「下一季」與「今年全年」EPS，與實際公布值比較。

在看結果之前先寫下的設計與判斷標準（2026-11-07，之後不因結果調整公式或門檻）：

預測時點（origin）= 財報法定申報期限日：5/15（Q1）、8/14（Q2）、11/14（Q3）、次年 3/31（Q4/年報）。
  該日已知：截至該季的財報、已公告的月營收（公告日 = 次月 10 日，故 origin 當月之前一個月以前的月份都已知）。
  股價與預測誤差的縮放用 origin 當天（或之前最近）的收盤價。
  起點：月營收資料只有 2021-11 之後，故 origin 從 2022-03-31 起。

估算公式（單季）  EPS_hat = ((Rev_hat × margin + nonop) × (1 − tax) × parent) / shares
  Rev_hat   = 去年同季營收 × (1 + g)；g = 已公告的最近 3 個月營收 / 去年同期 − 1，限制在 [−50%, +100%]
  margin    = 去年同季的營業利益率（主要版本 M2）；對照版本 M1 = 近四季合計營業利益率
  nonop     = 近四季單季「營業外收入及支出」的平均
  tax       = 近四季所得稅 / 稅前淨利，限制在 [0, 30%]（稅前淨利合計 <= 0 則 0）
  parent    = 近四季歸屬母公司淨利 / 本期淨利，限制在 [0, 1]（本期淨利合計 <= 0 則 1）
  shares    = 近四季歸屬母公司淨利合計 / 近四季 EPS 合計（隱含股數，近四季 EPS 合計絕對值 < 0.3 則不估）
  全年 EPS_hat = 已公告的累計 EPS + 其餘各季 EPS_hat（各季同一個 g，各自的去年同季營收與利益率）
  沒有營業利益欄位的公司（金融保險業）不納入。

對照組（若複雜估算贏不了這些，就不值得用）
  下一季：B1 = 去年同季 EPS；B2 = 最近一季 EPS
  全年  ：B1 = 去年全年 EPS；B2 = 今年累計 EPS × (去年全年 / 去年同期累計)（季節調整的外推，去年同期累計 <= 0 則不算）

誤差定義：(預測 − 實際) / origin 股價（業界常用的股價縮放，避免低 EPS 公司的百分比誤差爆炸）。

假設與判斷標準
  E1  下一季：主要版本(M2)的絕對誤差比 B1 小的公司比例，各 origin 平均 > 55%，且 NW t 值 > 2。
  E2  全年：同上，對 B2（較強的對照組）。
  E3  成長組（g >= 20% 且去年同季營收 >= 3 億千元... 即 3 億元）內，E1 也成立。
  E4  方向：預測 EPS 相對去年同季是增或減，與實際相同的比例 > 65%。（只描述，不作成敗依據）
  任一個主要假設（E1、E2、E3）不成立，就停下來和 Terry 討論，不把估算接上本益比。

資料完整性規則：目標季實際 EPS 不足 1,000 家（財報尚未公告完，只剩早交者，會有選擇偏誤）的 origin 不納入。

限制：存活者偏誤（已下市公司沒有財報）；公告日為法定期限的保守估計；合併報表；單季 EPS 由累計值相減；
  不處理增資、減資造成的股數變動（隱含股數用近四季，變動大的公司誤差會偏大）。
"""
import sys

import numpy as np
import pandas as pd

from core import db
from core.config import ROOT
from analysis.price_test import nw_tstat

FLOW = ["Revenue", "OperatingIncome", "NonOperating", "PreTax", "Tax", "NetIncome", "NetIncomeParent", "EPS"]
ORIGIN_MD = {1: (5, 15), 2: (8, 14), 3: (11, 14), 4: (3, 31)}


def pid(year, q):
    return year * 4 + q - 1


def pid_str(p):
    return f"{p // 4}Q{p % 4 + 1}"


def load_financials(conn):
    """回傳 (ytd, single)：item -> DataFrame(stock × period_int)。single 為累計值相減。"""
    df = pd.read_sql_query("SELECT stock_id, period, item, value FROM financials WHERE type='income'", conn)
    df["p"] = df["period"].str[:4].astype(int) * 4 + df["period"].str[-1].astype(int) - 1
    ytd, single = {}, {}
    for item in FLOW:
        w = df[df["item"] == item].pivot(index="stock_id", columns="p", values="value")
        cols = range(int(w.columns.min()), int(w.columns.max()) + 1)
        w = w.reindex(columns=cols)
        s = w.copy()
        for p in cols:
            if p % 4 != 0:                       # 非 Q1：本季累計 − 上季累計
                s[p] = w[p] - w[p - 1] if (p - 1) in w.columns else np.nan
        ytd[item], single[item] = w, s
    return ytd, single


def load_monthly(conn):
    d = pd.read_sql_query("SELECT stock_id, ym, revenue, revenue_last_year FROM monthly_revenue", conn)
    d["m"] = d["ym"].str[:4].astype(int) * 12 + d["ym"].str[5:].astype(int) - 1
    return (d.pivot(index="stock_id", columns="m", values="revenue"),
            d.pivot(index="stock_id", columns="m", values="revenue_last_year"))


def load_prices(conn):
    d = pd.read_sql_query("SELECT stock_id, date, close FROM prices WHERE close > 0", conn)
    return d.sort_values("date")


def price_at(prices, origin):
    lo = (pd.Timestamp(origin) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    x = prices[(prices["date"] <= origin) & (prices["date"] >= lo)]
    return x.groupby("stock_id")["close"].last()


def growth(rev, rev_ly, origin):
    """origin 當月之前最近 3 個已公告月份的 3 個月營收 YoY（小數）。"""
    y, m = int(origin[:4]), int(origin[5:7])
    latest = y * 12 + m - 1 - 1                  # origin 的上一個月（月索引 = 年*12+月-1）
    ms = [latest - 2, latest - 1, latest]
    if not set(ms) <= set(rev.columns):
        return pd.Series(dtype=float)
    r, ly = rev[ms], rev_ly[ms]
    ok = r.notna().all(axis=1) & ly.notna().all(axis=1) & (ly.sum(axis=1) > 0)
    return (r.sum(axis=1)[ok] / ly.sum(axis=1)[ok] - 1).clip(-0.5, 1.0)


def params(single, pL):
    """以最近已公告季別 pL 的近四季資料算出各公司的 nonop / tax / parent / shares / TTM 利益率。"""
    win = list(range(pL - 3, pL + 1))
    g = lambda it: single[it].reindex(columns=win)
    nonop = g("NonOperating").mean(axis=1, skipna=False)
    pt, tx = g("PreTax").sum(axis=1, min_count=4), g("Tax").sum(axis=1, min_count=4)
    tax = (tx / pt).where(pt > 0, 0.0).clip(0, 0.3)
    ni, nip = g("NetIncome").sum(axis=1, min_count=4), g("NetIncomeParent").sum(axis=1, min_count=4)
    parent = (nip / ni).where(ni > 0, 1.0).clip(0, 1)
    eps = g("EPS").sum(axis=1, min_count=4)
    shares = (nip / eps).where(eps.abs() >= 0.3)
    shares = shares.where(shares > 0)
    ttm_margin = g("OperatingIncome").sum(axis=1, min_count=4) / g("Revenue").sum(axis=1, min_count=4)
    return pd.DataFrame({"nonop": nonop, "tax": tax, "parent": parent, "shares": shares, "ttm_margin": ttm_margin})


def eps_hat(single, P, gr, pt, variant="M2"):
    """目標季別 pt 的 EPS 估算（Series，index = stock）。"""
    rev_ly = single["Revenue"].get(pt - 4)
    oi_ly = single["OperatingIncome"].get(pt - 4)
    if rev_ly is None or oi_ly is None:
        return pd.Series(dtype=float)
    margin = (oi_ly / rev_ly.where(rev_ly > 0)) if variant == "M2" else P["ttm_margin"]
    d = pd.DataFrame({"rev": rev_ly * (1 + gr), "margin": margin}).join(P, how="inner", rsuffix="_p")
    if variant != "M2":
        d["margin"] = P["ttm_margin"]
    return (((d["rev"] * d["margin"] + d["nonop"]) * (1 - d["tax"]) * d["parent"]) / d["shares"]).dropna()


def evaluate(conn):
    ytd, single = load_financials(conn)
    rev, rev_ly = load_monthly(conn)
    prices = load_prices(conn)
    last_p = max(single["EPS"].columns)
    rows_q, rows_fy = [], []
    for year in range(2021, 2027):
        for q in (1, 2, 3, 4):
            pL = pid(year, q)
            oy, om, od = (year + 1, *ORIGIN_MD[4]) if q == 4 else (year, *ORIGIN_MD[q])
            origin = f"{oy}-{om:02d}-{od:02d}"
            if origin < "2022-03-31":
                continue
            gr = growth(rev, rev_ly, origin)
            if gr.empty:
                continue
            P = params(single, pL)
            px = price_at(prices, origin)
            # --- 下一季 ---
            pt = pL + 1
            if pt <= last_p and single["EPS"][pt].notna().sum() >= 1000:   # 目標季財報尚未收齊（只有早交者）不納入
                act = single["EPS"][pt].dropna()
                est = {v: eps_hat(single, P, gr, pt, v) for v in ("M2", "M1")}
                d = pd.DataFrame({"act": act, "M2": est["M2"], "M1": est["M1"],
                                  "B1": single["EPS"].get(pt - 4), "B2": single["EPS"][pL],
                                  "g": gr, "px": px, "rev_ly": single["Revenue"].get(pt - 4)}).dropna(subset=["act", "M2", "px", "B1", "B2"])
                d["origin"], d["target"] = origin, pid_str(pt)
                rows_q.append(d.reset_index().rename(columns={"index": "stock_id"}))
            # --- 全年（只在 Q1~Q3 公告後）---
            if q <= 3 and (year * 4 + 3) <= last_p:
                fy_act = ytd["EPS"][year * 4 + 3]
                cum = ytd["EPS"][pL]
                rest = None
                for pt in range(pL + 1, year * 4 + 4):
                    e = eps_hat(single, P, gr, pt, "M2")
                    rest = e if rest is None else rest.add(e, fill_value=np.nan).where(rest.notna() & e.notna())
                fy_ly = ytd["EPS"].get(year * 4 - 1)
                ytd_ly = ytd["EPS"].get(pL - 4)
                b2 = cum * (fy_ly / ytd_ly.where(ytd_ly > 0)) if fy_ly is not None and ytd_ly is not None else None
                d = pd.DataFrame({"act": fy_act, "M2": cum + rest, "B1": fy_ly, "B2": b2, "g": gr, "px": px,
                                  "rev_ly": single["Revenue"].get(pL - 3)})
                d = d.dropna(subset=["act", "M2", "px", "B1", "B2"])
                d["origin"], d["target"] = origin, f"FY{year}"
                rows_fy.append(d.reset_index().rename(columns={"index": "stock_id"}))
    return pd.concat(rows_q, ignore_index=True), pd.concat(rows_fy, ignore_index=True)


def add_errors(d, models):
    for m in models:
        d[f"e_{m}"] = (d[m] - d["act"]) / d["px"]
        d[f"a_{m}"] = d[f"e_{m}"].abs()
    return d


def closer_share(d, a, b):
    """各 origin：a 的絕對誤差小於 b 的公司比例；回傳 (平均比例, NW t, origin 數)。"""
    s = d.groupby("origin").apply(lambda g: (g[f"a_{a}"] < g[f"a_{b}"]).mean(), include_groups=False)
    return s.mean(), nw_tstat(s - 0.5, 1), len(s)


def table(d, models, base_list, title):
    out = [f"### {title}", ""]
    rows = []
    for m in models:
        r = {"預測": m, "筆數": len(d), "絕對誤差中位數(占股價%)": round(d[f"a_{m}"].median() * 100, 3),
             "平均有號誤差(占股價%)": round(d[f"e_{m}"].clip(-1, 1).mean() * 100, 3)}
        rows.append(r)
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]
    rows = []
    for a in models:
        for b in base_list:
            if a == b:
                continue
            share, t, n = closer_share(d, a, b)
            rows.append({"比較": f"{a} 優於 {b} 的公司比例", "平均比例": f"{share:.1%}", "t值(NW)": round(t, 2), "origin數": n})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]
    return out


def report(q, fy):
    q = add_errors(q, ["M2", "M1", "B1", "B2"])
    fy = add_errors(fy, ["M2", "B1", "B2"])
    qg = q[(q["g"] >= 0.2) & (q["rev_ly"] >= 300000)]
    fg = fy[(fy["g"] >= 0.2) & (fy["rev_ly"] >= 300000)]
    out = ["# EPS 估算驗證", "",
           f"下一季：{q['origin'].nunique()} 個預測時點（{q['origin'].min()} ~ {q['origin'].max()}），共 {len(q)} 筆；"
           f"全年：{fy['origin'].nunique()} 個時點，共 {len(fy)} 筆。設計、公式與判斷標準見 `analysis/eps_estimate.py` 開頭。", ""]
    out += ["## 下一季 EPS", ""]
    out += table(q, ["M2", "M1", "B1", "B2"], ["B1", "B2"], "全部公司")
    out += table(qg, ["M2", "M1", "B1", "B2"], ["B1", "B2"], f"成長組（3 個月營收 YoY >= 20% 且去年同季營收 >= 3 億元，{len(qg)} 筆）")
    out += ["## 今年全年 EPS", ""]
    out += table(fy, ["M2", "B1", "B2"], ["B1", "B2"], "全部公司")
    out += table(fg, ["M2", "B1", "B2"], ["B1", "B2"], f"成長組（{len(fg)} 筆）")
    # 方向
    out += ["## E4：預測方向（相對去年同季 EPS 增或減）是否正確", ""]
    rows = []
    for name, x in (("全部", q), ("成長組", qg)):
        x = x[x["act"] != x["B1"]]
        hit = (np.sign(x["M2"] - x["B1"]) == np.sign(x["act"] - x["B1"]))
        rows.append({"樣本": name, "筆數": len(x), "方向正確": f"{hit.mean():.1%}"})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]
    # 各 origin 逐一
    s = q.groupby("origin").apply(lambda g: pd.Series({
        "目標季": g["target"].iloc[0], "筆數": len(g), "M2優於B1": f"{(g['a_M2'] < g['a_B1']).mean():.0%}",
        "M2優於B2": f"{(g['a_M2'] < g['a_B2']).mean():.0%}"}), include_groups=False)
    out += ["## 下一季：各預測時點明細", "", s.reset_index().to_markdown(index=False), ""]
    return "\n".join(out)


def main():
    conn = db.connect()
    q, fy = evaluate(conn)
    q.to_pickle(ROOT / "data" / "eps_q.pkl")
    fy.to_pickle(ROOT / "data" / "eps_fy.pkl")
    text = report(q, fy)
    (ROOT / "docs" / "eps_estimate_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
