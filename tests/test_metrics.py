import pandas as pd
from analysis import metrics as mt
from core import db


def _load(conn, sid, revs, lys):
    """revs / lys: 從 2024-01 起的連續月份營收與去年同月營收"""
    p = pd.period_range("2024-01", periods=len(revs), freq="M")
    for per, r, l in zip(p, revs, lys):
        yoy = (r / l - 1) * 100 if l else None
        conn.execute("INSERT INTO monthly_revenue (stock_id, ym, revenue, revenue_last_year, yoy, published_at, source, updated_at) "
                     "VALUES (?,?,?,?,?,'2025-01-10','t','x')", (sid, str(per), r, l, yoy))
    conn.commit()


def test_basic_metrics(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    #            1月  2月  3月  4月  5月
    revs = [100, 110, 120, 130, 150]
    lys = [100, 100, 100, 100, 100]
    _load(conn, "AAA", revs, lys)
    res = mt.compute(conn).set_index("ym")
    assert round(res.loc["2024-03", "yoy_3m"], 2) == 10.0           # (330/300-1)
    assert round(res.loc["2024-05", "yoy_3m"], 2) == 33.33          # (400/300-1)
    assert res.loc["2024-01", "yoy_3m"] != res.loc["2024-01", "yoy_3m"]  # 不足 3 個月為 NaN
    assert res.loc["2024-05", "consec_growth"] == 4                  # 1 月 YoY=0 不算成長，2~5 月連 4 個月
    assert res.loc["2024-05", "accel"] == 1
    assert res.loc["2024-05", "high_all"] == 1 and res.loc["2024-04", "high_all"] == 1


def test_consecutive_resets_and_turnaround(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    lys = [100] * 6
    revs = [90, 80, 85, 95, 110, 120]  # 轉負後回升
    _load(conn, "BBB", revs, lys)
    res = mt.compute(conn).set_index("ym")
    assert res.loc["2024-02", "consec_growth"] == 0
    assert res.loc["2024-06", "consec_growth"] == 2
    # 3M YoY：3月 -15, 4月 -13.33, 5月 -3.33, 6月 +8.33 -> 6 月由負轉正
    assert res.loc["2024-05", "turnaround"] == 0  # 只收斂 2 個月，未滿 3 個月
    assert res.loc["2024-06", "turnaround"] == 1


def test_gap_months_do_not_misalign(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _load(conn, "CCC", [100, 100, 100, 100], [50, 50, 50, 50])
    conn.execute("DELETE FROM monthly_revenue WHERE ym='2024-02'")
    conn.commit()
    res = mt.compute(conn)
    assert "2024-02" not in set(res["ym"])
    r = res.set_index("ym")
    assert r.loc["2024-03", "yoy_3m"] != r.loc["2024-03", "yoy_3m"]  # 缺月導致 3 個月不足 -> NaN


def test_save_idempotent(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _load(conn, "AAA", [100, 110, 120, 130], [100] * 4)
    res = mt.compute(conn)
    n1 = mt.save(conn, res)
    n2 = mt.save(conn, res)
    assert n1 == n2
    assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == n1
