# 台股營收成長追蹤系統 — 實作計畫（給 Claude CLI）

> 本檔是從規格書濃縮出來的執行版。若兩者衝突，以 Terry 最新的指示為準，並先詢問。
> 溝通一律使用**繁體中文**。程式註解、log、報告也用繁體中文。

## 0. 工作規則（先讀這段）

1. **按階段做**：一次只做一個階段（P0 → P1 → …），每階段完成後停下來，列出驗收結果給 Terry 確認，再進下一階段。
2. **先討論再實作**：遇到規格沒寫的決策（欄位取捨、門檻、套件選擇），先列選項與建議問 Terry，不要自己決定後做完。
3. **只在 `本專案目錄` 內寫檔。** 其他專案**唯讀**：可以讀取、參考、複製片段到本專案，不可修改。複製時在檔頭註明來源檔與日期。
4. **金鑰與帳密**放 `.env`（已加入 `.gitignore`），不寫進程式、不 commit。提供 `.env.example`。
5. **不寫入個人資料**：其他專案的持股紀錄檔不要讀、不要複製。
6. 網路抓取要有禮貌：每次請求間隔 ≥ 1 秒（MOPS ≥ 2 秒）、有重試與退避、有 User-Agent、遵守 robots.txt（櫃買中心 openapi.tpex.org.tw 對爬蟲封鎖，若是用程式批量抓取先和 Terry 確認用途與替代來源）。
7. 數字一律由程式計算；LLM 只做解讀與分類，不算數字，也不挑股票。
8. 每個模組要能**單獨重跑**、**可重複執行（idempotent）**，並有簡單的單元測試（pytest）。
9. 不要為了「看起來完整」加規格外的功能。範圍外見第 9 節。

## 1. 目標

每天自動收集上市櫃公司（約 1,800 家）的月營收、季報、年報，找出：

- **成長未反映**：基本面正在成長，但股價還沒反映的公司。
- **成長將開始**：還沒達成長門檻，但出現營收拐點或 ≥ 2 個領先訊號的公司。

並提供每家公司的法說會 / 重大訊息 / 新聞時間軸，與 AI 對入選公司的解讀（成長來源、持續性、風險，附來源）。

營收與財報是**落後指標**，系統價值來自：月營收比季報早 1–2 個月、涵蓋沒人追蹤的中小型股、加入較領先的訊號與「市場尚未反映」指標。**這些訊號是否有用，必須用回測驗證（P2），不是假設。**

## 2. 已定案的決策

| # | 項目 | 決定 |
| --- | --- | --- |
| 1 | 執行環境 | **本機 Windows** + 工作排程器（設「錯過後盡快補跑」） |
| 2 | 儀表板 | Streamlit + Plotly |
| 3 | 資料庫 | SQLite（`data/revenue.db`）；若分析查詢太慢再評估 DuckDB |
| 4 | LLM | 混用：A1 新聞分類 Gemini Flash-Lite；A2/A3 Gemini Flash 或更高階。**開發期用免費額度**，上線再開付費並在 Google Cloud 設預算上限（約每月 5 美元）。以介面封裝，可切換 Claude |
| 5 | 歷史深度 | 5 年（回測至少涵蓋一個景氣循環） |
| 6 | 資料備援 | FinMind 免費版為備援，P0 實測後再決定要不要付費 |
| 7 | 通知 | **Email**（Gmail SMTP + 應用程式密碼）；之後再視需要加 Telegram |
| 8 | 門檻與權重 | 先用第 5 節初始值，P2 回測後調整 |
| 9 | 與 Stock_event | 獨立專案；新聞收集模組沿用其程式 |
| 10 | 供應鏈對照表 | 沿用 `stockfilter/data/supply_chain_data.json`，需重新更新 |
| 11 | 回測基準 | 加權指數、櫃買指數、同產業平均三者都算，以同產業平均為主 |

## 3. 資料來源（P0 要實測確認的部分標 ⚠）

| 資料 | 主要來源 | 備援 | 備註 |
| --- | --- | --- | --- |
| 上市月營收 | TWSE OpenAPI `https://openapi.twse.com.tw/v1/opendata/t187ap05_L` | MOPS、FinMind | **已確認**：只提供最新一個月；欄位含 `資料年月`(民國 YYYMM)、`公司代號`、`營業收入-當月營收`、`-上月營收`、`-去年當月營收`、`-上月比較增減(%)`、`-去年同月增減(%)`、`累計營業收入-當月累計營收`、`-去年累計營收`、`-前期比較增減(%)`、`備註`。單位未標示（⚠ 預期為千元，需核對） |
| 上櫃月營收 | ⚠ 櫃買中心 OpenAPI `mopsfin_t187ap05_O`（對網頁查詢工具封鎖，需實測） | MOPS `t21sc03` otc、FinMind | |
| 月營收歷史 | ⚠ MOPS 歷史彙總頁 `https://mops.twse.com.tw/nas/t21/{sii|otc}/t21sc03_{民國年}_{月}.html`（每月一頁、全市場，編碼預期 big5） | FinMind `TaiwanStockMonthRevenue`（2002 起；單一公司免費，全市場需付費會員） | 用於初始回補 5 年 |
| 季報 / 年報 | MOPS 財報彙總（損益表、資產負債表） | FinMind `TaiwanStockFinancialStatements`、`TaiwanStockBalanceSheet`、`TaiwanStockCashFlowsStatement` | ⚠ 合約負債、存貨、預付設備款的欄位名需實測 |
| 每日股價、市值 | 沿用 `stockfilter/data/fetcher.py` 的 yfinance 做法；或 TWSE / TPEx 每日收盤 | FinMind | 代號格式 yfinance：`.TW` 上市、`.TWO` 上櫃 |
| 法說會 | MOPS 法人說明會一覽、簡報 PDF | 公司 IR 網站 | ⚠ 實測 |
| 重大訊息 | MOPS 即時重大訊息 | — | ⚠ 實測 |
| 新聞 | 沿用 Stock_event 的 `news_fetcher.py`（鉅亨、MoneyDJ、工商時報、UDN、Yahoo 財經 RSS）；Google News RSS 依公司名查詢 | — | |
| 供應鏈 | `stockfilter/data/supply_chain_data.json`（166 產業、約 2,400 檔、上/中/下游） | — | 更新於 2026-03（概念股 2026-05），需重新更新 |

法定公告時間：月營收每月 10 日前；Q1 5/15、Q2 8/14、Q3 11/14；年報 3/31。

## 4. 資料庫 schema（草案，P0 定案）

- `companies`：stock_id (PK)、name、market (sii/otc)、industry、listed_date、delisted_date、updated_at
- `monthly_revenue`：stock_id、ym (YYYY-MM)、revenue、revenue_prev_month、revenue_last_year、mom、yoy、cum_revenue、cum_last_year、cum_yoy、note、published_at、source、**PK (stock_id, ym)**；保留更正值的覆寫紀錄（`revenue_history` 或 revision 欄位）
- `financials`：stock_id、period (如 2026Q3)、type（損益/資產負債）、item、value、published_at、source
- `prices`：stock_id、date、close、volume、market_cap
- `metrics`：stock_id、as_of、指標名、值（見第 5 節）
- `screens`：as_of、list_type (growth_unpriced / growth_starting)、stock_id、score、reasons(JSON)
- `events`：id、stock_id、ts、type (法說/重訊/新聞)、title、url、body、dedup_key
- `ai_analysis`：id、stock_id、as_of、task (A1/A2/A3)、model、input_hash、output(JSON)、sources(JSON)、created_at
- `supply_chain`：industry、stage (上/中/下游)、stock_id
- `job_runs`：job、started_at、finished_at、status、rows、error

回測要用「公告日當天已知」的資料，所以所有事實表都要有 `published_at`（或公告日），不能只存期別。

## 5. 判定邏輯（初始值，全部放 `config.yaml`，不寫死）

**月度指標**：月 YoY、月 MoM（僅參考）、累計 YoY、3 個月滾動 YoY、連續成長月數（YoY>0）、成長加速（本月 3M YoY > 上月 3M YoY）、營收創 12/36 個月或歷史新高。
**季度**：營收、毛利率、營業利益、EPS 的 YoY 與 QoQ；三率是否連 2 季上升。**年度**：3 年營收 / EPS CAGR。

**篩選（初始值）**
- 月度成長股：3M YoY ≥ 20% 且連續成長 ≥ 3 個月；加分：成長加速、創新高。
- 季度成長股：營收 YoY ≥ 15% 且營業利益 YoY ≥ 15%；加分：毛利率上升。
- 年度成長股：3 年營收 CAGR ≥ 15% 且 EPS 連 3 年成長。
- 排除：去年同期基期過低（去年同月營收 < 近 3 年平均的 50%）、單月營收 < 1 億元（可調）、全額交割 / 處置股。

**領先訊號**：合約負債成長（YoY ≥ 30% 或占營收比上升）、備貨訊號（存貨成長且週轉天數未惡化）、擴產訊號（資本支出 / 預付設備款大增；重訊取得設備或廠房）、法說指引上修（A2 判定）、上游帶動（供應鏈上游 3M YoY 轉強、本公司尚未跟上）、產業擴散（同產業 YoY 轉正的公司比例連 2 月上升）、營收拐點（3M YoY 由負轉正或衰退幅度連 3 月收斂）、新聞事件（A1 分類為接單 / 擴產 / 新客戶且重要度 ≥ 4）。

**市場尚未反映**：近 3 個月股價漲幅 − 3M 營收 YoY（差距越大越被低估）；本益比（近四季 EPS）在自身近 5 年的百分位；營收公告後 5 個交易日股價反應（利多未漲 = 尚未反映）。

**綜合分數 0–100**：各指標依全市場百分位排名後加權，初始權重：基本面動能 40%、領先訊號 30%、市場尚未反映 30%。分數用來排序，篩選規則用來入選。

## 6. AI 分析任務

| 任務 | 輸入 | 輸出（固定 JSON schema） | 頻率 | 模型 |
| --- | --- | --- | --- | --- |
| A1 新聞 / 重訊分類 | 標題 + 摘要，20–30 則合併一次呼叫 | 公司代號、類型（接單 / 擴產 / 財報 / 人事 / 法律…）、情緒（正 / 中 / 負）、重要度 1–5 | 每則一次 | Flash-Lite |
| A2 法說簡報摘要 | 簡報 PDF 抽出的文字 | 營運重點、產品與客戶組合、展望與指引（相對上次是否上修）、資本支出、風險 | 每場一次 | Flash |
| A3 成長綜合分析 | 程式算好的指標 + 最近一份 A2 + 近 90 天 A1 重要事件 | 成長來源、持續性（高 / 中 / 低）、領先訊號解讀、市場是否已反映、風險、與上期差異、引用來源 | 月營收後、季報後；只做入選公司與追蹤清單 | Flash 或更高階 |

原則：數字由程式算；每個結論附來源（簡報頁碼或新聞連結），無來源的推論標為「推測」；保留歷次分析版本；LLM 供應商以介面封裝；開發期用免費額度並限速，遇 429 退避重試；上線後設預算上限與用量警示。
可沿用：`Stock_event/llm_classifier.py`（Gemini 客戶端含 429 重試、Claude 備援、批次 JSON）、`Stock_event/news_fetcher.py`。

## 7. 排程（台灣時間，草案）

| 時間 | 工作 | 條件 |
| --- | --- | --- |
| 07:00 | 法說會一覽、前一晚新聞 + A1 | 每天 |
| 13:00 | 重訊、新聞 + A1 | 每天 |
| 20:00 | 月營收、重訊、新聞 + A1 | 營收：每月 1–12 日每天，其餘每週一次補更正 |
| 20:30 | 季 / 年財報 | 公告期（5、8、11、3 月） |
| 21:00 | 指標計算 → 篩選 | 有新營收或財報時 |
| 21:30 | A2 簡報摘要、A3 綜合分析 | 有新簡報或新入選公司時 |
| 22:00 | Email 當日摘要（入選變化、追蹤清單事件） | 每天 |

用 Windows 工作排程器，開啟「錯過排程後盡快補跑」。每個 job 寫 `job_runs`，失敗要有 log 與重試。

## 8. 階段與驗收標準

**P0 資料源驗證**
- 工作：執行 `p0_probe.py`（已寫好，使用者在本機跑，產出 `p0_probe_report.md/.json`），讀報告；補測沒涵蓋的來源（法說會、重訊、MOPS 歷史頁解析）；確認單位、欄位、歷史深度、限流；定案 DB schema 與資料來源優先順序。
- 交付：`docs/data_sources.md`（每個來源：端點、欄位對照、單位、歷史深度、限流、已知問題）、`schema.sql`。
- 驗收：每個資料來源有實測結果與結論；缺漏或不可用的來源列出替代方案，交給 Terry 決定。

**P1 MVP：月營收 + 股價 → 月度成長清單**
- 工作：M1 抓取（初始回補 5 年歷史 + 每日增量 + 更正值覆寫 + 缺漏檢查）、M3 月度指標、M4 月度篩選與綜合分數、Streamlit 兩個清單 + 個股營收走勢圖（含 YoY 線）、每日排程。
- 驗收：全市場（上市 + 上櫃）月營收完整入庫；隨機抽 20 檔與官方網頁數字一致；兩個清單可依產業、市值、分數篩選排序；排程可重複執行不產生重複資料。

**P2 回測驗證**
- 工作：用 5 年歷史，**只用公告日當天已知的資料**，檢驗每月入選公司公告後 1 / 3 / 6 個月報酬，與加權指數、櫃買指數、同產業平均比較；納入已下市公司；分別檢驗「基本面動能」「領先訊號」「市場尚未反映」各自有無效果。
- 交付：回測報告（含失效情境與樣本數），建議調整的門檻與權重。
- 驗收：報告說明結論是否支持規格假設。**若訊號無效，先停下來與 Terry 討論，不要繼續往下做。**

**P3 季 / 年財報**：損益表 + 資產負債表（合約負債、存貨、預付設備款）、季度與年度篩選、個股頁三率與 EPS、納入回測。
**P4 事件追蹤**：M2 法說會 / 重訊 / 新聞（沿用 `news_fetcher.py`）+ A1、事件時間軸、每日動態頁、追蹤清單。
**P5 AI 分析**：A2、A3、供應鏈對照表更新與上游帶動訊號（沿用 `stockfilter/core/supply_chain.py` 的邏輯）、Email 通知。
**P6 持續優化**：每季重跑回測並追蹤實際入選表現；視需要串接 Stock_event。

## 9. 不在範圍內（v1）

股價技術分析與買賣訊號（由 Stock_event / stockfilter 負責）、自動下單、即時盤中資料、興櫃與海外公司。

## 10. 建議專案結構

```
Revenue/
  PLAN.md  config.yaml  .env.example  requirements.txt  schema.sql
  p0_probe.py  p0_probe_report.md
  docs/data_sources.md
  collectors/   (revenue.py, financials.py, prices.py, events.py, news.py)
  analysis/     (metrics.py, screen.py, score.py, backtest.py, ai/)
  app/          (streamlit 儀表板)
  jobs/         (排程入口，供工作排程器呼叫)
  tests/
  data/         (revenue.db，不 commit)
```

## 11. 開始時請先做

1. 讀本檔，用 3–5 行回報你的理解與要先問的問題（不要直接開工）。
2. 檢查 `p0_probe_report.md` 是否已存在；若無，請 Terry 先執行 `python p0_probe.py`。
3. 開始 P0；完成後停下來給驗收結果。
