"""股價淘汰測試：月度篩選的入選公司，之後的股價有沒有贏過同產業？

在看結果之前先寫下的設計與假設（2026-11-01，之後不因結果調整門檻或權重）：

設計
  * 每月 11 日（營收法定公告期限之後）為選股日 as_of，只用 published_at <= as_of 的營收與 as_of 前的股價
    （直接重用 analysis/screen.py，與實際使用的篩選邏輯完全相同）。
  * 進場價 = as_of 當天或之後第一個交易日的還原收盤價；出場價 = 進場日 + 30 / 91 / 182 個日曆天內最後一個交易日。
  * 超額報酬 = 個股報酬 − 同產業其他公司平均報酬（排除自己；該產業不足 6 家則用全市場平均）。
    全市場等權平均超額報酬也一併列出（近似大盤，未使用加權／櫃買指數）。
  * 每月先算組內平均超額報酬，再對各月取平均；t 值用 Newey-West（落後期 = 持有月數 − 1）處理重疊持有期。
  * 選股日範圍：2022-01 ~ 資料允許的最後月份。

假設（主要看 3 個月）
  H1  「成長未反映」清單的 3 個月平均超額報酬 > 0。
  H2  「成長未反映」的 3 個月超額報酬 > 「符合成長條件但差距 <= 0（已反映）」。
  H3  總分與 3 個月超額報酬的橫斷面 Spearman 相關（IC）平均 > 0；三個子分數另外各自檢驗。
  探索性（不作結論依據）：「成長將開始」清單、依市值分大小型、清單前 30 名。

已知偏誤（結果應往樂觀方向打折）
  * 存活者偏誤：下市公司沒有股價，不在樣本內。
  * 早期（2024 年以前）low_base 與 12 個月新高因歷史不足為空值，基期過低公司未被排除。
  * 公告日為估計值（次月 10 日）；未計交易成本與流動性；只有約 5 年、一個行情階段。
"""
import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

from core import db
from core.config import ROOT, load_config
from analysis import screen as sc

HORIZONS = {"1個月": 30, "3個月": 91, "6個月": 182}


def load_prices(conn):
    px = pd.read_sql_query("SELECT stock_id, date, adj_close FROM prices", conn)
    return px.pivot(index="date", columns="stock_id", values="adj_close").sort_index()


def fwd_returns(px: pd.DataFrame, as_of: str):
    """回傳 DataFrame(index=stock_id, columns=HORIZONS)，百分比報酬。"""
    idx = px.index
    i0 = idx.searchsorted(as_of)             # as_of 當天或之後第一個交易日
    if i0 >= len(idx):
        return pd.DataFrame()
    d0 = idx[i0]
    p0 = px.iloc[i0]
    out = {}
    for name, days in HORIZONS.items():
        target = (date.fromisoformat(d0) + timedelta(days=days)).isoformat()
        if target > idx[-1]:
            out[name] = pd.Series(np.nan, index=px.columns)
            continue
        i1 = idx.searchsorted(target, side="right") - 1
        out[name] = (px.iloc[i1] / p0 - 1) * 100
    return pd.DataFrame(out)


def excess(frame: pd.DataFrame, col: str) -> pd.Series:
    """同產業其他公司平均為基準（不足 6 家用全市場平均）。"""
    r = frame[col]
    ok = r.notna()
    grp = frame["industry"].where(ok)
    s = r.groupby(grp).transform("sum")
    n = r.groupby(grp).transform("count")
    loo = (s - r) / (n - 1)
    mkt = r[ok].mean()
    return r - loo.where(n >= 6, mkt)


def nw_tstat(x: pd.Series, lag: int) -> float:
    x = x.dropna().values
    n = len(x)
    if n < lag + 3:
        return np.nan
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        var += 2 * w * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(var / n) if var > 0 else np.nan


def monthly_dates(last_price_date: str):
    out, y, m = [], 2022, 1
    while True:
        d = date(y, m, 11).isoformat()
        if d > last_price_date:
            break
        out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def collect(conn, cfg):
    px = load_prices(conn)
    last = px.index[-1]
    recs, ics = [], []
    for as_of in monthly_dates(last):
        df = sc.score_and_select(sc.build_frame(conn, as_of, cfg), cfg)
        if df.empty:
            continue
        fr = fwd_returns(px, as_of)
        if fr.empty:
            continue
        df = df.merge(fr, left_on="stock_id", right_index=True, how="left")
        for h in HORIZONS:
            df[f"ex_{h}"] = excess(df, h)
            df[f"mx_{h}"] = df[h] - df[h].mean()
        df["as_of"] = as_of
        med = df["market_cap"].median()
        df["small"] = df["market_cap"] < med
        recs.append(df)
    return pd.concat(recs, ignore_index=True)


def groups(d: pd.DataFrame):
    g = {
        "全市場": d,
        "符合成長條件": d[d["is_growth"]],
        "成長未反映（入選）": d[d["list_type"] == sc.LIST_UNPRICED],
        "成長已反映（差距<=0）": d[d["is_growth"] & (d["list_type"] != sc.LIST_UNPRICED)],
        "成長將開始（探索）": d[d["list_type"] == sc.LIST_STARTING],
    }
    u = d[d["list_type"] == sc.LIST_UNPRICED]
    g["未反映・小型（探索）"] = u[u["small"]]
    g["未反映・大型（探索）"] = u[~u["small"]]
    g["未反映・前30名（探索）"] = u.sort_values("score", ascending=False).groupby("as_of").head(30)
    return g


def summarize(d: pd.DataFrame, col: str, lag: int):
    m = d.groupby("as_of")[col].mean()
    cnt = d.dropna(subset=[col]).groupby("as_of").size()
    return {"月數": int(m.notna().sum()), "平均每月檔數": round(cnt.mean(), 1) if len(cnt) else 0,
            "平均超額報酬%": round(m.mean(), 2), "t值(NW)": round(nw_tstat(m, lag), 2),
            "超額>0的月份": f"{(m > 0).mean():.0%}"}


def ic_table(d: pd.DataFrame, h: str, lag: int):
    rows = []
    for name, col in (("總分", "score"), ("基本面動能", "s_fundamental"), ("領先訊號", "s_leading"),
                      ("市場尚未反映", "s_unpriced")):
        ics = []
        for _, g in d.groupby("as_of"):
            x = g[[col, f"ex_{h}"]].dropna()
            ics.append(x[col].rank().corr(x[f"ex_{h}"].rank()) if len(x) > 50 else np.nan)  # Spearman = 排名的皮爾森
        s = pd.Series(ics)
        rows.append({"分數": name, "平均IC": round(s.mean(), 4), "t值(NW)": round(nw_tstat(s, lag), 2),
                     "IC>0的月份": f"{(s > 0).mean():.0%}", "月數": int(s.notna().sum())})
    return pd.DataFrame(rows)


def report(d: pd.DataFrame) -> str:
    out = ["# 股價淘汰測試", "",
           f"選股日 {d['as_of'].min()} ~ {d['as_of'].max()}（每月 11 日），共 {d['as_of'].nunique()} 個月。"
           "設計、假設與已知偏誤見 `analysis/price_test.py` 開頭。報酬為還原股價，未計成本。", ""]
    g = groups(d)
    for h, days in HORIZONS.items():
        lag = max(round(days / 30) - 1, 0)
        out += [f"## 持有 {h}：平均超額報酬（相對同產業）", ""]
        rows = []
        for name, sub in g.items():
            r = summarize(sub, f"ex_{h}", lag)
            r2 = summarize(sub, f"mx_{h}", lag)
            rows.append({"組別": name, **r, "相對全市場%": r2["平均超額報酬%"]})
        out += [pd.DataFrame(rows).to_markdown(index=False), ""]
    out += ["## H3：分數與 3 個月超額報酬的橫斷面 IC（Spearman）", "", ic_table(d, "3個月", 2).to_markdown(index=False), ""]
    return "\n".join(out)


def main():
    cfg = load_config()
    conn = db.connect()
    cache = ROOT / "data" / "price_test_frame.pkl"
    if "--cached" in sys.argv and cache.exists():
        d = pd.read_pickle(cache)
    else:
        d = collect(conn, cfg)
        d.to_pickle(cache)
    text = report(d)
    (ROOT / "docs" / "price_test_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
