"""
P0 資料源探測腳本：只讀公開資料，不寫入任何資料庫。
用法（在你的 Windows 電腦）：
    pip install requests
    python p0_probe.py
可選：設定環境變數 FINMIND_TOKEN 以測試登入後的額度。
產出：p0_probe_report.md、p0_probe_report.json（同資料夾）
"""
import json, os, time, io
from datetime import datetime
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) revenue-p0-probe"}
TOKEN = os.environ.get("FINMIND_TOKEN", "")
results = []


def get(name, url, params=None, headers=None, timeout=30, as_json=True, sleep=1.0):
    r = {"name": name, "url": url, "params": params}
    t0 = time.time()
    try:
        resp = requests.get(url, params=params, headers={**UA, **(headers or {})}, timeout=timeout)
        r["status"] = resp.status_code
        r["bytes"] = len(resp.content)
        r["seconds"] = round(time.time() - t0, 2)
        r["content_type"] = resp.headers.get("Content-Type", "")
        if as_json and resp.status_code == 200:
            try:
                r["json"] = resp.json()
            except Exception as e:
                r["json_error"] = str(e)
        else:
            r["text_head"] = resp.content[:300].decode("utf-8", errors="replace")
        r["_resp"] = resp
    except Exception as e:
        r["error"] = f"{type(e).__name__}: {e}"
    results.append(r)
    time.sleep(sleep)
    return r


def summarize_rows(rows):
    if not isinstance(rows, list) or not rows:
        return {"rows": 0}
    return {"rows": len(rows), "fields": list(rows[0].keys()), "sample": rows[0]}


# 1. TWSE OpenAPI 上市月營收（只有最新一個月）
a = get("TWSE 上市月營收 t187ap05_L", "https://openapi.twse.com.tw/v1/opendata/t187ap05_L")
if "json" in a:
    a["summary"] = summarize_rows(a["json"])
    a["months"] = sorted({x.get("資料年月") for x in a["json"]})

# 2. TPEx OpenAPI 上櫃月營收
b = get("TPEx 上櫃月營收 mopsfin_t187ap05_O", "https://openapi.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O")
if "json" in b:
    b["summary"] = summarize_rows(b["json"])
    b["months"] = sorted({x.get("資料年月", x.get("Date", "")) for x in b["json"]})[:5]

# 3. MOPS 歷史月營收彙總 HTML（上市 sii、上櫃 otc；民國年_月）
for market in ("sii", "otc"):
    for roc, m in ((110, 1), (113, 6), (115, 9)):
        u = f"https://mops.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{m}.html"
        r = get(f"MOPS 歷史月營收 {market} {roc}/{m}", u, as_json=False, sleep=2.0)
        if r.get("status") == 200:
            raw = r["_resp"].content
            for enc in ("big5", "utf-8"):
                try:
                    txt = raw.decode(enc, errors="strict")
                    r["encoding"] = enc
                    r["has_table"] = "<table" in txt.lower()
                    r["row_count_est"] = txt.lower().count("<tr")
                    break
                except Exception:
                    continue

# 4. FinMind
fm = "https://api.finmindtrade.com/api/v4"
hdr = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
u = get("FinMind user_info", "https://api.web.finmindtrade.com/v2/user_info", headers=hdr)
c = get("FinMind 月營收 2330（2015起）", f"{fm}/data", {"dataset": "TaiwanStockMonthRevenue", "data_id": "2330", "start_date": "2015-01-01"}, hdr)
if "json" in c:
    rows = c["json"].get("data", [])
    c["summary"] = summarize_rows(rows)
    c["first_date"] = rows[0]["date"] if rows else None
    c["last_date"] = rows[-1]["date"] if rows else None
d = get("FinMind 全市場月營收（不帶 data_id）", f"{fm}/data", {"dataset": "TaiwanStockMonthRevenue", "start_date": "2026-09-01", "end_date": "2026-09-30"}, hdr)
if "json" in d:
    d["api_msg"] = d["json"].get("msg")
    d["summary"] = summarize_rows(d["json"].get("data", []))
e = get("FinMind 資產負債表 2330", f"{fm}/data", {"dataset": "TaiwanStockBalanceSheet", "data_id": "2330", "start_date": "2025-01-01"}, hdr)
if "json" in e:
    rows = e["json"].get("data", [])
    e["summary"] = summarize_rows(rows)
    types = sorted({x.get("type") for x in rows})
    e["types_total"] = len(types)
    e["types_match"] = [t for t in types if any(k in str(t) for k in ("Contract", "Inventor", "Prepay", "合約", "存貨", "預付"))]
    e["types_all"] = types
f = get("FinMind 損益表 2330", f"{fm}/data", {"dataset": "TaiwanStockFinancialStatements", "data_id": "2330", "start_date": "2025-01-01"}, hdr)
if "json" in f:
    f["summary"] = summarize_rows(f["json"].get("data", []))
    f["types_all"] = sorted({x.get("type") for x in f["json"].get("data", [])})

# 5. 法說會（MOPS 法說會一覽）— 只看是否可連線
get("MOPS 法說會一覽（可連線性）", "https://mops.twse.com.tw/mops/web/t100sb07_1", as_json=False)

# 輸出報告
for r in results:
    r.pop("_resp", None)
    if "json" in r:
        j = r.pop("json")
        r["json_kind"] = type(j).__name__
with open("p0_probe_report.json", "w", encoding="utf-8") as fh:
    json.dump(results, fh, ensure_ascii=False, indent=2, default=str)

lines = [f"# P0 資料源探測報告\n\n產生時間：{datetime.now():%Y-%m-%d %H:%M}\n"]
for r in results:
    lines.append(f"## {r['name']}\n")
    lines.append(f"- 狀態：{r.get('status', r.get('error'))}，大小：{r.get('bytes')} bytes，耗時：{r.get('seconds')} 秒")
    for k in ("months", "first_date", "last_date", "api_msg", "encoding", "has_table", "row_count_est", "types_total", "types_match"):
        if k in r:
            lines.append(f"- {k}：{r[k]}")
    s = r.get("summary")
    if s:
        lines.append(f"- 筆數：{s['rows']}")
        if s.get("fields"):
            lines.append(f"- 欄位：{s['fields']}")
    lines.append("")
with open("p0_probe_report.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines))
print("完成，請把 p0_probe_report.md 與 p0_probe_report.json 留在此資料夾。")
