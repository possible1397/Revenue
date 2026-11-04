"""儀表板煙霧測試：用真實 data/revenue.db 跑首頁、完整清單、個股詳情，確認沒有例外。沒有資料庫時略過。"""
from pathlib import Path
import pytest

DB = Path(__file__).resolve().parent.parent / "data" / "revenue.db"
APP = str(Path(__file__).resolve().parent.parent / "app" / "main.py")


@pytest.mark.skipif(not DB.exists(), reason="沒有 data/revenue.db")
def test_app_views_run_without_exception():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=90).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.metric) == 4
    at.session_state["view"] = "list"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    at.sidebar.radio[0].set_value("growth_starting").run()
    assert not at.exception, [e.value for e in at.exception]
    at.session_state["view"], at.session_state["stock"] = "detail", "2330"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
