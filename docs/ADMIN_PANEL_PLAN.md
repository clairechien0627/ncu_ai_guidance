# Admin Panel 整體計畫書

> 最後更新：2026-05-19
> 系統：Report Agent — RAG 學術 PDF 分析 + 學生導讀研究助理

---

## 一、Admin Panel 的定位與目標

Admin Panel 是面向**系統維護者 / 研究員**的後台工作站，負責：

| 角色 | 使用場景 |
|------|---------|
| 系統管理員 | 監控 pipeline 健康、文件處理、trace 品質、系統資源 |
| 研究員 / Prompt 工程師 | 評估回答品質、做 A/B 測試、優化 prompt 策略 |
| 資料管理員 | 管理文件庫、維護 dataset、觸發回歸測試 |

整個 Admin 由五大功能域組成，彼此有清楚的資料流向：

```
文件 → 索引 → 對話 → Trace → 評估 → Dataset → 實驗
  ↑                                              ↓
Prompt ←───────────── 指標回饋 ──────────────────┘
```

---

## 二、模組地圖與互動關係

```
Overview
├── Dashboard          ← 所有模組的聚合統計入口
├── System Jobs        ← 所有背景任務的即時監控
└── Playground         ← 主動觸發測試 + 系統健康

Tracing（資料觀察）
├── Sessions           ← Thread 維度的對話群組
├── Traces             ← 單次請求的核心記錄單元  ←── 全域中樞
├── Observations       ← Trace 內部的 LLM/Tool 步驟
└── Users              ← 使用者行為統計

Evaluation（品質保證）
├── Scores             ← 7 維度品質分數聚合
├── Eval Runs          ← 批次評估任務管理
├── Datasets           ← 回歸測試資料集
└── Experiments        ← Dataset Replay A/B 測試

Documents（內容管理）
└── Documents          ← PDF 上傳 / 解析 / 索引 / 摘要

Prompts（提示策略）
├── Prompts            ← 版本管理 + 品質指標
└── Prompt Metrics     ← 每個 prompt 的效能趨勢
```

**核心互動線：**

```
Traces ──→ Eval Runs ──→ Scores ──→ Datasets ──→ Experiments
   ↑            │                        │             │
   │            └── observations ──→     │             │
   │                                     ↓             ↓
Documents ──→ Jobs                  Prompts ──→ Prompt Metrics
```

---

## 三、各模組詳細說明

---

### 3.1 Dashboard

**定位**：系統健康一眼看全局，快速發現異常

**現有功能：**

- 品質 + 延遲趨勢折線圖（7/14/30 天）
- 按 Route Intent 分類的執行統計
- 最近 5 筆錯誤 trace（可點擊跳轉）

**目標：**

- 讓管理員 30 秒內判斷系統是否正常
- 提供觸發深度調查的快速入口

**現狀評分：** ⭐⭐⭐ 基本可用

**待補充：**

- [ ] Token / Cost 每日趨勢（費用監控）
- [ ] 文件管線狀態摘要（待處理/失敗文件數）
- [ ] Eval worker 佇列深度指示（異常告警）
- [ ] 快速跳轉：「最近失敗評估」「待批次評分 trace」

---

### 3.2 System Jobs

**定位**：被動監控三條背景管線的執行狀態

**現有功能：**

- Document Pipeline（SSE 即時推送）
- Evaluation Runs（8s 輪詢）
- Experiment Runs（8s 輪詢）

**目標：**

- 管線失敗能立刻被看到，有明確的重試入口
- 不需要進詳情頁就能判斷任務是否正常

**現狀評分：** ⭐⭐⭐⭐ 功能完整

**待補充：**

- [ ] Eval Run Retry 按鈕（目前需去 Eval Runs 頁操作）
- [ ] Job 失敗原因展開（目前只有 error text，無 stack trace）
- [ ] 預估完成時間（based on 平均處理速率）

---

### 3.3 Playground ✨（新）

**定位**：主動觸發測試 + 系統健康驗證，開發/維護者工具

**現有功能：**

- Chat Tester：發訊息 → 看 route + trace_id → 跳 trace 詳情
- Route Tester：毫秒測路由決策（不跑 agent）
- System Health：服務狀態 + outbox/worker 佇列深度
- Maintenance：Backfill dry-run、Batch Score、Retry

**目標：**

- 不切頁面就能驗證「發訊息 → trace 有沒有進 v2 → 評估能不能跑」完整鏈

**現狀評分：** ⭐⭐⭐ 新建，基礎可用

**待補充：**

- [ ] Retrieval Tester：針對特定文件測 RAG 檢索結果
- [ ] Trace 驗證工具：輸入 trace_id 顯示 v2/observation/score 完整狀態
- [ ] Cost 估算：一次對話消耗多少 token / 多少費用

---

### 3.4 Sessions

**定位**：以 Thread（對話線程）為維度聚合 Trace 群組

**現有功能：**

- 按任務類型、日期範圍過濾
- 每個 Session 的 trace 數、token、平均品質

**目標：**

- 讓研究員快速找到「同一個使用者的連續對話」
- 識別哪些 session 品質特別差（觸發深度調查）

**現狀評分：** ⭐⭐⭐ 基本可用

**與其他模組互動：**

- Session Detail → 展開 Session 下所有 Trace
- 每個 Trace → 可跳轉 TracesPage 詳情

**待補充：**

- [ ] 直接在 Session 行觸發「對此 Session 所有 trace 評分」
- [ ] Session 品質折線圖（對話品質是否隨輪數下降）

---

### 3.5 Traces ⭐（全域中樞）

**定位**：最核心的資料觀察單元，整個 admin 的中心

**現有功能：**

- 19 欄可自訂寬度表格，12 個 filter 維度
- Bulk eval（批次評分）、Bulk delete
- Obs. Levels 顯示子觀察健康狀態
- Level 欄（ERROR/WARNING/DEFAULT/DEBUG）
- 點擊行 → 側邊詳情抽屜（trace detail + observation tree）

**目標：**

- 任何一次使用者請求都能在這裡找到、診斷、評分
- trace 的 input/output/latency/cost/level 一目瞭然

**現狀評分：** ⭐⭐⭐⭐⭐ 接近 Langfuse 水準

**與其他模組互動：**

- → Eval Runs（選取 trace → Run Eval）
- → Datasets（Add to Dataset）
- → Sessions（Session ID 可點擊）
- → Observations（Trace Detail 展開 observation tree）

**待補充：**

- [ ] Trace input/output 搜尋（語義搜尋或全文搜尋）
- [ ] Score filter（只看已評分 / 未評分 / 低分）
- [ ] Cost 欄（token 費用）

---

### 3.6 Observations

**定位**：Trace 內部的 LLM 呼叫、Tool 呼叫、Chain 步驟

**現有功能：**

- 列表所有 observation，按 type 過濾（LLM/TOOL/CHAIN/SPAN）
- 顯示 token 使用、延遲、狀態、Trace 連結

**目標：**

- 讓 prompt 工程師看到每個 LLM call 的 input/output
- 識別哪些 tool 呼叫失敗率高、哪些模型速度慢

**現狀評分：** ⭐⭐⭐ 基本可用

**與其他模組互動：**

- ← Traces（Trace Detail 內嵌 observation tree）
- → Prompts（promptName 可跳轉）

**待補充：**

- [ ] 按 trace_id 篩選（從 trace 跳到只看此 trace 的 observations）
- [ ] 模型成本視圖（哪個模型最貴）
- [ ] Observation 型別 EVALUATOR 的顯示優化

---

### 3.7 Users

**定位**：使用者行為統計，識別活躍用戶和問題用戶

**現有功能：**

- 每個使用者的 session 數、trace 數、token、平均品質
- 進入詳情查看該使用者所有 session

**現狀評分：** ⭐⭐⭐ 基本可用

**待補充：**

- [ ] 使用者活動時間線（哪些時間段最活躍）
- [ ] 品質過低的使用者高亮（可能輸入品質差）

---

### 3.8 Scores

**定位**：品質評分的匯聚視圖，評估整體模型回答品質

**現有功能：**

- 分數分布直方圖（0-5 分佈）
- 7 個評分維度的平均值
- 低品質 trace 列表（可加入 Dataset）
- 觸發 Batch Score

**目標：**

- 讓研究員一眼知道整體回答品質是上升還是下降
- 快速找到最差的 trace 進行原因分析

**現狀評分：** ⭐⭐⭐ 基本可用

**與其他模組互動：**

- → Datasets（低品質 trace 加入 dataset）
- ← Traces（分數來源）
- → Eval Runs（觸發批次評分）

**待補充：**

- [ ] 分數趨勢線（每週分數是否進步）
- [ ] 按 route_intent 細分分數（研究類 vs 問答類品質差多少）
- [ ] 評分維度相關性分析（哪些維度會一起下降）

---

### 3.9 Eval Runs

**定位**：批次評估任務的生命週期管理

**現有功能：**

- 列表所有評估運行，狀態（pending/running/completed/failed）
- 進度百分比、平均分數
- 新建評估、點進詳情看每筆 trace 評分

**目標：**

- 定期對所有未評分的 trace 批次打分
- 為 Datasets / Experiments 提供評分資料

**現狀評分：** ⭐⭐⭐⭐ 功能完整

**與其他模組互動：**

- ← Traces（被評估的對象）
- → Scores（評分結果寫回）
- → Datasets（評分 trace → 加入 dataset）

**待補充：**

- [ ] Retry 按鈕直接在列表行（目前需點進詳情）
- [ ] 評估進度 ETA
- [ ] 評估成本估算（多少 token）

---

### 3.10 Datasets

**定位**：回歸測試資料集，儲存「問題-預期答案」對

**現有功能：**

- 建立、管理 dataset
- 每個 item：input / expected_output / context / tags
- 從 trace 或低品質 trace 批次加入
- 匯出 JSON

**目標：**

- 讓研究員積累「已知好答案」的測試集
- 配合 Experiments 做 prompt 改版前的回歸測試

**現狀評分：** ⭐⭐⭐⭐ 功能完整

**與其他模組互動：**

- ← Traces / Eval Runs（item 來源）
- → Experiments（dataset 作為 replay 輸入）

**待補充：**

- [ ] Dataset 覆蓋率統計（有幾個 intent / task_type 被覆蓋）
- [ ] 重複 item 偵測
- [ ] 從 Observation 層面建 dataset item（測特定步驟而非整體輸出）

---

### 3.11 Experiments

**定位**：A/B 測試平台，對 dataset 用不同 prompt/model 做 replay

**現有功能：**

- 建立實驗（選 dataset + prompt + model）
- 追蹤 replay 進度
- 兩個實驗對比（進步/退步統計、維度 delta）

**目標：**

- prompt 改版前先在 dataset 上做回歸，確認品質不退步
- 比較不同 model（如 gpt-4o vs gpt-4o-mini）的回答品質

**現狀評分：** ⭐⭐⭐⭐ 功能完整

**與其他模組互動：**

- ← Datasets（輸入）
- → Eval Runs（實驗完成後觸發評估）
- → Prompts（選用哪個 prompt 版本）

**待補充：**

- [ ] 統計顯著性檢驗（差距是否有意義）
- [ ] 成本對比（新 prompt 是否更貴）
- [ ] 匯出對比報告（PDF/CSV）

---

### 3.12 Documents

**定位**：PDF 文件庫的全生命週期管理（上傳 → 解析 → 索引 → 摘要）

**現有功能：**

- 上傳 PDF，觸發解析/嵌入/摘要 pipeline
- 批次操作（解析、重新索引、摘要抽取）
- 5 標籤詳情：Info / Pipeline / Content / Chunks / Traces
- 文件 Trace 記錄

**目標：**

- 讓資料管理員快速掌握文件庫狀態
- 識別解析失敗、摘要品質差的文件

**現狀評分：** ⭐⭐⭐⭐ 功能最完整的頁面

**與其他模組互動：**

- → Jobs（管線任務由 Jobs 頁面監控）
- → Traces（每份文件有對應的 research_agent trace）
- → Scores（摘要品質來自 summary_quality 評分）

**待補充：**

- [ ] 摘要品質分數顯示（現在只有有/無）
- [ ] 文件解析可靠性指標（chunk 數、平均 chunk 長度）
- [ ] Step 4 summary_quality 啟用（ENABLE_QUALITY_CHECK env）

---

### 3.13 Prompts + Prompt Metrics

**定位**：Prompt 版本管理 + 效能追蹤，prompt 工程師的工作台

**現有功能：**

- 列出所有 prompt，版本數、平均品質
- 版本內容 / 歷史 / diff 查看
- 每個 prompt 的品質/延遲趨勢、版本對比

**目標：**

- 讓 prompt 工程師看到改版後品質是否提升
- 快速定位哪個 prompt 版本表現最好

**現狀評分：** ⭐⭐⭐⭐ 功能完整

**與其他模組互動：**

- ← Traces（trace 記錄 prompt_name + prompt_version）
- → Experiments（選 prompt 版本做 replay）

**待補充：**

- [ ] Prompt 同步狀態（與 Langfuse 是否同步）
- [ ] A/B 統計顯著性（版本差距是否有意義）
- [ ] 自動推薦最優版本

---

## 四、跨模組缺失分析

### 4.1 成本追蹤（Cost Tracking）⚠️

**現狀**：`observations` 有 `total_cost`，`traces_v2` 有 `input_cost`/`output_cost`，但所有頁面都沒有顯示費用。

**影響**：不知道每次對話/每份文件的處理費用，無法控制 API 成本。

**需要加的地方：**

- Dashboard：每日 API 費用趨勢
- Traces：Cost 欄（現有欄位可加）
- Experiments：對比兩組實驗的總費用差異
- Documents：每份文件的處理費用

---

### 4.2 告警機制（Alerting）⚠️

**現狀**：錯誤只能被動在 Dashboard 或 TracesPage 看到，沒有主動通知。

**需要加的地方：**

- Dashboard：錯誤率超過閾值 → 紅色高亮警示卡
- Playground / System Health：outbox failed > 0 → 主動顯示告警
- Jobs：worker 停止 → 顯示告警

---

### 4.3 匯出功能（Export）

**現狀**：只有 Dataset 有匯出 JSON。

**需要加的地方：**

- Traces：匯出 CSV（選定 filter 的所有 trace）
- Eval Runs：匯出評分報告
- Experiment Compare：匯出對比結果

---

### 4.4 Trace v2 資料完整性

**現狀**：router_agent trace 有 input/output（已修），chat_agent 有 observation，research_agent 有 observation tree。但：

- Eval Runs 評估結果作為 EVALUATOR observation 寫入（已修）
- Observations 頁面的 EVALUATOR type 顯示未優化
- Backfill 腳本語意已修，可對歷史資料補跑

---

### 4.5 Session ↔ Trace 跨頁導航

**現狀**：Sessions 到 Traces 的跳轉需多步，反向（Trace → Session）已有 session_id 欄但未連結。

**建議**：Trace 的 Session 欄可直接點擊 → 跳 Session Detail。

---

## 五、模組進度總表

| 模組 | 完整度 | 關鍵缺失 |
|------|--------|---------|
| Dashboard | ⭐⭐⭐ 70% | Cost 趨勢、管線狀態摘要 |
| System Jobs | ⭐⭐⭐⭐ 85% | Retry 按鈕、ETA |
| **Playground** | ⭐⭐⭐ 60% | Retrieval Tester、Trace 驗證 |
| Sessions | ⭐⭐⭐ 70% | Session 級別 eval、品質趨勢 |
| **Traces** | ⭐⭐⭐⭐⭐ 95% | Cost 欄、語義搜尋 |
| Observations | ⭐⭐⭐ 65% | 按 trace 過濾、Cost 視圖 |
| Users | ⭐⭐⭐ 65% | 活動時間線 |
| Scores | ⭐⭐⭐ 70% | 趨勢線、按 intent 細分 |
| Eval Runs | ⭐⭐⭐⭐ 85% | Retry 快捷、Cost 估算 |
| **Datasets** | ⭐⭐⭐⭐ 85% | Observation-level item |
| Experiments | ⭐⭐⭐⭐ 85% | 統計顯著性、Cost 對比 |
| Documents | ⭐⭐⭐⭐ 90% | 摘要品質顯示 |
| Prompts | ⭐⭐⭐⭐ 85% | 同步狀態、統計顯著性 |
| Prompt Metrics | ⭐⭐⭐⭐ 80% | A/B 顯著性 |

---

## 六、建議優先補充清單

### P1（影響日常使用）

1. **Traces Cost 欄** — token 費用顯示，資料已在 observations 表
2. **Dashboard Cost 趨勢** — 每日 API 費用，防止意外超支
3. **Eval Runs 行內 Retry** — 失敗時不需進詳情頁才能重試
4. **ENABLE_QUALITY_CHECK=true** — 啟用 summary step 4

### P2（品質提升）

1. **Session → Trace 雙向導航** — Trace 的 Session 欄可點擊
2. **Scores 趨勢線** — 每週品質是否改善
3. **Playground Retrieval Tester** — 測 RAG 檢索結果

### P3（長期功能）

1. **Experiment 統計顯著性** — A/B 結果是否有意義
2. **Traces CSV 匯出** — 批次下載分析
3. **告警機制** — 錯誤率 / worker 狀態主動告警
