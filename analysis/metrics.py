"""M3 月度指標計算。

輸入 monthly_revenue，輸出寬表（每家公司每個營收月一列）並寫入 metrics 表（長格式，as_of = 營收所屬月 YYYY-MM）。
全部由程式計算，同一輸入重跑結果相同（INSERT OR REPLACE）。回測時以 monthly_revenue.published_at 判斷可用時點。

指標（%為百分比數值）：
  yoy, mom, cum_yoy        直接取自公告值
  yoy_3m                   近 3 個月營收合計 / 去年同期 3 個月合計 - 1
  consec_growth            連續 YoY > 0 的月數（到本月為止）
  accel                    本月 yoy_3m > 上月 yoy_3m（1 / 0）
  turnaround               yoy_3m 由負轉正，或衰退幅度（負值絕對值）連 3 個月收斂（1 / 0）
  high_12 / high_36 / high_all   本月營收創近 12 / 36 個月 / 有資料以來新高（1 / 0）
  low_base                 去年同月營收 < 近 36 個月平均營收 × low_base_ratio（1 / 0）
"""
import numpy as np
import pandas as pd

from core import db
from core.config import load_config

METRIC_COLS = ["yoy", "mom", "cum_yoy", "yoy_3m", "consec_growth", "accel", "turnaround",
               "high_12", "high_36", "high_all", "low_base"]


def _consec_positive(s: pd.Series) -> pd.Series:
    pos = (s > 0).astype(int)
    grp = (pos == 0).cumsum()
    return pos.groupby(grp).cumsum().where(s.notna(), np.nan)


def _one_stock(g: pd.DataFrame, low_base_ratio: float) -> pd.DataFrame:
    g = g.set_index("period").sort_index()
    full = pd.period_range(g.index.min(), g.index.max(), freq="M")
    g = g.reindex(full)
    rev, ly = g["revenue"], g["revenue_last_year"]

    r3, l3 = rev.rolling(3, min_periods=3).sum(), ly.rolling(3, min_periods=3).sum()
    yoy_3m = (r3 / l3 - 1) * 100
    yoy_3m = yoy_3m.where(l3 > 0)

    d1 = yoy_3m.diff()
    shrinking = (yoy_3m < 0) & (d1 > 0)  # 負值且比上月好
    narrowing3 = shrinking.rolling(3, min_periods=3).sum() == 3
    crossed = (yoy_3m > 0) & (yoy_3m.shift(1) < 0)

    out = pd.DataFrame(index=g.index)
    out["yoy"], out["mom"], out["cum_yoy"] = g["yoy"], g["mom"], g["cum_yoy"]
    out["yoy_3m"] = yoy_3m
    out["consec_growth"] = _consec_positive(g["yoy"])
    out["accel"] = (yoy_3m > yoy_3m.shift(1)).astype(float).where(yoy_3m.notna() & yoy_3m.shift(1).notna())
    out["turnaround"] = (crossed | narrowing3).astype(float).where(yoy_3m.notna() & yoy_3m.shift(1).notna())
    for name, n in (("high_12", 12), ("high_36", 36)):
        out[name] = (rev >= rev.rolling(n, min_periods=n).max()).astype(float).where(rev.notna() & (rev.rolling(n, min_periods=n).count() == n))
    out["high_all"] = (rev >= rev.expanding().max()).astype(float).where(rev.notna())
    avg36 = rev.rolling(36, min_periods=36).mean()
    out["low_base"] = (ly < avg36 * low_base_ratio).astype(float).where(ly.notna() & avg36.notna())
    out["revenue"] = rev
    return out


def compute(conn, low_base_ratio=0.5) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT stock_id, ym, revenue, revenue_last_year, mom, yoy, cum_yoy FROM monthly_revenue", conn)
    df["period"] = pd.PeriodIndex(df["ym"], freq="M")
    parts = []
    for sid, g in df.groupby("stock_id", sort=False):
        o = _one_stock(g.drop(columns=["stock_id", "ym"]), low_base_ratio)
        o.insert(0, "stock_id", sid)
        parts.append(o)
    res = pd.concat(parts)
    res.index.name = "period"
    res = res.reset_index()
    res["ym"] = res["period"].astype(str)
    # 補出來的缺月（原本沒有營收資料）不輸出
    have = set(zip(df["stock_id"], df["ym"]))
    res = res[[(a, b) in have for a, b in zip(res["stock_id"], res["ym"])]]
    return res.drop(columns=["period"])


def save(conn, res: pd.DataFrame) -> int:
    long = res.melt(id_vars=["stock_id", "ym"], value_vars=METRIC_COLS, var_name="name", value_name="value")
    long = long.dropna(subset=["value"])
    conn.executemany("INSERT OR REPLACE INTO metrics (stock_id, as_of, name, value) VALUES (?,?,?,?)",
                     [(r.stock_id, r.ym, r.name, float(r.value)) for r in long.itertuples(index=False)])
    conn.commit()
    return len(long)


def run(conn=None, cfg=None):
    cfg = cfg or load_config()
    conn = conn or db.connect()
    with db.job_run(conn, "metrics_monthly") as info:
        res = compute(conn, cfg["monthly_screen"]["low_base_ratio"])
        info["rows"] = save(conn, res)
    return res


if __name__ == "__main__":
    r = run()
    print(f"完成：{len(r)} 列，{r['ym'].min()} ~ {r['ym'].max()}")
