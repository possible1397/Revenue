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
LIST_NAMES = {"growth_unpriced": "成長未反映", "growth_starting": "成長將開始"}
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
def load_dates(_mt):
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT DISTINCT as_of FROM screens ORDER BY as_of DESC")]


@st.cache_data(show_spinner=False)
def load_screen(as_of, _mt):
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
def load_revenue(stock_id, _mt):
    with _conn() as c:
        return pd.read_sql_query(
            "SELECT ym, revenue, revenue_last_year, yoy FROM monthly_revenue WHERE stock_id=? ORDER BY ym",
            c, params=(stock_id,))


@st.cache_data(show_spinner=False)
def load_metric(stock_id, name, _mt):
    with _conn() as c:
        return pd.read_sql_query("SELECT as_of AS ym, value FROM metrics WHERE stock_id=? AND name=? ORDER BY as_of",
                                 c, params=(stock_id, name))


@st.cache_data(show_spinner=False)
def load_prices(stock_id, _mt):
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


def main():
    st.title("月營收成長清單")
    dates = load_dates(_mtime())
    if not dates:
        st.warning("尚無篩選結果，請先執行 `python -m analysis.screen`。")
        return

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
        min_score = st.slider("最低分數", 0, 100, 0)

    d = df[df["market"].isin(markets) & (df["score"] >= min_score)]
    d = d[d["market_cap_yi"].fillna(0).between(*cap) | d["market_cap_yi"].isna()]
    if inds:
        d = d[d["industry"].isin(inds)]
    if q:
        d = d[d["stock_id"].str.contains(q, case=False) | d["name"].fillna("").str.contains(q, case=False)]
    d = d.sort_values("score", ascending=False).reset_index(drop=True)

    st.caption(f"{LIST_NAMES[list_type]}：{len(d)} 檔（資料日期 {as_of}；營收月 "
               f"{d['ym'].max() if len(d) else '-'}）。分數僅供排序，入選條件見 analysis/screen.py。")

    cols = {"stock_id": "代號", "name": "名稱", "industry": "產業", "market_cap_yi": "市值(億)",
            "revenue_yi": "單月營收(億)", "yoy": "YoY%", "yoy_3m": "3M YoY%", "cum_yoy": "累計YoY%",
            "consec_growth": "連續成長月", "ret_3m": "3M股價%", "gap": "差距(百分點)", "score": "分數"}
    show = d[list(cols)].rename(columns=cols)
    event = st.dataframe(
        show, hide_index=True, width="stretch", height=380, on_select="rerun", selection_mode="single-row",
        column_config={
            "市值(億)": st.column_config.NumberColumn(format="%.0f"),
            "單月營收(億)": st.column_config.NumberColumn(format="%.2f"),
            "YoY%": st.column_config.NumberColumn(format="%.1f"),
            "3M YoY%": st.column_config.NumberColumn(format="%.1f"),
            "累計YoY%": st.column_config.NumberColumn(format="%.1f"),
            "連續成長月": st.column_config.NumberColumn(format="%d"),
            "3M股價%": st.column_config.NumberColumn(format="%.1f"),
            "差距(百分點)": st.column_config.NumberColumn(format="%.1f"),
            "分數": st.column_config.ProgressColumn(format="%.1f", min_value=0, max_value=100),
        })

    rows = event.selection.rows if event and event.selection else []
    if len(d) == 0:
        return
    sel = d.iloc[rows[0]] if rows else d.iloc[0]
    st.divider()
    st.subheader(f"{sel['stock_id']} {sel['name']}　{sel['industry'] or ''}")
    if not rows:
        st.caption("尚未選取，預設顯示第一檔；點選表格左側的列可切換。")
    left, right = st.columns([3, 2])
    with left:
        revenue_charts(sel["stock_id"])
    with right:
        price_chart(sel["stock_id"])
        st.markdown("**分數組成**")
        st.write(f"基本面動能 {sel['s_fundamental']:.0f}　領先訊號 {sel['s_leading']:.0f}　市場尚未反映 {sel['s_unpriced']:.0f}")
        flags = [n for n, k in (("成長加速", "accel"), ("營收拐點", "turnaround"), ("產業擴散", "diffusion"),
                                ("創 12 個月新高", "high_12")) if sel.get(k) == 1]
        st.write("訊號：" + ("、".join(flags) if flags else "無"))


main()
