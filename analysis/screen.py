"""M4 月度篩選與綜合分數。

as_of 可指定任一日期，只使用 published_at <= as_of 的營收與 date <= as_of 的股價，回測（P2）可直接重用。

兩份清單（互斥）：
  growth_unpriced  成長未反映：月度成長條件成立，且 3M YoY − 近 3 個月股價漲幅 > min_gap
  growth_starting  成長將開始：不符合月度成長條件，但營收拐點，或領先訊號 >= 2 個

綜合分數 0–100 = 基本面動能 × 40% + 領先訊號 × 30% + 市場尚未反映 × 30%（權重見 config.yaml）。
  基本面動能：yoy_3m、cum_yoy、consec_growth 三項的全市場百分位平均；成長加速 +5、創 12 個月新高 +5，上限 100
  領先訊號  ：目前只有「營收拐點」「產業擴散」兩項可由營收算出，分數 = 成立個數 / 2 × 100
              （合約負債、擴產、法說上修、上游帶動、新聞事件尚待 P3–P5 資料）
  市場尚未反映：(yoy_3m − 近 3 個月股價漲幅) 的全市場百分位
              （本益比百分位需 P3 財報、公告後股價反應需另行實作，目前未納入）

尚未實作的排除條件：全額交割 / 處置股（尚無資料來源，見 docs）。
"""
import json
import sys
import argparse
from datetime import date, timedelta

import numpy as np
import pandas as pd

from core import db
from core.config import load_config
from analysis.metrics import METRIC_COLS

LIST_UNPRICED = "growth_unpriced"
LIST_STARTING = "growth_starting"


def _ym_shift(ym: str, n: int) -> str:
    return str(pd.Period(ym, freq="M") + n)


def _latest_prices(conn, on_or_before: str, max_stale_days: int, col="adj_close"):
    df = pd.read_sql_query(
        f"SELECT p.stock_id, p.date, p.{col} AS px FROM prices p JOIN "
        "(SELECT stock_id, MAX(date) d FROM prices WHERE date <= ? GROUP BY stock_id) m "
        "ON p.stock_id = m.stock_id AND p.date = m.d", conn, params=(on_or_before,))
    cutoff = (date.fromisoformat(on_or_before) - timedelta(days=max_stale_days)).isoformat()
    df.loc[df["date"] < cutoff, "px"] = np.nan
    return df.set_index("stock_id")["px"]


def build_frame(conn, as_of: str, cfg: dict) -> pd.DataFrame:
    sc = cfg["monthly_screen"]
    as_of_d = date.fromisoformat(as_of)

    rev = pd.read_sql_query(
        "SELECT stock_id, ym, revenue, published_at FROM monthly_revenue WHERE published_at <= ?",
        conn, params=(as_of,))
    if rev.empty:
        return pd.DataFrame()
    earliest_ok = _ym_shift(as_of_d.strftime("%Y-%m"), -2)
    rev = rev[rev["ym"] >= earliest_ok].sort_values("ym").drop_duplicates("stock_id", keep="last")

    yms = sorted({_ym_shift(y, -k) for y in rev["ym"].unique() for k in (0, 1, 2)})
    ph = ",".join("?" * len(yms))
    m = pd.read_sql_query(f"SELECT stock_id, as_of AS ym, name, value FROM metrics WHERE as_of IN ({ph})",
                          conn, params=yms)
    wide = m.pivot_table(index=["stock_id", "ym"], columns="name", values="value").reset_index()

    for c in METRIC_COLS:  # 資料不足時整欄可能不存在，補成 NaN
        if c not in wide.columns:
            wide[c] = np.nan
    df = rev.merge(wide, on=["stock_id", "ym"], how="left")
    comp = pd.read_sql_query("SELECT stock_id, name AS company, market, industry, shares FROM companies", conn)
    df = df.merge(comp, on="stock_id", how="left")

    # 產業擴散：同產業 YoY>0 的公司比例，連 2 個月上升
    allyoy = wide[["stock_id", "ym", "yoy"]].dropna().merge(comp[["stock_id", "industry"]], on="stock_id")
    allyoy = allyoy[allyoy["industry"].notna()]
    frac = (allyoy.assign(pos=allyoy["yoy"] > 0).groupby(["industry", "ym"])
            .agg(frac=("pos", "mean"), n=("pos", "size")))
    fd = frac["frac"].to_dict()
    nd = frac["n"].to_dict()

    def diffusion(row):
        ind, ym = row["industry"], row["ym"]
        if not isinstance(ind, str):
            return np.nan
        k0, k1, k2 = (ind, ym), (ind, _ym_shift(ym, -1)), (ind, _ym_shift(ym, -2))
        if not all(k in fd for k in (k0, k1, k2)) or nd[k0] < sc["min_industry_size"]:
            return np.nan
        return float(fd[k0] > fd[k1] > fd[k2])

    df["diffusion"] = df.apply(diffusion, axis=1)

    # 股價漲幅
    stale = sc["max_price_staleness_days"]
    px_now = _latest_prices(conn, as_of, stale)
    px_prev = _latest_prices(conn, (as_of_d - timedelta(days=sc["price_window_days"])).isoformat(), stale)
    ret = (px_now / px_prev - 1) * 100
    df["ret_3m"] = df["stock_id"].map(ret)
    close_now = _latest_prices(conn, as_of, stale, col="close")
    df["close"] = df["stock_id"].map(close_now)
    df["market_cap"] = df["close"] * df["shares"] / 1000.0   # 千元
    df["gap"] = df["yoy_3m"] - df["ret_3m"]
    return df


def score_and_select(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    sc, w = cfg["monthly_screen"], cfg["score_weights"]
    if df.empty:
        return df
    df = df.copy()
    pct = lambda s: s.rank(pct=True)
    fm = pd.concat([pct(df["yoy_3m"]), pct(df["cum_yoy"]), pct(df["consec_growth"])], axis=1).mean(axis=1) * 100
    fm = (fm + 5 * df["accel"].fillna(0) + 5 * df["high_12"].fillna(0)).clip(upper=100)
    df["leading_count"] = df["turnaround"].fillna(0) + df["diffusion"].fillna(0)
    ls = df["leading_count"] / 2 * 100
    nyp = (pct(df["gap"]) * 100).fillna(0)
    df["s_fundamental"], df["s_leading"], df["s_unpriced"] = fm, ls, nyp
    df["score"] = (w["fundamental_momentum"] * fm + w["leading_signals"] * ls + w["not_yet_priced"] * nyp).round(2)

    eligible = (df["revenue"] >= sc["min_monthly_revenue_k"]) & (df["low_base"].fillna(0) != 1)
    growth = ((df["yoy_3m"] >= sc["min_yoy_3m"]) & (df["consec_growth"] >= sc["min_consecutive_months"]) & eligible)
    df["is_growth"] = growth
    unpriced = growth & (df["gap"] > sc["min_gap"])
    starting = (~growth) & eligible & ((df["turnaround"] == 1) | (df["leading_count"] >= 2))
    df["list_type"] = np.where(unpriced, LIST_UNPRICED, np.where(starting, LIST_STARTING, None))
    return df


def _reasons(r) -> str:
    keys = ["ym", "revenue", "yoy", "yoy_3m", "cum_yoy", "consec_growth", "accel", "turnaround", "diffusion",
            "high_12", "ret_3m", "gap", "close", "market_cap", "s_fundamental", "s_leading", "s_unpriced"]
    out = {}
    for k in keys:
        v = r[k]
        out[k] = None if (isinstance(v, float) and np.isnan(v)) else (v if isinstance(v, str) else float(v))
    return json.dumps(out, ensure_ascii=False)


def save(conn, df: pd.DataFrame, as_of: str) -> int:
    sel = df[df["list_type"].notna()]
    conn.execute("DELETE FROM screens WHERE as_of=?", (as_of,))   # 同日重跑覆蓋，不重複
    conn.executemany("INSERT INTO screens (as_of, list_type, stock_id, score, reasons) VALUES (?,?,?,?,?)",
                     [(as_of, r["list_type"], r["stock_id"], float(r["score"]), _reasons(r))
                      for _, r in sel.iterrows()])
    conn.commit()
    return len(sel)


def run(conn=None, cfg=None, as_of=None):
    cfg = cfg or load_config()
    conn = conn or db.connect()
    as_of = as_of or date.today().isoformat()
    with db.job_run(conn, "screen_monthly") as info:
        df = score_and_select(build_frame(conn, as_of, cfg), cfg)
        info["rows"] = save(conn, df, as_of) if not df.empty else 0
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None)
    a = ap.parse_args()
    d = run(as_of=a.as_of)
    print(d["list_type"].value_counts(dropna=False).to_string())
