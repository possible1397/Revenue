import pandas as pd
from analysis import metrics as mt, screen as sc
from core import db
from core.config import load_config


def _add(conn, sid, industry, revs, lys, start="2026-04", price=None):
    conn.execute("INSERT INTO companies (stock_id,name,market,industry,shares,updated_at) VALUES (?,?,?,?,?,'x')",
                 (sid, sid, "sii", industry, 1_000_000))
    for per, r, l in zip(pd.period_range(start, periods=len(revs), freq="M"), revs, lys):
        pub = f"{(per + 1).year}-{(per + 1).month:02d}-10"
        conn.execute("INSERT INTO monthly_revenue (stock_id, ym, revenue, revenue_last_year, yoy, published_at, source, updated_at) "
                     "VALUES (?,?,?,?,?,?,'t','x')", (sid, str(per), r, l, (r / l - 1) * 100 if l else None, pub))
    if price:  # (3 個月前價, 現價)
        conn.execute("INSERT INTO prices (stock_id,date,close,adj_close) VALUES (?, '2026-07-30', ?, ?)", (sid, price[0], price[0]))
        conn.execute("INSERT INTO prices (stock_id,date,close,adj_close) VALUES (?, '2026-10-30', ?, ?)", (sid, price[1], price[1]))


def _setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    big = 200000  # 2 億千元，高於 1 億門檻
    # 6 個月：YoY 持續 +40%
    _add(conn, "G1", "A", [big] * 6, [big / 1.4] * 6, price=(100, 101))      # 成長、股價沒動 -> 未反映
    _add(conn, "G2", "A", [big] * 6, [big / 1.4] * 6, price=(100, 160))      # 成長、股價大漲 -> 已反映
    _add(conn, "S1", "B", [big * 0.9, big * 0.8, big * 0.85, big * 0.95, big * 1.1, big * 1.2], [big] * 6)  # 拐點
    _add(conn, "L1", "B", [10000] * 6, [5000] * 6, price=(100, 101))          # 營收太小
    conn.commit()
    cfg = load_config()
    mt.save(conn, mt.compute(conn, cfg["monthly_screen"]["low_base_ratio"]))
    return conn, cfg


def test_lists(tmp_path):
    conn, cfg = _setup(tmp_path)
    df = sc.score_and_select(sc.build_frame(conn, "2026-11-01", cfg), cfg).set_index("stock_id")
    assert df.loc["G1", "list_type"] == sc.LIST_UNPRICED
    assert pd.isna(df.loc["G2", "list_type"])          # 成長但股價已漲過頭
    assert df.loc["S1", "list_type"] == sc.LIST_STARTING
    assert pd.isna(df.loc["L1", "list_type"])          # 單月營收低於門檻
    assert df["score"].between(0, 100).all()


def test_no_future_data(tmp_path):
    conn, cfg = _setup(tmp_path)
    # 2026-06-15 時，只有 4、5 月營收已公告（公告日為次月 10 日），6 月營收尚未公告
    df = sc.build_frame(conn, "2026-06-15", cfg)
    assert set(df["ym"]) == {"2026-05"}


def test_save_idempotent(tmp_path):
    conn, cfg = _setup(tmp_path)
    df = sc.score_and_select(sc.build_frame(conn, "2026-11-01", cfg), cfg)
    n1 = sc.save(conn, df, "2026-11-01")
    n2 = sc.save(conn, df, "2026-11-01")
    assert n1 == n2 == conn.execute("SELECT COUNT(*) FROM screens").fetchone()[0]
