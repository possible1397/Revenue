"""營收成長持續性測試（P2 前置）：目前符合成長條件的公司，未來的營收成長會不會持續？

在看結果之前先寫下的假設（2026-11-01，之後不因結果改動）：
  結果變數
    Y1：t+3 個月的單月 YoY > 0
    Y3：t+3 個月的 3 個月滾動 YoY >= 20%（成長維持在門檻以上）
  H1  成長組（yoy_3m>=20、連續成長>=3 個月、單月營收>=1 億、非基期過低）的 Y1、Y3 命中率
      高於「全部公司月份」，也高於只要求「當月 YoY>0」的簡單持續基準。
  H2  在成長組內，成長加速、創 12 個月新高、產業擴散各自使 Y3 命中率提高。
  H3  各年度（2022–2026）方向一致，而非只在某一年成立。
  只做以上檢驗，不調整門檻、不搜尋其他組合。

只使用 t 月及之前已知的資訊分組；結果變數取自 t 月之後的營收。
限制：
  * 同一家公司連續月份高度相關，樣本數不等於獨立樣本數，信賴區間偏窄。
  * 產業用「目前」的產業別。
  * 此測試只回答營收是否持續，不回答股價是否已反映。
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from core import db
from core.config import ROOT, load_config
from analysis import metrics as mt

H = 3  # 往後看幾個月


def build(conn, cfg) -> pd.DataFrame:
    sc = cfg["monthly_screen"]
    m = mt.compute(conn, sc["low_base_ratio"])
    comp = pd.read_sql_query("SELECT stock_id, industry FROM companies", conn)
    m = m.merge(comp, on="stock_id", how="left")
    m["p"] = pd.PeriodIndex(m["ym"], freq="M")

    # 產業擴散：同產業 YoY>0 比例連 2 個月上升（與 screen.py 同定義）
    pos = m.dropna(subset=["yoy", "industry"]).assign(pos=lambda d: d["yoy"] > 0)
    frac = pos.groupby(["industry", "p"]).agg(f=("pos", "mean"), n=("pos", "size")).reset_index()
    frac = frac.sort_values(["industry", "p"])
    g = frac.groupby("industry")
    ok = (g["p"].diff(1).apply(lambda x: x.n if pd.notna(x) else np.nan) == 1) & \
         (g["p"].diff(2).apply(lambda x: x.n if pd.notna(x) else np.nan) == 2)
    frac["diffusion"] = ((frac["f"] > g["f"].shift(1)) & (g["f"].shift(1) > g["f"].shift(2)) & ok
                         & (frac["n"] >= sc["min_industry_size"])).astype(float)
    m = m.merge(frac[["industry", "p", "diffusion"]], on=["industry", "p"], how="left")

    fut = m[["stock_id", "p", "yoy", "yoy_3m"]].copy()
    fut["p"] = fut["p"] - H
    fut = fut.rename(columns={"yoy": "yoy_fut", "yoy_3m": "yoy3m_fut"})
    m = m.merge(fut, on=["stock_id", "p"], how="left")
    m = m.dropna(subset=["yoy_fut", "yoy3m_fut", "yoy_3m"]).copy()
    m["Y1"] = (m["yoy_fut"] > 0).astype(float)
    m["Y3"] = (m["yoy3m_fut"] >= sc["min_yoy_3m"]).astype(float)
    eligible = (m["revenue"] >= sc["min_monthly_revenue_k"]) & (m["low_base"].fillna(0) != 1)
    m["growth"] = (m["yoy_3m"] >= sc["min_yoy_3m"]) & (m["consec_growth"] >= sc["min_consecutive_months"]) & eligible
    m["yoy_pos"] = m["yoy"] > 0
    m["year"] = m["p"].dt.year
    return m


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def row(name, d):
    n = len(d)
    r = {"組別": name, "樣本數": n, "公司數": d["stock_id"].nunique()}
    for y in ("Y1", "Y3"):
        k = int(d[y].sum())
        lo, hi = wilson(k, n)
        r[f"{y}命中率"] = k / n if n else np.nan
        r[f"{y}區間"] = f"{lo:.1%}~{hi:.1%}" if n else "-"
    return r


def table(rows):
    df = pd.DataFrame(rows)
    for c in ("Y1命中率", "Y3命中率"):
        df[c] = df[c].map(lambda v: f"{v:.1%}")
    return df.to_markdown(index=False) if hasattr(df, "to_markdown") else df.to_string(index=False)


def report(m: pd.DataFrame) -> str:
    g = m[m["growth"]]
    out = ["# 營收成長持續性測試", "",
           f"樣本：{m['ym'].min()} ~ {m['ym'].max()} 的公司月份（需有 t+{H} 個月的結果），共 {len(m):,} 筆。",
           "假設與限制見 `analysis/persistence.py` 開頭。Y1 = t+3 單月 YoY>0；Y3 = t+3 的 3 個月 YoY ≥ 20%。", "",
           "## H1：成長組 vs 基準", "",
           table([row("全部公司月份", m), row("僅要求當月 YoY>0", m[m["yoy_pos"]]), row("成長組", g)]), "",
           "## H2：成長組內的附加訊號", ""]
    rows = [row("成長組（全部）", g)]
    for k, label in (("accel", "成長加速"), ("high_12", "創 12 個月新高"), ("diffusion", "產業擴散")):
        rows.append(row(f"　有{label}", g[g[k] == 1]))
        rows.append(row(f"　無{label}", g[g[k] != 1]))
    out += [table(rows), "", "## 連續成長月數（成長組）", ""]
    bins = pd.cut(g["consec_growth"], [2, 5, 11, 23, 1e9], labels=["3-5", "6-11", "12-23", "24+"])
    out += [table([row(f"連續 {b} 個月", g[bins == b]) for b in bins.cat.categories]), "",
            "## H3：分年度（成長組 vs 全部）", ""]
    rows = []
    for y in sorted(m["year"].unique()):
        rows.append(row(f"{y} 成長組", g[g["year"] == y]))
        rows.append(row(f"{y} 全部", m[m["year"] == y]))
    out += [table(rows), ""]
    return "\n".join(out)


def main():
    cfg = load_config()
    conn = db.connect()
    m = build(conn, cfg)
    text = report(m)
    p = ROOT / "docs" / "persistence_report.md"
    p.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
