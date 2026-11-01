"""重大訊息收集（TWSE / TPEx OpenAPI）。

兩個端點都只提供「最新一個交易日」，沒有歷史，所以必須每天抓、自己累積；電腦關機的那天會永久漏掉。
寫入 events 表（type = material_info），以 dedup_key 去重，可重複執行。
"""
import hashlib
import sys
import logging
from datetime import datetime

from core.config import load_config
from core import db
from core.http import PoliteSession

log = logging.getLogger("events")


def _clean(row: dict) -> dict:
    return {k.strip(): v for k, v in row.items()}


def roc_ts(date_s, time_s):
    """民國 YYYMMDD + HHMMSS（可能少一位）-> ISO 時間字串；解析失敗回傳 None"""
    try:
        d = str(date_s).strip()
        y, m, dd = int(d[:-4]) + 1911, int(d[-4:-2]), int(d[-2:])
        t = str(time_s or "0").strip().zfill(6)
        return datetime(y, m, dd, int(t[:2]), int(t[2:4]), int(t[4:6])).isoformat()
    except (ValueError, TypeError):
        return None


def parse_rows(rows):
    """兩個端點的欄位名稱不同（TWSE 中文、TPEx 部分英文），統一成 events 的欄位。"""
    out = []
    for raw in rows:
        r = _clean(raw)
        sid = str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()
        ts = roc_ts(r.get("發言日期"), r.get("發言時間"))
        title = (r.get("主旨") or "").strip()
        if not sid or not ts or not title:
            continue
        body_parts = [f"符合條款：{r.get('符合條款', '').strip()}", f"事實發生日：{r.get('事實發生日', '').strip()}",
                      (r.get("說明") or "").strip()]
        key = hashlib.sha1(f"{sid}|{ts}|{title}".encode("utf-8")).hexdigest()
        out.append({"stock_id": sid, "ts": ts, "type": "material_info", "title": title,
                    "url": None, "body": "\n".join(p for p in body_parts if p), "dedup_key": key})
    return out


def save(conn, events):
    before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO events (stock_id, ts, type, title, url, body, dedup_key) "
        "VALUES (:stock_id, :ts, :type, :title, :url, :body, :dedup_key)", events)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] - before


def run_daily(conn, cfg):
    f = cfg["fetch"]
    sess = PoliteSession(f["user_agent"], f["min_interval_sec"], f["retries"], f["backoff_sec"], f["timeout_sec"])
    with db.job_run(conn, "events_material") as info:
        for key in ("twse_material", "tpex_material"):
            rows = sess.get(cfg["sources"][key]).json()
            ev = parse_rows(rows)
            new = save(conn, ev)
            log.info("%s：取得 %d 則、新增 %d 則", key, len(ev), new)
            info["rows"] += new


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    run_daily(db.connect(), load_config())
    sys.exit(0)
