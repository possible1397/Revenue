from datetime import date

from collectors import financials as fin
from core import db

HTML = """<table><tr><td>上市公司第二季資料</td></tr></table>
<table><thead><tr><th>公司 代號</th><th>公司名稱</th><th>營業收入</th><th>營業利益（損失）</th><th>基本每股盈餘（元）</th></tr></thead>
<tbody><tr><td>2330</td><td>台積電</td><td>1266154378</td><td>535573848</td><td>18.25</td></tr>
<tr><td>1101</td><td>台泥</td><td>--</td><td>-5</td><td>-0.1</td></tr></tbody></table>
<table><thead><tr><th>公司 代號</th><th>公司名稱</th><th>利息淨收益</th><th>本期稅後淨利（淨損）</th></tr></thead>
<tbody><tr><td>2801</td><td>彰銀</td><td>11063307</td><td>7443997</td></tr></tbody></table>"""


def test_parse_mops_html_maps_items_and_skips_dashes():
    recs = set(fin.parse_mops_html(HTML, 113, 2))
    assert ("2330", "2024Q2", "Revenue", 1266154378.0) in recs
    assert ("2330", "2024Q2", "EPS", 18.25) in recs
    assert ("1101", "2024Q2", "OperatingIncome", -5.0) in recs
    assert not any(r[0] == "1101" and r[2] == "Revenue" for r in recs)           # '--' 不寫入
    assert ("2801", "2024Q2", "NetIncome", 7443997.0) in recs                    # 金融業也有淨利
    assert not any(r[1] != "2024Q2" for r in recs)


def test_parse_openapi_twse_and_tpex_keys():
    twse = [{"年度": "115", "季別": "3", "公司代號": "1215", "營業收入": "21866426.00", "基本每股盈餘（元）": "2.1"}]
    tpex = [{"Year": "115", "Season": "3", "SecuritiesCompanyCode": "1570", "營業收入": "385478.00"},
            {"Year": "115", "Season": "3", "SecuritiesCompanyCode": "00679B", "營業收入": "1"}]
    assert ("1215", "2026Q3", "EPS", 2.1) in fin.parse_openapi(twse)
    out = fin.parse_openapi(tpex)
    assert out == [("1570", "2026Q3", "Revenue", 385478.0)]                      # 非 4 碼股票略過


def test_deadline_and_periods():
    assert fin.deadline("2024Q2") == "2024-08-14" and fin.deadline("2024Q4") == "2025-03-31"
    ps = fin.periods_since("2025Q1", today=date(2025, 11, 5))
    assert ps == ["2025Q1", "2025Q2"]                                           # Q3 期限 11/14 尚未到
    assert fin.periods_since("2025Q1", today=date(2025, 11, 20))[-1] == "2025Q3"


def test_upsert_keeps_earliest_published_and_counts_revisions(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    r = [("1", "2026Q3", "Revenue", 100.0)]
    assert fin.upsert(conn, r, "mops_html", "2026-11-14") == (1, 0)
    assert fin.upsert(conn, r, "twse_openapi", "2026-11-05") == (0, 0)           # 更早的實際日期取代估計
    assert fin.upsert(conn, [("1", "2026Q3", "Revenue", 110.0)], "twse_openapi", "2026-11-20") == (0, 1)
    v, pub = conn.execute("SELECT value, published_at FROM financials").fetchone()
    assert v == 110.0 and pub == "2026-11-05"


def test_valuation_latest_filed_and_rolling():
    import numpy as np
    import pandas as pd
    from analysis import valuation as v
    assert v.latest_filed("2026-05-11") == (2025, 4)       # Q1 期限 5/15 尚未到
    assert v.latest_filed("2026-11-11") == (2026, 2)       # Q3 期限 11/14 尚未到
    assert v.latest_filed("2026-11-14") == (2026, 3)
    assert v.latest_filed("2026-03-31") == (2025, 4)
    pe = pd.DataFrame({"a": np.arange(1.0, 41.0)})
    med, p25, p75, n = v.rolling_pe_stats(pe)
    assert np.isnan(med["a"].iloc[17]) and not np.isnan(med["a"].iloc[18])          # 前面不足 18 筆不算
    assert n["a"].iloc[39] == 36 and med["a"].iloc[39] == pd.Series(np.arange(4.0, 40.0)).median()   # 只用「之前」36 筆
