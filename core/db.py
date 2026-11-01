"""SQLite 連線與 schema 初始化、job_runs 紀錄。"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from .config import ROOT, load_config


def connect(db_path=None):
    p = Path(db_path or load_config()["db_path"])
    if not p.is_absolute():
        p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    return conn


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def job_run(conn, job):
    """記錄一次 job 執行；例外時標記 failed 並重新拋出。"""
    cur = conn.execute("INSERT INTO job_runs (job, started_at, status) VALUES (?,?,'running')", (job, now_iso()))
    run_id = cur.lastrowid
    conn.commit()
    info = {"rows": 0}
    try:
        yield info
        conn.execute("UPDATE job_runs SET finished_at=?, status='ok', rows=? WHERE id=?",
                     (now_iso(), info["rows"], run_id))
    except Exception as e:
        conn.rollback()
        conn.execute("UPDATE job_runs SET finished_at=?, status='failed', rows=?, error=? WHERE id=?",
                     (now_iso(), info["rows"], repr(e)[:500], run_id))
        raise
    finally:
        conn.commit()
