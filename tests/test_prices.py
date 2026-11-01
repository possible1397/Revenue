from datetime import date, datetime, timedelta
import pandas as pd
from collectors import prices as pr
from core import db


def _df(days, close0=100.0, adj_factor=1.0):
    idx = [date.today() - timedelta(days=days - i) for i in range(days)]
    close = [close0 + i for i in range(days)]
    return pd.DataFrame({"Close": close, "Adj Close": [c * adj_factor for c in close], "Volume": [1000] * days},
                        index=pd.DatetimeIndex(idx))


def _db(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO companies (stock_id, name, market, shares, updated_at) VALUES ('2330','x','sii',1000000,'x')")
    conn.commit()
    return conn


def test_ticker():
    assert pr.yf_ticker("2330", "sii") == "2330.TW"
    assert pr.yf_ticker("5460", "otc") == "5460.TWO"


def test_initial_and_incremental_idempotent(tmp_path):
    conn = _db(tmp_path)
    fetch = lambda t, s: _df(30)
    far = datetime(2026, 1, 3, 15, 0)  # 週六，不會被視為盤中
    assert pr.update_one(conn, "2330", "sii", fetch=fetch, now=far) == 30
    assert pr.update_one(conn, "2330", "sii", fetch=fetch, now=far) > 0
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 30  # 重跑不重複


def test_adjustment_triggers_full_refetch(tmp_path):
    conn = _db(tmp_path)
    far = datetime(2026, 1, 3, 15, 0)
    pr.update_one(conn, "2330", "sii", fetch=lambda t, s: _df(30), now=far)
    calls = []

    def fetch(t, s):
        calls.append(s)
        return _df(30, adj_factor=0.95)  # 除息後還原基準改變
    pr.update_one(conn, "2330", "sii", fetch=fetch, now=far)
    assert len(calls) == 2  # 增量一次 + 全量一次
    row = conn.execute("SELECT close, adj_close FROM prices ORDER BY date LIMIT 1").fetchone()
    assert round(row["adj_close"] / row["close"], 2) == 0.95


def test_trim_incomplete_today():
    today = date(2026, 11, 2)  # 週一
    df = pd.DataFrame({"Close": [1, 2]}, index=pd.DatetimeIndex([date(2026, 10, 30), today]))
    assert len(pr._trim_incomplete_today(df, datetime(2026, 11, 2, 11, 0))) == 1
    assert len(pr._trim_incomplete_today(df, datetime(2026, 11, 2, 15, 0))) == 2


def test_market_cap(tmp_path):
    conn = _db(tmp_path)
    pr.update_one(conn, "2330", "sii", fetch=lambda t, s: _df(3, close0=100), now=datetime(2026, 1, 3, 15))
    pr.refresh_market_cap(conn)
    row = conn.execute("SELECT close, market_cap FROM prices ORDER BY date LIMIT 1").fetchone()
    assert row["market_cap"] == row["close"] * 1000000 / 1000


def test_empty_first_fetch_is_failure(tmp_path):
    import pytest
    conn = _db(tmp_path)
    with pytest.raises(RuntimeError):
        pr.update_one(conn, "2330", "sii", fetch=lambda t, s: pd.DataFrame(), now=datetime(2026, 1, 3, 15))
