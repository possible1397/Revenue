from collectors import delisted as dl


def _fm(year, month, rev):
    return {"revenue_year": year, "revenue_month": month, "revenue": rev * 1000}


def test_roc_slash():
    assert dl.roc_slash_to_iso("115/09/01") == "2026-09-01"


def test_build_revenue_rows_yoy_and_cum():
    rows = [_fm(2020, m, 100) for m in range(1, 13)] + [_fm(2021, m, 100) for m in range(1, 13)] \
        + [_fm(2022, 1, 150), _fm(2022, 2, 200)]
    out = {r["ym"]: r for r in dl.build_revenue_rows("X", rows)}
    assert "2021-10" not in out and "2021-11" in out                  # 只輸出視窗內月份
    assert round(out["2022-01"]["yoy"], 2) == 50.0                    # 150 / 100 - 1
    assert out["2022-01"]["revenue_last_year"] == 100 and out["2022-01"]["revenue_prev_month"] == 100
    assert out["2022-02"]["cum_revenue"] == 350 and out["2022-02"]["cum_last_year"] == 200
    assert round(out["2022-02"]["cum_yoy"], 2) == 75.0


def test_missing_month_gives_no_yoy():
    rows = [_fm(2021, m, 100) for m in range(1, 13) if m != 5] + [_fm(2022, 5, 120)]
    out = {r["ym"]: r for r in dl.build_revenue_rows("X", rows)}
    assert out["2022-05"]["yoy"] is None      # 去年同月缺資料，不亂補


def test_adjust_prices():
    px = [("2022-01-03", 100.0, 10), ("2022-07-27", 52.5, 10), ("2022-08-01", 53.0, 10)]
    ev = [("2022-07-27", 56.5, 52.5)]         # 除息：參考價 56.5 -> 52.5
    adj = dl.adjust_prices(px, ev)
    assert round(adj[0][2], 2) == round(100 * 52.5 / 56.5, 2)     # 除息日前的價格往下調整
    assert adj[1][2] == 52.5 and adj[2][2] == 53.0               # 除息日當天與之後不變
