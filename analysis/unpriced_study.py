"""「市場尚未反映」診斷：現行定義（3M YoY − 近 3 個月股價漲幅）為什麼沒通過 H2？

在看結果之前先寫下的問題與假設（2026-11-02，讀取 price_test 存下的中間檔，唯讀、不動資料庫）：
  U1  在符合成長條件的公司內，近 3 個月股價漲幅（ret_3m）與之後 3 個月超額報酬的 IC 為「正」（動能延續），
      也就是現行「股價漲得少 = 尚未反映」的方向是反的。
  U2  Fama-MacBeth 橫斷面迴歸（排名後）：ex_3個月 ~ yoy_3m + ret_3m，ret_3m 的平均係數為正，
      yoy_3m 的平均係數為正；若成立，現行差距 = yoy_3m − ret_3m 把股價動能的正效果扣掉了。
  U3  「市場尚未反映」子分數與「基本面動能」子分數的橫斷面相關偏高（>0.5），故其 IC 不是獨立貢獻。
只檢驗以上三項，不調整定義或搜尋其他組合；結論只決定是否把「尚未反映」從入選條件拿掉。

限制：同 price_test（含存活者偏誤殘餘、估計公告日、無風險調整與交易成本）。
"""
import sys

import numpy as np
import pandas as pd

from core.config import ROOT
from analysis.price_test import nw_tstat

Y = "ex_3個月"


def rank_corr(a: pd.Series, b: pd.Series):
    x = pd.concat([a, b], axis=1).dropna()
    return x.iloc[:, 0].rank().corr(x.iloc[:, 1].rank()) if len(x) > 50 else np.nan


def ic_by_month(d: pd.DataFrame, col: str):
    return pd.Series({m: rank_corr(g[col], g[Y]) for m, g in d.groupby("as_of")})


def fm_coefs(d: pd.DataFrame, xs):
    rows = {}
    for m, g in d.groupby("as_of"):
        g = g[xs + [Y]].dropna()
        if len(g) < 80:
            continue
        r = g.rank(pct=True)
        X = np.column_stack([np.ones(len(r))] + [r[c].values for c in xs])
        rows[m] = np.linalg.lstsq(X, r[Y].values, rcond=None)[0][1:]
    return pd.DataFrame(rows, index=xs).T


def report(d: pd.DataFrame) -> str:
    g = d[d["is_growth"]]
    out = ["# 「市場尚未反映」診斷", "",
           "問題與假設見 `analysis/unpriced_study.py` 開頭。報酬為 3 個月持有、相對同產業的超額報酬。", ""]

    out += ["## U1：成長組內各變數與後續超額報酬的 IC（Spearman）", ""]
    rows = []
    for name, col in (("3M YoY", "yoy_3m"), ("累計 YoY", "cum_yoy"), ("連續成長月數", "consec_growth"),
                      ("近 3 個月股價漲幅", "ret_3m"), ("差距（現行定義）", "gap")):
        s = ic_by_month(g, col)
        rows.append({"變數": name, "平均IC": round(s.mean(), 4), "t值(NW)": round(nw_tstat(s, 2), 2),
                     "IC>0的月份": f"{(s > 0).mean():.0%}", "月數": int(s.notna().sum())})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]

    out += ["## U2：Fama-MacBeth 迴歸（排名後）：後續超額報酬 ~ 3M YoY + 近 3 個月股價漲幅", ""]
    rows = []
    for label, data in (("全市場", d), ("成長組", g)):
        c = fm_coefs(data, ["yoy_3m", "ret_3m"])
        for x, nm in (("yoy_3m", "3M YoY"), ("ret_3m", "近 3 個月股價漲幅")):
            rows.append({"樣本": label, "變數": nm, "平均係數": round(c[x].mean(), 4),
                         "t值(NW)": round(nw_tstat(c[x], 2), 2), "月數": len(c)})
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]

    out += ["## U3：子分數之間的橫斷面相關（各月 Spearman 平均）", ""]
    pairs = (("市場尚未反映 vs 基本面動能", "s_unpriced", "s_fundamental"),
             ("市場尚未反映 vs 3M YoY", "s_unpriced", "yoy_3m"),
             ("基本面動能 vs 領先訊號", "s_fundamental", "s_leading"))
    rows = [{"配對": n, "平均相關": round(pd.Series({m: rank_corr(x[a], x[b]) for m, x in d.groupby("as_of")}).mean(), 3)}
            for n, a, b in pairs]
    out += [pd.DataFrame(rows).to_markdown(index=False), ""]
    return "\n".join(out)


def main():
    d = pd.read_pickle(ROOT / "data" / "price_test_frame.pkl")
    text = report(d)
    (ROOT / "docs" / "unpriced_study.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    sys.exit(main())
