from collectors import revenue as rv
from core import db

HTML = """<table>
<tr><th>x</th></tr>
<tr align=right><td align=center>2330</td><td align=left>台積電</td><td nowrap> 511,857,399</td><td nowrap> 514,805,337</td><td nowrap> 330,980,920</td><td nowrap> -0.57</td><Td nowrap> 54.64</td><td nowrap> 3,898,726,974</td><td nowrap> 2,762,963,851</td><td nowrap> 41.10</td><td align=left>備註文字</td></tr>
<tr align=right><td align=center>2337</td><td align=left>旺宏</td><td nowrap> 8,367,275</td><td nowrap> 8,145,267</td><td nowrap> 2,902,927</td><td nowrap> 2.72</td><Td nowrap> 188.23</td><td nowrap> 53,830,664</td><td nowrap> 21,150,880</td><td nowrap> 154.50</td><td align=left>-</td></tr>
<tr><td>合計</td><td>x</td></tr>
</table>"""

API = [{"出表日期": "1151101", "資料年月": "11509", "公司代號": "2330", "公司名稱": "台積電", "產業別": "半導體業",
        "營業收入-當月營收": "511857399", "營業收入-上月營收": "514805337", "營業收入-去年當月營收": "330980920",
        "營業收入-上月比較增減(%)": "-0.57", "營業收入-去年同月增減(%)": "54.64",
        "累計營業收入-當月累計營收": "3898726974", "累計營業收入-去年累計營收": "2762963851",
        "累計營業收入-前期比較增減(%)": "41.10", "備註": "-"}]


def test_roc_and_estimate():
    assert rv.roc_to_ym("11509") == "2026-09"
    assert rv.roc_to_ym("10912") == "2020-12"
    assert rv.estimate_published("2026-09") == "2026-10-10"
    assert rv.estimate_published("2026-12") == "2027-01-10"


def test_parse_mops_html():
    companies, recs = rv.parse_mops_html(HTML, "2026-09", "sii")
    assert [c["stock_id"] for c in companies] == ["2330", "2337"]
    r = recs[0]
    assert r["revenue"] == 511857399 and r["yoy"] == 54.64 and r["cum_revenue"] == 3898726974
    assert recs[1]["note"] == "-"


def test_parse_openapi_matches_html():
    _, a = rv.parse_openapi(API, "sii")
    _, b = rv.parse_mops_html(HTML, "2026-09", "sii")
    for f in rv.FIELDS:
        assert a[0][f] == b[0][f], f


def test_months_back():
    from datetime import date
    assert rv.months_back(3, date(2026, 1, 15)) == [(2025, 12), (2025, 11), (2025, 10)]


def test_upsert_idempotent_and_revision(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _, recs = rv.parse_mops_html(HTML, "2026-09", "sii")
    assert rv.upsert_revenue(conn, recs, "mops_html", "2026-10-10", True) == (2, 0, 0)
    assert rv.upsert_revenue(conn, recs, "mops_html", "2026-10-10", True) == (0, 0, 2)  # 重跑不重複
    assert conn.execute("SELECT COUNT(*) FROM monthly_revenue").fetchone()[0] == 2

    recs[0]["revenue"] = 500000000  # 公司更正營收
    assert rv.upsert_revenue(conn, recs, "twse_openapi", "2026-10-20", False) == (0, 1, 1)
    hist = conn.execute("SELECT revenue FROM revenue_history WHERE stock_id='2330'").fetchall()
    assert [h[0] for h in hist] == [511857399]
    assert conn.execute("SELECT revenue FROM monthly_revenue WHERE stock_id='2330'").fetchone()[0] == 500000000


def test_actual_date_replaces_earlier_than_estimate(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    _, recs = rv.parse_mops_html(HTML, "2026-09", "sii")
    rv.upsert_revenue(conn, recs, "mops_html", "2026-10-10", True)
    rv.upsert_revenue(conn, recs, "twse_openapi", "2026-10-05", False)  # 實際抓到日早於估計
    row = conn.execute("SELECT published_at, published_is_estimate FROM monthly_revenue WHERE stock_id='2337'").fetchone()
    assert tuple(row) == ("2026-10-05", 0)
    rv.upsert_revenue(conn, recs, "twse_openapi", "2026-10-30", False)  # 之後更晚抓到不應覆蓋
    row = conn.execute("SELECT published_at FROM monthly_revenue WHERE stock_id='2337'").fetchone()
    assert row[0] == "2026-10-05"


def test_check_flags_drop(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO companies (stock_id,name,market,updated_at) VALUES ('1','a','sii','x')")
    conn.execute("INSERT INTO companies (stock_id,name,market,updated_at) VALUES ('2','b','sii','x')")
    for ym, sids in (("2026-07", ["1", "2"]), ("2026-08", ["1"])):
        for s in sids:
            conn.execute("INSERT INTO monthly_revenue (stock_id, ym, published_at, source, updated_at) "
                         "VALUES (?,?,'2026-09-10','t','x')", (s, ym))
    _, issues = rv.check(conn)
    assert len(issues) == 1 and "2026-08" in issues[0]
