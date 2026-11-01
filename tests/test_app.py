"""儀表板煙霧測試：用真實 data/revenue.db 跑一遍，確認沒有例外。沒有資料庫時略過。"""
from pathlib import Path
import pytest

DB = Path(__file__).resolve().parent.parent / "data" / "revenue.db"


@pytest.mark.skipif(not DB.exists(), reason="沒有 data/revenue.db")
def test_app_runs_without_exception():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "main.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    at.sidebar.radio[0].set_value("growth_starting").run()
    assert not at.exception, [e.value for e in at.exception]
