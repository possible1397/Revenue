from datetime import date
from jobs import daily


def names(d, force=False):
    return [n for n, _ in daily.steps(d, force)]


def test_weekly_steps():
    assert names(date(2026, 11, 2)) == ["revenue", "shares", "prices", "metrics", "screen"]            # 週一
    assert names(date(2026, 11, 3)) == ["revenue", "prices", "metrics", "screen"]                      # 週二
    assert names(date(2026, 11, 1)) == ["revenue", "recheck", "prices", "metrics", "screen"]           # 週日
    assert "recheck" in names(date(2026, 11, 3), force=True) and "shares" in names(date(2026, 11, 3), force=True)


def test_failure_does_not_stop_later_steps(monkeypatch, tmp_path):
    ran = []

    def boom(cfg, conn):
        ran.append("boom")
        raise RuntimeError("x")

    monkeypatch.setattr(daily, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(daily, "steps", lambda d, f=False: [("a", boom), ("b", lambda c, k: ran.append("b"))])
    monkeypatch.setattr(daily.db, "connect", lambda: __import__("sqlite3").connect(":memory:"))
    assert daily.main([]) == 1
    assert ran == ["boom", "b"]
    assert not (tmp_path / "lock").exists()    # 鎖已釋放


def test_lock_blocks_second_run(monkeypatch, tmp_path):
    monkeypatch.setattr(daily, "LOCK", tmp_path / "lock")
    (tmp_path / "lock").write_text("1")
    assert daily.main([]) == 2
