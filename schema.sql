-- 台股營收成長追蹤系統 SQLite schema（P0 草案，待 Terry 驗收）
-- 金額單位：千元（與 MOPS 月營收一致）。日期：ym 用 'YYYY-MM'（西元），其餘用 ISO 8601。
-- 所有事實表帶 published_at，回測只能使用公告日當天已知的資料。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS companies (
    stock_id      TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    market        TEXT NOT NULL CHECK (market IN ('sii','otc')),
    industry      TEXT,
    listed_date   TEXT,
    delisted_date TEXT,              -- 下市公司要保留，回測避免存活者偏誤
    shares        INTEGER,           -- 已發行普通股數（目前值，用來估算市值）
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS monthly_revenue (
    stock_id           TEXT NOT NULL,
    ym                 TEXT NOT NULL,   -- 營收所屬月份
    revenue            INTEGER,         -- 千元
    revenue_prev_month INTEGER,
    revenue_last_year  INTEGER,
    mom                REAL,            -- %
    yoy                REAL,            -- %
    cum_revenue        INTEGER,
    cum_last_year      INTEGER,
    cum_yoy            REAL,
    note               TEXT,
    published_at       TEXT NOT NULL,   -- 回補資料為估計值（次月10日），增量資料為實際抓取日
    published_is_estimate INTEGER NOT NULL DEFAULT 0,
    source             TEXT NOT NULL,   -- twse_openapi / tpex_openapi / mops_html / finmind
    updated_at         TEXT NOT NULL,
    PRIMARY KEY (stock_id, ym)
);

-- 更正值覆寫前，舊值寫入這裡
CREATE TABLE IF NOT EXISTS revenue_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id     TEXT NOT NULL,
    ym           TEXT NOT NULL,
    revenue      INTEGER,
    revenue_last_year INTEGER,
    source       TEXT,
    superseded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_revhist ON revenue_history (stock_id, ym);

CREATE TABLE IF NOT EXISTS financials (
    stock_id     TEXT NOT NULL,
    period       TEXT NOT NULL,   -- 如 2026Q3
    type         TEXT NOT NULL,   -- income / balance / cashflow
    item         TEXT NOT NULL,   -- 如 Revenue、Inventories、CurrentContractLiabilities
    value        REAL,
    published_at TEXT NOT NULL,
    source       TEXT NOT NULL,
    PRIMARY KEY (stock_id, period, type, item)
);

CREATE TABLE IF NOT EXISTS prices (
    stock_id   TEXT NOT NULL,
    date       TEXT NOT NULL,
    close      REAL,             -- 未還原收盤價
    adj_close  REAL,             -- 還原收盤價（含除權息，用來算報酬）
    volume     INTEGER,
    market_cap REAL,             -- 千元；close × 目前股數估算
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS metrics (
    stock_id TEXT NOT NULL,
    as_of    TEXT NOT NULL,
    name     TEXT NOT NULL,
    value    REAL,
    PRIMARY KEY (stock_id, as_of, name)
);

CREATE TABLE IF NOT EXISTS screens (
    as_of     TEXT NOT NULL,
    list_type TEXT NOT NULL CHECK (list_type IN ('growth_unpriced','growth_starting')),
    stock_id  TEXT NOT NULL,
    score     REAL,
    reasons   TEXT,               -- JSON
    PRIMARY KEY (as_of, list_type, stock_id)
);

-- 重訊只有當天資料，必須每日累積
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id  TEXT,
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,      -- investor_conf / material_info / news
    title     TEXT,
    url       TEXT,
    body      TEXT,
    dedup_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_stock_ts ON events (stock_id, ts);

CREATE TABLE IF NOT EXISTS ai_analysis (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id   TEXT NOT NULL,
    as_of      TEXT NOT NULL,
    task       TEXT NOT NULL CHECK (task IN ('A1','A2','A3')),
    model      TEXT,
    input_hash TEXT,
    output     TEXT,              -- JSON
    sources    TEXT,              -- JSON
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supply_chain (
    industry TEXT NOT NULL,
    stage    TEXT NOT NULL CHECK (stage IN ('上游','中游','下游')),
    stock_id TEXT NOT NULL,
    PRIMARY KEY (industry, stage, stock_id)
);

CREATE TABLE IF NOT EXISTS job_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job         TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT,             -- running / ok / failed
    rows        INTEGER,
    error       TEXT
);
