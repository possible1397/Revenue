# 資料來源實測結果（P0）

實測日期：2026-11-01，環境：本機 Windows、Python 3.10、requests。
原始報告：`p0_probe_report.md/.json`。

## 1. 月營收

| 項目 | 上市 | 上櫃 |
| --- | --- | --- |
| 最新一月端點 | `https://openapi.twse.com.tw/v1/opendata/t187ap05_L` | `https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O` |
| 實測筆數（資料年月 11509） | 1,088 | 893 |
| 格式 | JSON | JSON |
| 歷史端點 | `https://mopsov.twse.com.tw/nas/t21/sii/t21sc03_{民國年}_{月}.html` | 同左，`sii` 換成 `otc` |
| 歷史實測筆數（115/9） | 1,091 | 897 |
| 歷史深度 | 已測 110/1（2021-01），可用 | 同左 |

重點：
- PLAN 原寫的 `openapi.tpex.org.tw` 網域解析失敗，正確網域是 `www.tpex.org.tw`。
- 舊網域 `mops.twse.com.tw/nas/t21/...` 回 404，要改用 `mopsov.twse.com.tw`。
- 單位是**千元**。驗證：台積電 115/9 營收 511,857,399 千元。
- 歷史 HTML 為 big5 編碼，每列 11 欄：公司代號、公司名稱、當月營收、上月營收、去年當月營收、上月比較增減(%)、去年同月增減(%)、當月累計營收、去年累計營收、前期比較增減(%)、備註。依產業分表，沒有產業欄位，產業要從 OpenAPI 或 `companies` 表補。
- 交叉驗證：115/9 的 MOPS HTML 與 OpenAPI 比對，上市 1,088 筆全部對得上公司，只有 2 筆營收數字不同；上櫃 893 筆全部對得上，1 筆不同。不同的推測是公司更正營收，尚未逐筆確認。
- OpenAPI 回應沒有「公告日」，MOPS HTML 有「出表日期」（115/11/01）但那是頁面產生日，不是各公司公告日。

## 2. FinMind（備援）

- 未登入：單一公司可查。`TaiwanStockMonthRevenue` 2330 從 2015-01 到 2026-10，共 142 筆。欄位只有 `revenue`、`revenue_month`、`revenue_year`，沒有 YoY、MoM、累計，需自行計算。
- 全市場（不帶 `data_id`）回 400，需要會員。`user_info` 回「請提供 token」。
- 資產負債表 `TaiwanStockBalanceSheet`：以 2330 實測，最新一期 2026-06-30；欄位格式為 `date, stock_id, type, value, origin_name`。
  - 存貨：`Inventories`。
  - 合約負債：`CurrentContractLiabilities`，只有該公司有這個科目時才出現（2330、3008、2317 沒有；6446、8299 有）。
  - 預付款項：`Prepayments`，不是「預付設備款」。預付設備款在 FinMind 找不到對應欄位。
- 損益表 `TaiwanStockFinancialStatements`：有 `Revenue`、`GrossProfit`、`OperatingIncome`、`IncomeAfterTaxes`、`EPS` 等。
- 現金流量表 `TaiwanStockCashFlowsStatement`：資本支出看 `PropertyAndPlantAndEquipment`（取得不動產、廠房及設備）。
- 限流：未登入有每小時額度（實測期間沒觸發，上限未確認）。

## 3. 重大訊息

| 項目 | 上市 | 上櫃 |
| --- | --- | --- |
| 端點 | `https://openapi.twse.com.tw/v1/opendata/t187ap04_L` | `https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O` |
| 實測筆數 | 170 | 67 |

- **只含最新一天**，沒有歷史。要有時間軸必須每天抓、自己累積。
- 欄位：發言日期、發言時間、公司代號、公司名稱、主旨、符合條款、事實發生日、說明。上櫃欄位名稱不同（`Date`、`SecuritiesCompanyCode`、`CompanyName`），入庫前要正規化。
- 歷史重訊無法回補，回測與時間軸只能從開始收集那天起算。

## 4. 法說會

- 舊網址 `mops.twse.com.tw/mops/web/t100sb07_1` 回 200 但只有 65 bytes 的轉址腳本。
- 新版 MOPS 的 `/mops/api/t100sb07_1` 用猜的參數 POST，同樣被轉回首頁，沒有取得資料。
- TWSE / TPEx OpenAPI 的 swagger 清單中，沒有法說會相關端點。
- **結論：目前沒有找到可用的程式化來源。** 我沒有繼續嘗試繞過 MOPS 的防護，需要你決定替代方案（見下）。

## 5. 尚未實測

- 股價與市值（yfinance 或 TWSE / TPEx 每日收盤）：沿用 `stockfilter/data/fetcher.py` 前未讀，P1 開始前測。
- FinMind 未登入的實際額度。
- 季報 / 年報的 MOPS 彙總頁（P3 才需要）。
- 新聞來源（P4 才需要）。

## 6. 資料來源優先順序（建議）

1. 月營收最新月：TWSE / TPEx OpenAPI（每日 1 個請求，各 1 次）。
2. 月營收歷史回補與更正檢查：MOPS `mopsov` 歷史頁，間隔 ≥ 2 秒，約 120 頁（60 個月 × 上市櫃）。
3. 備援：FinMind 單一公司，約 1,980 家逐檔查，每次間隔 ≥ 1 秒。
4. 重訊：OpenAPI 每日累積。

## 7. 已決定（Terry，2026-11-01）

1. 同意用程式批量抓 `www.tpex.org.tw` OpenAPI，頻率每日各 1 個請求。
2. 回補資料的公告日估計為次月 10 日，`published_is_estimate = 1`；之後每日增量記實際抓取日。已知限制：免費來源沒有各公司實際公告時點（FinMind `create_time` 是入庫日，不可用），回測的「公告後 5 日股價反應」在回補期間會失真，P2 報告需註明。
3. 法說會 P4 再評估。
4. 預付設備款改用現金流量表資本支出（`PropertyAndPlantAndEquipment`）當擴產訊號。
