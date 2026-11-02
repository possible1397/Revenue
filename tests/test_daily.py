from datetime import date
from jobs import daily


def names(d, force=False):
    return [n for n, _ in daily.steps(d, force)]


def test_weekly_steps():
    assert names(date(2026, 11, 2)) == ["events", "revenue", "shares", "prices", "metrics", "screen"]            # 週一
    assert names(date(2026, 11, 3)) == ["events", "revenue", "prices", "metrics", "screen"]                      # 週二
    assert names(date(2026, 11, 1)) == ["events", "revenue", "recheck", "prices", "metrics", "screen"]           # 週日
    assert "recheck" in names(date(2026, 11, 3), force=True) and "shares" in names(date(2026, 11, 3), force=True)


def test_failure_does_not_stop_later_steps(monkeypatch, tmp_path):
    ran = []

    def boom(cfg, conn):
        ran.append("boom")
        raise RuntimeError("x")

    monkeypatch.setattr(daily, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(daily, "steps", lambda d, f=False: [("a", boom), ("b", lambda c, k: ran.append("b"))])
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE job_runs (id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, status TEXT, rows INTEGER, error TEXT)")
    monkeypatch.setattr(daily.db, "connect", lambda: conn)
    assert daily.main([]) == 1
    assert ran == ["boom", "b"]
    assert not (tmp_path / "lock").exists()    # 鎖已釋放


def test_lock_blocks_when_owner_alive(monkeypatch, tmp_path):
    monkeypatch.setattr(daily, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(daily, "_pid_alive", lambda pid: True)
    (tmp_path / "lock").write_text("1234")
    assert daily.main([]) == 2


def test_stale_lock_is_taken_over_and_interrupted_runs_closed(monkeypatch, tmp_path):
    import sqlite3
    monkeypatch.setattr(daily, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(daily, "_pid_alive", lambda pid: False)   # 上次的程序已被強制中斷
    (tmp_path / "lock").write_text("1234")
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE job_runs (id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, finished_at TEXT, status TEXT, rows INTEGER, error TEXT)")
    conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES ('shares_update','x','running')")
    monkeypatch.setattr(daily.db, "connect", lambda: conn)
    monkeypatch.setattr(daily, "steps", lambda d, f=False: [])
    assert daily.main([]) == 0
    assert conn.execute("SELECT status, error FROM job_runs").fetchone() == ("failed", "interrupted")
