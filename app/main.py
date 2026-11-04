"""月營收成長清單儀表板（Streamlit）。

啟動：.venv\\Scripts\\streamlit run app\\main.py
資料來源：data/revenue.db 的 screens / monthly_revenue / prices（由 jobs 每日更新，儀表板只讀不寫）。
"""
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.config import load_config  # noqa: E402

BLUE = "#3987e5"      # 類別色 1（亮暗背景皆可用）
ORANGE = "#eb6834"    # 類別色 2
LIST_NAMES = {"growth_unpriced": "月度成長股", "growth_starting": "成長將開始"}
K_TO_YI = 1e5         # 千元 -> 億元

st.set_page_config(page_title="月營收成長清單", layout="wide")


@st.cache_resource
def _db_path():
    p = Path(load_config()["db_path"])
    return p if p.is_absolute() else ROOT / p


def _conn():
    c = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _mtime():
    return _db_path().stat().st_mtime


@st.cache_data(show_spinner=False)
def load_dates(mt):
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT as_of FROM screens ORDER BY as_of DESC")]


@st.cache_data(show_spinner=False)
def load_screen(as_of, mt):
    with _conn() as c:
        rows = c.execute(
            "SELECT s.list_type, s.stock_id, s.score, s.reasons, c.name, c.industry, c.market "
            "FROM screens s LEFT JOIN companies c USING(stock_id) WHERE s.as_of=?", (as_of,)).fetchall()
    out = []
    for r in rows:
        d = json.loads(r["reasons"])
        d.update(list_type=r["list_type"], stock_id=r["stock_id"], score=r["score"],
                 name=r["name"], industry=r["industry"], market=r["market"])
        out.append(d)
    df = pd.DataFrame(out)
    if not df.empty:
        df["market_cap_yi"] = df["market_cap"] / K_TO_YI
        df["revenue_yi"] = df["revenue"] / K_TO_YI
    return df


@st.cache_data(show_spinner=False)
def load_revenue(stock_id, mt):
    with _conn() as c:
        return pd.read_sql_query(
            "SELECT ym, revenue, revenue_last_year, yoy FROM monthly_revenue WHERE stock_id=? ORDER BY ym",
            c, params=(stock_id,))


@st.cache_data(show_spinner=False)
def load_metric(stock_id, name, mt):
    with _conn() as c:
        return pd.read_sql_query("SELECT as_of AS ym, value FROM metrics WHERE stock_id=? AND name=? ORDER BY as_of",
                                 c, params=(stock_id, name))


@st.cache_data(show_spinner=False)
def load_prices(stock_id, mt):
    with _conn() as c:
        return pd.read_sql_query("SELECT date, close, adj_close FROM prices WHERE stock_id=? ORDER BY date",
                                 c, params=(stock_id,))


def _theme():
    try:
        dark = st.context.theme.type == "dark"
    except Exception:
        dark = False
    return {"font": "#f0efec" if dark else "#0b0b0b", "grid": "rgba(128,128,128,0.25)"}


def _layout(fig, th, height):
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", font=dict(color=th["font"]), hovermode="x unified",
                      legend=dict(orientation="h", y=1.12, x=0))
    fig.update_xaxes(showgrid=False, linecolor=th["grid"])
    fig.update_yaxes(gridcolor=th["grid"], zeroline=False)
    return fig


def revenue_charts(stock_id, months=36):
    mt = _mtime()
    rev, m3 = load_revenue(stock_id, mt), load_metric(stock_id, "yoy_3m", mt)
    if rev.empty:
        st.info("沒有營收資料")
        return
    rev = rev.tail(months).merge(m3.rename(columns={"value": "yoy_3m"}), on="ym", how="left")
    th = _theme()

    st.markdown("**單月營收（億元）**")
    fig = go.Figure(go.Bar(x=rev["ym"], y=rev["revenue"] / K_TO_YI, name="單月營收", marker_color=BLUE,
                           hovertemplate="%{y:,.2f} 億元<extra>單月營收</extra>"))
    fig.update_layout(bargap=0.35, showlegend=False)
    st.plotly_chart(_layout(fig, th, 260), width="stretch")

    st.markdown("**年增率（%）**")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=rev["ym"], y=rev["yoy"], name="單月 YoY", mode="lines",
                             line=dict(color=BLUE, width=2), hovertemplate="%{y:.1f}%<extra>單月 YoY</extra>"))
    fig.add_trace(go.Scatter(x=rev["ym"], y=rev["yoy_3m"], name="3 個月滾動 YoY", mode="lines",
                             line=dict(color=ORANGE, width=2), hovertemplate="%{y:.1f}%<extra>3 個月滾動 YoY</extra>"))
    fig.add_hline(y=0, line_color=th["grid"], line_width=1)
    st.plotly_chart(_layout(fig, th, 260), width="stretch")

    with st.expander("資料表"):
        t = rev.rename(columns={"ym": "營收月", "revenue": "單月營收(千元)", "revenue_last_year": "去年同月(千元)",
                                "yoy": "單月YoY(%)", "yoy_3m": "3M YoY(%)"})
        st.dataframe(t.sort_values("營收月", ascending=False), hide_index=True, width="stretch")


def price_chart(stock_id):
    px = load_prices(stock_id, _mtime())
    if px.empty:
        st.info("沒有股價資料")
        return
    px = px.tail(500)
    th = _theme()
    st.markdown("**還原股價（近 2 年）**")
    fig = go.Figure(go.Scatter(x=px["date"], y=px["adj_close"], mode="lines", line=dict(color=BLUE, width=2),
                               name="還原收盤價", hovertemplate="%{y:,.2f}<extra>還原收盤價</extra>"))
    fig.update_layout(showlegend=False)
    st.plotly_chart(_layout(fig, th, 240), width="stretch")



@st.cache_data(show_spinner=False)
def load_events(stock_id, mt):
    with _conn() as c:
        return pd.read_sql_query(
            "SELECT ts, type, title, url, body FROM events WHERE stock_id=? ORDER BY ts DESC LIMIT 30",
            c, params=(stock_id,))


def _go(view, stock=None):
    st.session_state["view"] = view
    st.session_state["stock"] = stock
    st.session_state["nonce"] = st.session_state.get("nonce", 0) + 1   # 換掉表格的 key，清掉殘留的選取


def _topbar():
    c1, c2, _ = st.columns([1, 1, 6])
    if c1.button("首頁", width="stretch"):
        _go("home")
        st.rerun()
    if c2.button("完整清單", width="stretch"):
        _go("list")
        st.rerun()


def _rank_table(title, hint, d, col, label, fmt, key):
    st.markdown(f"**{title}**")
    st.caption(hint)
    t = d.sort_values(col, ascending=False).head(10).reset_index(drop=True)
    show = pd.DataFrame({"公司": t["stock_id"] + " " + t["name"].fillna(""), "產業": t["industry"], label: t[col]})
    top = float(max(t[col].max(), 1))
    ev = st.dataframe(
        show, hide_index=True, width="stretch", height=390, on_select="rerun", selection_mode="single-row",
        key=f"{key}_{st.session_state.get('nonce', 0)}",
        column_config={label: st.column_config.ProgressColumn(format=fmt, min_value=0, max_value=top)})
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        _go("detail", t.iloc[rows[0]]["stock_id"])
        st.rerun()


def home_view():
    dates = load_dates(_mtime())
    as_of = dates[0]
    df = load_screen(as_of, _mtime())
    g = df[df["list_type"] == "growth_unpriced"].copy()
    s = df[df["list_type"] == "growth_starting"]
    g["rev_add_yi"] = (g["revenue"] - g["revenue"] / (1 + g["yoy"] / 100)) / K_TO_YI
    prev = load_screen(dates[1], _mtime()) if len(dates) > 1 else pd.DataFrame()
    new_n = None
    if not prev.empty:
        new_n = len(set(g["stock_id"]) - set(prev[prev["list_type"] == "growth_unpriced"]["stock_id"]))

    st.title("月營收成長追蹤")
    ym = g["ym"].max() if len(g) else "-"
    m = st.columns(4)
    m[0].metric("月度成長股", f"{len(g)} 檔", help="近 3 個月營收年增 ≥ 20%、連續成長 ≥ 3 個月、單月營收 ≥ 1 億、非低基期")
    m[1].metric("較上次新入選", f"{new_n} 檔" if new_n is not None else "-", help=f"與 {dates[1]} 的清單比較" if len(dates) > 1 else None)
    m[2].metric("成長將開始", f"{len(s)} 檔", help="營收拐點或多個領先訊號出現，尚未達成長股門檻")
    m[3].metric("營收月份 / 資料日期", f"{ym}", f"{as_of}", delta_color="off")
    st.caption("點選任一列即可查看該公司詳細資料。")

    a, b, c = st.columns(3)
    with a:
        _rank_table("成長最快", "近 3 個月營收年增率（%）", g, "yoy_3m", "3M YoY%", "%.0f", "rk_fast")
    with b:
        _rank_table("營收增加最多", "單月營收比去年同月多出多少（億元）", g, "rev_add_yi", "增加(億)", "%.1f", "rk_add")
    with c:
        _rank_table("連續成長最久", "營收年增率連續為正的月數", g, "consec_growth", "連續月數", "%d", "rk_long")

    st.divider()
    st.markdown("**月度成長股的產業分布**")
    cnt = g.groupby(g["industry"].fillna("未分類")).size().sort_values().tail(12)
    th = _theme()
    fig = go.Figure(go.Bar(x=cnt.values, y=cnt.index, orientation="h", marker_color=BLUE,
                           hovertemplate="%{y}：%{x} 檔<extra></extra>"))
    fig.update_layout(bargap=0.35, showlegend=False)
    st.plotly_chart(_layout(fig, th, 380), width="stretch")
    st.caption("只顯示檔數最多的 12 個產業。")


def detail_view():
    sid = st.session_state.get("stock")
    df = load_screen(load_dates(_mtime())[0], _mtime())
    row = df[df["stock_id"] == sid]
    if st.button("← 返回"):
        _go("home")
        st.rerun()
    if row.empty:
        st.warning("這檔不在最新清單內。")
        return
    r = row.iloc[0]
    st.title(f"{r['stock_id']} {r['name']}")
    st.caption(f"{r['industry'] or '未分類'}　{'上市' if r['market'] == 'sii' else '上櫃'}　"
               f"{LIST_NAMES.get(r['list_type'], '')}　營收月 {r['ym']}")
    m = st.columns(5)
    m[0].metric("單月營收", f"{r['revenue_yi']:.2f} 億")
    m[1].metric("單月 YoY", f"{r['yoy']:.1f}%")
    m[2].metric("3M YoY", f"{r['yoy_3m']:.1f}%")
    m[3].metric("連續成長", f"{int(r['consec_growth'])} 個月")
    m[4].metric("市值（估）", f"{r['market_cap_yi']:.0f} 億" if pd.notna(r["market_cap_yi"]) else "-")
    flags = [n for n, k in (("成長加速", "accel"), ("營收拐點", "turnaround"), ("產業擴散", "diffusion"),
                            ("創 12 個月新高", "high_12")) if r.get(k) == 1]
    st.write("訊號：" + ("、".join(flags) if flags else "無"))

    left, right = st.columns([3, 2])
    with left:
        revenue_charts(sid)
    with right:
        price_chart(sid)
    st.markdown("**近期重大訊息**")
    ev = load_events(sid, _mtime())
    if ev.empty:
        st.caption("尚無紀錄（重大訊息從 2026-11-01 才開始累積）。")
    for _, e in ev.iterrows():
        with st.expander(f"{e['ts'][:10]}　{e['title'].replace(chr(13), '').replace(chr(10), ' ')[:60]}"):
            st.text(e["body"] or "")


def list_view():
    dates = load_dates(_mtime())
    with st.sidebar:
        st.header("篩選")
        as_of = st.selectbox("資料日期", dates)
        list_type = st.radio("清單", list(LIST_NAMES), format_func=LIST_NAMES.get)
        df = load_screen(as_of, _mtime())
        df = df[df["list_type"] == list_type]
        q = st.text_input("代號或名稱")
        markets = st.multiselect("市場", ["sii", "otc"], default=["sii", "otc"],
                                 format_func=lambda m: "上市" if m == "sii" else "上櫃")
        inds = st.multiselect("產業", sorted(df["industry"].dropna().unique()))
        cap_max = float(df["market_cap_yi"].max()) if df["market_cap_yi"].notna().any() else 1000.0
        cap = st.slider("市值（億元）", 0.0, max(cap_max, 1.0), (0.0, max(cap_max, 1.0)))

    d = df[df["market"].isin(markets)]
    d = d[d["market_cap_yi"].fillna(0).between(*cap) | d["market_cap_yi"].isna()]
    if inds:
        d = d[d["industry"].isin(inds)]
    if q:
        d = d[d["stock_id"].str.contains(q, case=False) | d["name"].fillna("").str.contains(q, case=False)]
    d = d.sort_values("yoy_3m", ascending=False).reset_index(drop=True)

    st.title("完整清單")
    st.caption(f"{LIST_NAMES[list_type]}：{len(d)} 檔（資料日期 {as_of}）。預設依 3M YoY 排序，點欄位標題可改排序；"
               "點選一列查看詳細資料。")
    cols = {"stock_id": "代號", "name": "名稱", "industry": "產業", "market_cap_yi": "市值(億)",
            "revenue_yi": "單月營收(億)", "yoy": "YoY%", "yoy_3m": "3M YoY%", "cum_yoy": "累計YoY%",
            "consec_growth": "連續成長月", "ret_3m": "3M股價%"}
    show = d[list(cols)].rename(columns=cols)
    ev = st.dataframe(
        show, hide_index=True, width="stretch", height=600, on_select="rerun", selection_mode="single-row",
        key=f"list_{st.session_state.get('nonce', 0)}",
        column_config={
            "市值(億)": st.column_config.NumberColumn(format="%.0f"),
            "單月營收(億)": st.column_config.NumberColumn(format="%.2f"),
            "YoY%": st.column_config.NumberColumn(format="%.1f"),
            "3M YoY%": st.column_config.NumberColumn(format="%.1f"),
            "累計YoY%": st.column_config.NumberColumn(format="%.1f"),
            "連續成長月": st.column_config.NumberColumn(format="%d"),
            "3M股價%": st.column_config.NumberColumn(format="%.1f"),
        })
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        _go("detail", d.iloc[rows[0]]["stock_id"])
        st.rerun()


def main():
    if not load_dates(_mtime()):
        st.warning("尚無篩選結果，請先執行 `python -m analysis.screen`。")
        return
    view = st.session_state.get("view", "home")
    if view != "detail":
        _topbar()
    {"home": home_view, "list": list_view, "detail": detail_view}[view]()


main()
