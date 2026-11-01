from collectors import events as ev
from core import db

TWSE = [{"出表日期": "1151031", "發言日期": "1151030", "發言時間": "70003", "公司代號": "2072", "公司名稱": "世紀能源",
         "主旨 ": "公告更名", "符合條款": "第11款", "事實發生日": "1151030", "說明": "更名說明"}]
TPEX = [{"Date": "1151031", "發言日期": "1151030", "發言時間": "133249", "SecuritiesCompanyCode": "6147",
         "CompanyName": "頎邦", "主旨": "第3季財報董事會決議", "符合條款": "第31款", "事實發生日": "1151030", "說明": "x"}]


def test_roc_ts():
    assert ev.roc_ts("1151030", "70003") == "2026-10-30T07:00:03"
    assert ev.roc_ts("1151030", "133249") == "2026-10-30T13:32:49"
    assert ev.roc_ts("bad", "1") is None


def test_parse_both_formats():
    a, b = ev.parse_rows(TWSE), ev.parse_rows(TPEX)
    assert a[0]["stock_id"] == "2072" and a[0]["title"] == "公告更名"   # 主旨欄位名稱結尾有空白
    assert b[0]["stock_id"] == "6147" and b[0]["ts"] == "2026-10-30T13:32:49"


def test_save_dedup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    e = ev.parse_rows(TWSE + TPEX)
    assert ev.save(conn, e) == 2
    assert ev.save(conn, e) == 0
