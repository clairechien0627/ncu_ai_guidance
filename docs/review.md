# Report Agent 程式碼細節與架構 Review

本文件針對大專生計畫報告系統 (Report Agent) 的前後端程式碼與資料庫設計進行詳細的 Review，總結其架構優點並提出潛在的優化建議。

## 1. 架構設計與特色亮點 (Architecture Highlights)

### 1.1 後端架構 (Backend)

- **Agent 職責分離**: 採用了先進的 Agent 路由架構，將龐大的任務切分為 `orchestrator_agent`、`retrieval_agent`、`question_agent` 和 `research_agent`。這有效降低了單一 LLM 產生幻覺 (Hallucination) 的機率，並使系統更容易擴展與單獨測試各個專家能力。
- **動態 Prompt 系統**: 將 Prompt 文字與 Python 程式碼解耦 (Decoupling)，存放於 `backend/prompts` 下，並搭配 `registry.py` 和 `loader.py` 實作熱重載。支援 `# extends:` 語法繼承 Prompt、基於 SHA256 的版本追蹤、以及透過 `PROMPT_AB_TESTS` 環境變數做 A/B 測試。這是非常良好的開發模式，適合頻繁調整 Prompt 的 AI 專案。
- **完善的 Trace 機制**: 開發了 `tracer.py` (LocalTracer) 作為 LangSmith 的本地替代方案，繼承 `BaseCallbackHandler` 來攔截所有 chain/llm/tool 事件。搭配 `_build_display()` 將 LangGraph 的原始輸出重建為結構化的 TraceDisplay (messages + answer + sources)，為前端提供了極高的可見度 (Observability)。
- **Research Pipeline 設計精良**: 使用 LangGraph StateGraph 實作了一個完整的 plan→retrieve→reflect→write 迴圈。其中的覆蓋計畫 (coverage items)、slot 狀態管理 (NOT_FILLED/PARTIAL/FILLED/EXHAUSTED)、以及 per-slot 搜尋上限控制，展現了對 RAG 品質控制的深度思考。
- **學術 PDF 解析的深度處理**: `rag/` package 對中文學術論文做了大量客製化處理，包括：
  - 多層級章節標題偵測（字型大小 + 粗體 + 正規表達式 + LLM 分類，三重 fallback）
  - 封面頁 / 目錄 / 參考文獻 / NMR 參數頁面的自動過濾
  - 品質檢測：掃描件 (scanned)、亂碼 (garbled)、圖表密集 (image_heavy)
  - 近似重複 chunk 去除（Jaccard 相似度 ≥ 75%）
- **Hybrid Search (Dense + Sparse)**: Qdrant 向量庫同時啟用了 `text-embedding-3-large` Dense Vector 與 `BM25` Sparse Vector，並使用 `FlashrankRerank` 做後處理。還針對英文文件自動切換為 Dense-only 模式（因 BM25 對英文文件反而有害）。

### 1.2 前端架構 (Frontend)

- **視覺化豐富與高互動性**: 提供了 `TraceViewer`、`DocChat`、`ChunkViewer`、`AdminTracesPage` 等多樣的元件，並能即時透過 SSE (Server-Sent Events) 接收後端任務的進度更新，使用者體驗十分流暢。
- **組件式設計**: React 元件大量運用了 `Suspense` 和 `lazy()` 進行非同步載入 (如 `BatchPage`、`PdfPageViewer`、`ResearchCard`)，降低了初始 bundle 負載。
- **前端 API 層型別安全**: `api.ts` 中為所有後端回傳定義了完整的 TypeScript interface（`TraceItem`、`TraceDetail`、`TraceDisplay`、`SummaryItem` 等），確保前後端契約的型別安全性。
- **SSE 串流 + 狀態即時同步**: `sendMessageStream` 實作了完整的 SSE 串流解析，支援 `token`、`stage`、`done`、`error`、`clear` 多種事件型別，並透過 `/api/jobs/stream` SSE 即時更新 Job 狀態。

### 1.3 資料庫設計 (Database)

- **自訂 JSONB 類型適配器**: `JsonColumn` 類型巧妙地在 PostgreSQL 上使用 `JSONB`（支援 GIN 索引與路徑查詢），在 SQLite 上退化為 `Text`，實現了跨資料庫相容。
- **分離 Document 與 Extraction**: 將 `documents` (原文件元資料) 與 `document_extractions` (生成的摘要與 RAG 結果) 分開儲存，支援未來多版本的提取結果，同時保持向後相容 (backward compat)。
- **豐富的條件式索引**: 對 traces 表建立了多個 partial index（如 `WHERE prompt_name IS NOT NULL`、`WHERE parent_run_id IS NULL`），針對 Trace Monitor 的高頻查詢路徑做了針對性優化。
- **Alembic 遷移 + 防禦式 DDL**: `create_tables()` 中使用大量 `ADD COLUMN IF NOT EXISTS` 語句作為 DDL 防呆，搭配 `_run_migrations()` 在每次啟動時自動執行 Alembic 遷移，確保 schema 始終一致。

---

## 2. 檔案架構分析 (File Architecture Review)

### 2.1 目前架構總覽

```text
d:\try\
├── AGENTS.md / CLAUDE.md / STYLE_GUIDE.md   ← AI 輔助開發指引（根目錄）
├── README.md / TRACE_MONITOR.md
├── docs/                                    ← ✅ 文件已集中至 docs/
├── docker-compose.yml
├── _reference/                              ← 參考資料
├── 各系大專生計畫(104-114)/                   ← 原始資料集
│
├── backend/
│   ├── main.py                              ← FastAPI 入口
│   ├── config.py                            ← 環境設定（Pydantic Settings）
│   ├── database.py                          ← ORM 模型 + DDL + session
│   ├── tracer.py                            ← LocalTracer（LangChain callback handler）
│   ├── observability.py                     ← ✅ Langfuse 整合層（tracer 之上）
│   ├── rag/                                 ← ✅ 已拆分的 RAG package
│   │   ├── __init__.py                      ← re-export + process_pdf 主入口
│   │   ├── cleaning.py                      ← ✅ 過濾/清理函式（消除三重重複）
│   │   ├── section.py                       ← 章節偵測
│   │   ├── store.py                         ← Qdrant 向量管理
│   │   ├── retrieval.py                     ← 搜尋與重排
│   │   └── parsers.py                       ← 三種 PDF 解析器
│   │
│   ├── agents/                              ← Agent 邏輯
│   │   ├── __init__.py                      ← facade：route_agent_message / route_agent_stream
│   │   ├── router_agent.py                  ← ✅ 路由 + intent 分類（原 main_agent.py）
│   │   ├── runner.py                        ← Agent runtime + LangGraph checkpointer
│   │   ├── chat_agent.py                    ← ✅ 對話 agent（從 runner 分離）
│   │   ├── retrieval_agent.py               ← 文件 QA agent
│   │   ├── question_agent.py                ← 出題/導讀 agent
│   │   ├── research_agent.py                ← Research pipeline 入口
│   │   ├── evaluation_agent.py              ← ✅ 品質評分 agent（原 quality_agent.py）
│   │   ├── no_tool_runner.py                ← ✅ 無工具模式的精簡 runner
│   │   ├── request_context.py               ← ✅ 請求上下文（user_id 傳遞）
│   │   ├── types.py                         ← AgentRoute / AgentResult dataclasses
│   │   └── research/                        ← LangGraph research pipeline
│   │       ├── research_graph.py
│   │       ├── state.py / planner.py / reflector.py / retriever.py
│   │       ├── writer.py / task_planner.py / runtime_prompts.py
│   │       └── (8 files)
│   │
│   ├── api/                                 ← FastAPI 路由
│   │   ├── chat.py
│   │   ├── documents.py                     ← ⚠️ 最大的 API 檔案（630 行）
│   │   ├── summaries.py
│   │   ├── traces.py
│   │   └── jobs.py
│   │
│   ├── services/                            ← 業務邏輯服務
│   │   ├── job_service.py                   ← 背景任務佇列
│   │   ├── extraction.py                    ← AI extraction pipeline
│   │   ├── memory_service.py                ← 對話記憶壓縮
│   │   └── storage_service.py
│   │
│   ├── tools/                               ← Agent 工具定義
│   │   ├── rag_tool.py
│   │   └── trace_tool.py
│   │
│   ├── prompting/                           ← Prompt 載入系統
│   │   ├── registry.py / loader.py
│   │
│   ├── prompts/                             ← Prompt 文字檔
│   │
│   ├── tests/
│   ├── scripts/
│   ├── eval/
│   └── uploads/
│
└── frontend/
    └── src/
        ├── main.tsx / App.tsx
        ├── api.ts                           ← 所有 API 呼叫 + 型別定義
        ├── App.css     ⚠️                   ← 全部樣式在單一 CSS（62KB+）
        ├── index.css                        ← 設計系統 tokens
        ├── store/
        │   └── jobStore.ts                  ← ✅ Zustand job 狀態
        ├── hooks/
        │   └── useJobSSE.ts                 ← ✅ SSE 連線 hook
        └── components/                      ← 頁面元件（扁平結構）
            ├── SummaryPage.tsx     ⚠️        ← 仍為大型元件
            ├── AdminTracesPage.tsx ⚠️
            └── (其餘元件)
```

### 2.2 架構亮點 (What's Done Well)

1. **前後端完全分離**：`backend/` 與 `frontend/` 清晰切割，透過 REST API + SSE 溝通，各自可獨立部署與開發。
2. **Agent 邏輯分層設計**：`agents/` 目錄將 orchestrator → specialist → research pipeline 以檔案層級分層，每個 agent 職責明確。
3. **Prompt 與程式碼解耦**：`prompts/`（文字檔）與 `prompting/`（載入系統）分開，修改 prompt 不需要動到 Python 程式碼。
4. **RAG 已完成初步模組化**：`rag/` package 將 section detection、vector store、retrieval、parsers 各自獨立，並透過 `__init__.py` 保持向後相容。
5. **快取目錄結構合理**：`llamacache/`、`pymupdfcache/`、`azuredicache/` 各解析器獨立快取，避免交叉污染。

### 2.3 架構改進建議

#### ✅ 2.3.2 ~~`database.py` 職責過載~~

- 建立 `backend/db/` package，職責明確拆分：
  - `db/session.py` — engine、SessionLocal、Base、get_db
  - `db/models.py` — JsonColumn + 所有 ORM 模型
  - `db/migrations.py` — create_tables（含全部 ALTER TABLE）
  - `db/__init__.py` — re-export 所有公開符號
- `database.py` 改為薄薄一層 re-export（`from db import ...`），28 個現有 `from database import X` **零改動**繼續運作，alembic/env.py 也自動相容。
- 新程式碼應直接從 `db.session`、`db.models`、`db.migrations` 匯入。

#### ✅ 2.3.3 ~~`tracer.py` 孤立在根目錄~~

- `observability.py`（Langfuse 整合）和 `tracer.py`（LocalTracer LangChain callback handler）合併為 `backend/observability/` package：
  - `observability.py` → `observability/__init__.py`（13 個地方的 `from observability import` 自動相容，零改動）
  - `tracer.py` → `observability/tracer.py`
  - 唯一需要更新的 caller：`agents/runner.py` 的 `from tracer import LocalTracer` → `from observability.tracer import LocalTracer`

#### ✅ 2.3.4 ~~`agents/research_agent.py` 與 `agents/research/` 的角色模糊~~

- `agents/research_agent.py`（858 行）全部移入 `agents/research/agent.py`，相對 import 路徑一併修正（`.research.xxx` → `.xxx`、`.types` → `..types`）。
- `agents/research/__init__.py` 改為 re-export facade，對外暴露 `_background_tasks`、`run_research_task`、`run_research_summary`、`trace_metadata`。
- 4 個外部 caller 更新為 `from agents.research import ...`：`main.py`、`services/extraction.py`、`agents/router_agent.py`、`tests/test_prompt_versions.py`。
- 原始 `agents/research_agent.py` 已刪除。

#### ✅ 2.3.5 ~~前端 `components/` 扁平化~~

- 建立 `pages/`：SummaryPage.tsx、AdminTracesPage.tsx、BatchPage.tsx（`../api` 路徑不變）
- 建立 `components/chat/`：ChatWindow.tsx、DocChat.tsx（`../api` → `../../api`）
- 建立 `components/viewer/`：ChunkViewer.tsx、PdfPageViewer.tsx、PdfPanel.tsx、TraceViewer.tsx（`../api` → `../../api`）
- 更新所有 cross-component import：`./ChunkViewer` → `../viewer/ChunkViewer`（ChatWindow、SummaryBrowser）
- App.tsx 的 5 個 import 同步更新（lazy pages + ChatWindow + PdfPanel）
- SummaryPage 的 6 個 lazy import 路徑更新完成

  ```text
  frontend/src/
  ├── pages/              ← SummaryPage / AdminTracesPage / BatchPage
  ├── components/
  │   ├── chat/           ← ChatWindow / DocChat
  │   ├── viewer/         ← ChunkViewer / PdfPageViewer / PdfPanel / TraceViewer
  │   ├── Sidebar.tsx / MarkdownRenderer.tsx / ResearchCard.tsx / SummaryBrowser.tsx
  ├── store/ hooks/       ← 已存在
  └── styles/             ← 見 2.3.6
  ```

#### ✅ 2.3.6 ~~前端 CSS 架構 — `App.css`（3014 行）過大~~

- 依自然邊界切出 4 個子檔案到 `src/styles/`：
  - `sidebar.css`（313 行）— layout、sidebar、conversations、common buttons
  - `chat.css`（555 行）— chat area、messages、input bar、attachments
  - `viewer.css`（192 行）— PDF panel、chunk viewer
  - `traces.css`（1954 行）— admin traces page、trace viewer、research debug
- `App.css` 改為 7 行的 `@import` hub，編輯樣式直接找對應子檔案即可。

#### ✅ 2.3.7 ~~`tools/db_tool.py` 空殼 + `scripts/` 內含測試~~

- `tools/db_tool.py` 已刪除（確認無任何 caller 引用）。
- `scripts/test_llama.py` → `scripts/debug_llamaparse.py`，避免 pytest 誤收。
- `scripts/pdf_abstracts.json` → `scripts/data/pdf_abstracts.json`，資料檔與腳本分離。

### 2.4 目標架構（✅ = 已完成）

```text
d:\try\
├── README.md / AGENTS.md / CLAUDE.md
├── docs/                                     ← ✅ 文件集中
├── docker-compose.yml
│
├── backend/
│   ├── main.py / config.py
│   ├── database.py                           ← ✅ 薄 re-export（from db import ...）
│   ├── db/                                   ← ✅ 職責拆分
│   │   ├── session.py                        ← engine / SessionLocal / Base / get_db
│   │   ├── models.py                         ← ORM 模型（JsonColumn + 全部 models）
│   │   ├── migrations.py                     ← create_tables（待遷移至 Alembic）
│   │   └── __init__.py                       ← re-export facade
│   ├── observability/                        ← ✅ tracer + Langfuse 整合
│   │   ├── __init__.py                       ← Langfuse helpers
│   │   └── tracer.py                         ← LocalTracer
│   ├── rag/                                  ← ✅ 已模組化
│   │   ├── cleaning.py                       ← 過濾/清理（消除重複）
│   │   ├── __init__.py / section.py / store.py / retrieval.py / parsers.py
│   │   └── chunking.py / ingestion.py        ← 待拆（從 __init__.py 繼續拆）
│   ├── agents/
│   │   ├── __init__.py / router_agent.py / runner.py
│   │   ├── chat_agent.py / retrieval_agent.py / question_agent.py
│   │   ├── evaluation_agent.py / no_tool_runner.py / request_context.py
│   │   └── research/                         ← ✅ 所有 research 邏輯集中
│   │       ├── agent.py                      ← 主入口（原 research_agent.py）
│   │       ├── __init__.py                   ← re-export facade
│   │       └── research_graph.py / state.py / planner.py / ...
│   ├── api/ / services/
│   ├── tools/                                ← ✅ db_tool.py 已移除
│   ├── prompting/ / prompts/
│   ├── tests/ / eval/
│   └── scripts/
│       ├── debug_llamaparse.py               ← ✅ 已重命名
│       ├── data/pdf_abstracts.json           ← ✅ 已移入 data/
│       └── (其餘腳本)
│
└── frontend/src/
    ├── pages/                                ← ✅ SummaryPage / AdminTracesPage / BatchPage
    ├── components/
    │   ├── chat/                             ← ✅ ChatWindow / DocChat
    │   ├── viewer/                           ← ✅ ChunkViewer / PdfPageViewer / PdfPanel / TraceViewer
    │   └── Sidebar / MarkdownRenderer / ResearchCard / SummaryBrowser
    ├── store/ hooks/                         ← ✅ Zustand + useJobSSE
    ├── styles/                               ← ✅ sidebar / chat / viewer / traces CSS
    └── App.css                               ← ✅ @import hub（7 行）
```

---

## 3. 程式碼優化建議 (Code Optimization Opportunities)

### 3.1 後端程式碼品質 (Backend Code Quality)

#### ✅ 3.1.1 ~~`rag/__init__.py` 仍過度集中~~

- `rag/cleaning.py` — 過濾/清理函式（已獨立，消除三重重複）
- `rag/chunking.py` — `_make_splitter`、`_remove_near_duplicate_chunks`、`_split_by_structure_and_semantics`（374 行）
- `rag/ingestion.py` — `extract_abstract`、`process_pdf` 主入口（226 行）
- `rag/__init__.py` 縮減為 172 行純 re-export facade（原 713 行）

#### 3.1.3 `job_service.py` 純記憶體佇列（僅多進程部署時相關）

- 目前單進程部署下運作正常，`threading.Lock()` 已保護讀寫。
- **僅在多進程部署時需要處理**：crash 時已 pop 但尚未 persist 的工作會丟失 → 可遷移至 PostgreSQL `SKIP LOCKED` 佇列。目前規模不需要。

#### 3.1.4 全域可變單例（等 3.6 測試覆蓋率上來後再評估）

- `rag/store.py` 5 個、`agents/runner.py` 3 個 `global` 變數，單進程運作正確。
- 封裝至 `AppState` dataclass 的主要收益是測試 mock，目前測試少所以收益有限。等 3.6 加了 unit test 後若遇到 mock 困難再處理。

#### 3.1.5 ✅ ~~API 路由中直接導入私有函式~~

- `_run_chat_agent` → `run_chat_agent`、`_run_research_agent` → `run_research_agent`，已在 `router_agent.py` 去除底線前綴，`api/chat.py` 與測試 monkeypatch 一併更新。

#### 3.1.6 DDL 遷移的技術債（部分改善）

- **現況**: `db/migrations.py`（原 `database.py` 中的 `create_tables()`）已獨立為專屬模組，並加上 docstring 說明「長期目標是移至 Alembic」。50+ 行 ALTER TABLE 語句仍在每次啟動時執行。
- **待完成**: 逐步將所有 ALTER TABLE 遷入 Alembic versions，最終 `migrations.py` 只保留 `Base.metadata.create_all()`。見速查表 P1-2-4 / P1-3-8。

### 3.2 前端優化 (Frontend)

#### 3.2.1 巨型元件重構（第一輪完成）

新建 `hooks/` 目錄，目前包含：

| Hook | 封裝內容 | 用在 |
| ---- | -------- | ---- |
| `useJobSSE` | SSE 連線 + job 狀態 side effects | SummaryPage |
| `useDocumentList` | items、loading、search、deptFilter、loadItems | SummaryPage |
| `useInlineEdit` | 通用 inline 欄位編輯（editing/draft/saving） | SummaryPage（abstract + title） |
| `useChat` | messages、loading、error、conversationId、selectedDocIds + `sendMessageStream` 7 個 callback | App.tsx |

`SummaryPage.tsx` 的 `useState` 從 32 → 17 個（第一輪 hooks 抽離）；`App.tsx` 從 392 → 302 行。

**✅ 第二輪完成**：`useParserCompare` hook + `<JobDrawer>` / `<DocumentDetailPanel>` + `<ParserCompareModal>` + `summaryUtils.ts`

- `SummaryPage.tsx`：**1396 → 546 行**（−61%），useState 25 → 17（8 項移入 hook/component）
- `src/utils/summaryUtils.ts`（37 行）：`getDisplayTitle`、`getProjectNumber`、`relativeTime`、`getStatusMeta`、`getCollegeInfo` 共用工具
- `src/hooks/useParserCompare.ts`（71 行）：解析對比 modal 的 6 項狀態 + 4 個操作函式（`openCompare`、`closeCompare`、`togglePanel`、`handlePage`）
- `src/components/JobDrawer.tsx`（223 行）：工作佇列抽屜，自持 `dismissedJobKeys` / `expandedJobLogs`，直接讀 Zustand store
- `src/components/ParserCompareModal.tsx`（179 行）：解析對比 overlay，使用 `useParserCompare` 回傳值 + props
- `src/components/DocumentDetailPanel.tsx`（492 行）：文件詳情面板，`effectiveParser` memo 已移入此元件，`StatusPill` 私有元件定義在同檔
- TypeScript 型別檢查：新增 6 個檔案零錯誤；既有 5 項錯誤（App.tsx / ChatWindow.tsx）為本次改動前已存在

#### ✅ 3.2.2 ~~內聯樣式的維護問題~~（核心已清理）

- `onMouseEnter`/`onMouseLeave` JS hover 已全部移除（改為 CSS `.job-cancel-btn:hover`）
- Job status dot 顏色 hardcode 改為 `className` 驅動（`.job-status-dot.error/done/running` 等），新增至 `styles/chat.css`
- 剩餘 98 個 `style={{` 為 layout 屬性（flex/gap/fontSize），命名 class 收益有限，保持現狀合理

#### 3.2.3 狀態管理複雜度（部分改善）

- **已完成**：`useChat` hook 封裝了 SSE 串流的全部狀態與回呼（`App.tsx` 的核心複雜度來源已移出）。
- **✅ 已完成**：安裝 `@tanstack/react-query` v5，加入 `QueryClientProvider`（`main.tsx`）。
  - `App.tsx`：`documents` / `conversations` 改 `useQuery`；documents 以 `refetchInterval` 取代手動 `setInterval`；刪除 `refreshDocuments` / `refreshConversations` callback；mutations 改用 `queryClient.setQueryData` 做 optimistic update + `invalidateQueries` 觸發伺服器確認；同時修掉既有的 `setLoading` TypeScript bug。
  - `AdminTracesPage.tsx`：原本 `load()` 一次並行呼叫 9 個 API → 拆成 8 個獨立 `useQuery`（stats、byMode、byPrompt、byVersion、errors、slowRuns、promptList、timeline）+ 1 個 filter-dependent traces query；移除 `load` useCallback、`useEffect([load])`、8 個 `useState`（loading、error、stats 等）；loadMore 改為 append 到 `appendedTraces`，filter 變動時 `useEffect` 自動重置。
  - **未轉換**：`useDocumentList`（SummaryPage 大量 optimistic mutation 需要更大重構）。

### 3.3 RAG 品質與效能 (RAG Quality & Performance)

#### ✅ 3.3.1 ~~HyDE 的使用時機可進一步細化~~ — `run_search_report()` 加入零結果自動 HyDE fallback：首次搜尋結果為空且 planner 未啟用 HyDE 時，自動以 `use_hyde=True` 重試一次，不增加 `search_count`，不影響 `consecutive_empty` 計數

#### ✅ 3.3.2 ~~`_get_section_llm()` 每次呼叫都建立新實例~~ — `_section_llm: AzureChatOpenAI | None = None` 模組變數延遲初始化，後續呼叫重用同一實例

#### ✅ 3.3.3 ~~Reranker `top_n` 設計意圖不明~~ — `get_reranker()` 加上注解說明「top_n 設為最大值是刻意設計，search_documents 再按需 slice，保留彈性」

### 3.4 安全性與穩定性 (Security & Robustness)

#### ✅ 3.4.1 ~~CORS 設定過於寬鬆~~ — `allow_methods` 改為 `["GET","POST","PUT","PATCH","DELETE","OPTIONS"]`，`allow_headers` 改為 `["Content-Type","Authorization","X-Requested-With"]`

#### ✅ 3.4.2 ~~異常處理的資訊洩漏~~ — `api/chat.py` 的 2 處 `detail=f"Agent error: {e}"` 改為 `logger.error(..., exc_info=True)` + 通用訊息回傳給前端

#### ✅ 3.4.3 ~~DB Session 管理不一致~~ — `db/session.py` 加入 `@contextmanager db_session()`，自動 rollback on exception，非 API 上下文的新程式碼可用 `with db_session() as db:` 取代手動 try/finally。已從 `database.py` 和 `db/__init__.py` re-export

### 3.5 資料庫與效能 (Database & Performance)

> 以下三項均屬長期技術債，在文件量小時不影響穩定性；建議待文件量 > 1000 或多進程部署時再評估。

#### 3.5.1 Trace 表長期膨脹

- 每次 Agent 呼叫產生多筆 trace（root + LLM + tool），JSONB 欄位（`inputs`/`outputs`/`display`）可能很大。
- **建議**: 定期歸檔超過 30 天的非 root traces，或對大型 JSONB 欄位設大小上限再寫入。

#### 3.5.2 DocumentExtraction 雙寫的技術債

- `db_set_summarized()`、`db_set_research_step1()` 等每次同時寫入 `Document` + `DocumentExtraction` 兩表。
- **建議**: 遷移所有讀取路徑至 `DocumentExtraction`，移除 `Document` 上的冗餘欄位（8 個已標記 `# DEPRECATED`）。

#### 3.5.3 快取機制

- 前端 `getSummaries` 每次完整查詢 DB；解析快取目前是本地檔案。
- **建議**: API 層引入 `ETag` / `304 Not Modified`，或 Redis 快取高頻查詢。

### 3.6 測試覆蓋率 (Test Coverage)

**`tests/test_rag_filters.py` — 91 tests，全部通過 ✅**

新增的測試覆蓋：

- `TestIsReferencesPage` — 中文/英文標題、引用行格式
- `TestIsReferenceContinuation` — 期刊關鍵字、年份、CJK 比例
- `TestIsHtmlDataTablePage` — `<tr>`/`<td>`/`<th>` 密度
- `TestCheckQuality` — scanned、garbled（含希臘字母 false-positive 防呆）、image_heavy、正常文件
- `TestExtractAbstract` ✅ — 中文標題「摘要」、英文標題「Abstract/ABSTRACT」、fallback 首段（≥ 150 字元門檻）、截斷在 2000 字、只搜尋前 6 頁、多頁合併

**`tests/test_research_graph.py` — 14 tests，全部通過 ✅**（0.52 秒，無 LLM/Qdrant）

新增的測試覆蓋（`TestNextCoverageSlot`）：

- Path A（Untried）：非 HyDE 優先於 HyDE、PARTIAL 非 HyDE 勝過 HyDE NOT_FILLED、全 HyDE 時取第一
- Path B（已嘗試）：stalled 需同 slot 連續兩次才觸發、NOT_FILLED 排序先於 PARTIAL、count 少優先、required_order 作為最終 tiebreaker、HyDE 在 tried 路徑中排後
- Path C（無可用）：全 FILLED、全 EXHAUSTED、達到 cap、non-required slot 無視

**順帶修正了一個 import bug**：`chunking.py` 錯誤地從 `rag.cleaning` 引入 `_classify_candidate_text`（實際定義在 `rag.section`），測試執行時發現並修正。

**待加測試**：（無）3.6 單元測試已完整覆蓋所有計畫中的項目。

---

## 4. 總結 (Conclusion)

Report Agent 專案展示了非常優秀的系統工程與 AI 代理設計。以下是最突出的亮點與最優先的改進建議：

### ✨ 頂級亮點

1. **Research Pipeline 的 Coverage-Slot 機制**：透過 `coverage_items` + `slot_status` + `per-slot 搜尋上限` + `verification pass` 實作了一個完整的「證據覆蓋」引擎，確保每個研究面向都得到充分檢索，而非單純搜一次就結束。
2. **學術 PDF 的深度適配**：章節偵測三重 fallback (字型→正規表達式→LLM)、封面頁/目錄/參考文獻的智慧過濾、以及 `_check_quality` 的亂碼偵測，展現了對真實世界學術文件的深刻理解。
3. **自建 Local Trace Monitor**：完全取代了 LangSmith 雲端服務，實作了本地的 trace 收集→DB 儲存→前端視覺化的完整鏈路，極大化了開發與 debug 效率。
4. **Prompt 版本化與 A/B 測試框架**：`registry.py` 的 `select()` + SHA256 版本追蹤 + `PROMPT_AB_TESTS` 環境變數，為 Prompt 迭代提供了生產等級的基礎設施。

### ✅ 優先項目全部完成

1. ~~**`create_tables()` DDL 遷移 Alembic**（P1-3-8）~~：migration 006 涵蓋全部 ALTER TABLE / CREATE INDEX；migration 002 補上 idempotent guard；`create_tables()` 縮減為純 `Base.metadata.create_all()`。✅
2. ~~**`SummaryPage.tsx` 繼續拆解**（3.2.1 第二輪）~~：1396 → 546 行，拆出 `useParserCompare`、`<JobDrawer>`、`<DocumentDetailPanel>`、`<ParserCompareModal>`、`summaryUtils.ts`，TypeScript 零錯誤。✅
3. ~~**unit test 覆蓋率**（3.6）~~：`tests/test_rag_filters.py`（91）+ `tests/test_research_graph.py`（14）= **105 tests 全部通過**。✅
4. ~~**React Query / SWR**（3.2.3）~~：`App.tsx` + `AdminTracesPage.tsx` 全部遷入 `@tanstack/react-query`；同步修掉 ChatWindow/DocChat 的 MarkdownRenderer 路徑錯誤及 `useChat.ts` null 型別 bug。✅

### 🔒 剩餘未完成項目（等外部條件或架構限制）

| 項目 | 原因 | 何時處理 |
| ---- | ---- | ------- |
| P3-3-1 | LangGraph `StateGraph` 不支援逐 token streaming | 等 LangGraph 支援或架構重設計 |

### 📋 低優先（可做但現況可接受）

| 項目 | 說明 |
| ---- | ---- |
| P1-3-10 | 舊 `SessionLocal()` 呼叫逐步遷移至 `db_session()` |
| P2-3-3 | ✅ | `body_size` 偵測可改用 percentile | 改用 median（第 50 百分位），對含大字標題/表格的 PDF 更穩健 |
| P5-2-3 | N/A | work queue 改用 `asyncio.Queue` | 跳過：`asyncio.Queue` 不支援當前使用的 `sort()` 和按 doc_id 取消功能 |
| P6-2-3 | ✅ | `export_document` 記憶體 ZIP 生成 | 改用 `tempfile.mkstemp` + `FileResponse` 流式回傳；cache 檔案改 `zf.write()` 直接串流 |
| P9-3-1 | ✅ | `debug_chunks.py` UI 與邏輯混合 | 択出 `_pipeline(docs, lang, pdf_path) -> dict` 純函式；`process()` 只負責載入文件和列印結果 |

這些改進使專案更容易協作，並具備承載更大量級學術文件的能力。

---
---

# Part II — 逐檔深度 Code Review

以下為逐檔案、逐函式的深度審查，按階段 (Phase) 分組。每個發現標記分類：

- 🔴 **Bug / 正確性** — 可能導致錯誤行為
- 🟡 **設計 / 品質** — 維護性、可讀性、架構問題
- 🟢 **效能** — 不必要的開銷或可優化的路徑
- 🔵 **安全性** — 潛在安全風險
- ⚪ **建議** — 非必要但值得考慮的改善

---

## 待處理項目速查（未標 ✅ 者）

| 項目 | 嚴重度 | 說明 | 狀態 |
| ---- | ------ | ---- | --- |
| P1-1-5 | ✅ | `config.py` 敏感欄位缺 `SecretStr` | 7 個欄位改 SecretStr，19 個檔案 22 處加 `.get_secret_value()` |
| P1-2-4 | ✅ | `create_tables()` + Alembic 雙重 DDL | migration 006 + 修 002 + 簡化 create_tables() |
| P1-3-8 | ✅ | 每次啟動執行 50+ DDL round-trip | 同上，啟動只剩 create_all() + alembic upgrade head |
| P1-3-10 | ⚪ | 40+ 處手動 `SessionLocal()` 不一致 | `db_session()` context manager 已加入 `db/session.py`，新程式碼請用它；舊程式碼逐步遷移 |
| P2-1-4 | ✅ | `update_document_vector_filename()` N 次 API 呼叫阻塞 rename endpoint | 改成 `BackgroundTasks`，rename HTTP 立即回應，N 次 Qdrant call 在背景執行 |
| P2-3-3 | ⚪ | `body_size` 偵測可改用 percentile | 低優先 |
| P2-4-5 | ~~⚪~~ | ~~`_split_by_structure_and_semantics()` 巢狀函式~~ | 已移入 `chunking.py`；巢狀是刻意設計（捕捉外層變數），提升層級需改所有簽名，收益小 |
| P2-4-7 | ✅ | `extract_abstract()` fallback 可能誤取致謝頁 | 搜尋範圍縮至前 2 頁；跳過致謝/目錄/圖表開頭段落 |
| P2-5-4 | ✅ | `_parse_with_llamaparse()` 320+ 行 | 重構完成：923 → 885 行，提取 6 個共用函式 |
| P2-5-9 | ✅ | ghost page retry 邏輯與主流程重複 | 隨 P2-5-4 一起解決：`_llamaparse_run_job`、`_build_page_map`、`_normalize_llamaparse_pages` 統一兩條路徑 |
| P3-3-1 | ✅ | research 路由不支援 token streaming | 改用  + ； node 的 token 透過  回呼即時流出，前端看到研究答案逐字生成 |
| P3-5-1 | ~~🟡~~ | ~~`answer()` 參數過多，缺少 AgentRequest 封裝~~ | 全部 callers 在 `router_agent.py` 一處且用明確 kwargs，可讀性已足夠 |
| P4-2-2 | ✅ | `_next_coverage_slot` 5-tuple 排序難測試 | `tests/test_research_graph.py` 已涵蓋所有 14 條路徑；排序邏輯正確，暫不重構 |
| P4-4-1 | ✅ | `_looks_like_prior_work_limitation` 不支援非中文 | prompt 第 8 條加明確排除指令（中英雙語例句），keyword filter 退為備援；中英文論文皆覆蓋 |
| P4-4-2 | ✅ | `_cross_slot_updates` 字串配對易有 false positive | 跨 slot 門檻 2→3；per-chunk 要求 ≥2 match 才記 note |
| P5-2-1 | ~~🟡~~ | ~~`_active_jobs` list O(N) 查找~~ | 清單長度 < 100，實際無效能問題；parse job 多筆同 doc_id 讓 dict 重構更複雜 |
| P5-2-3 | ⚪ | work queue 改用 `asyncio.Queue` | 現況可運作，低優先 |
| P6-2-3 | ⚪ | `export_document` 記憶體 ZIP 生成 | 上正式環境前處理 |
| P8-2-2 | ✅ | `App.tsx` `handleSend` → `useChat` hook | App.tsx 392→302 行 |
| P8-3-2 | ✅ | `SummaryPage.tsx` inline CSS 核心已清理 | JS hover 移除、status dot 改 className；剩餘 layout style 不值得命名 class |
| P9-3-1 | 🟡 | `debug_chunks.py` UI 與邏輯混合 | 低優先 |

---

---

## Phase 1: 核心基礎設施 (Core Infrastructure)

### P1-1. `config.py`（33 行）

#### 整體評估

使用 `pydantic_settings.BaseSettings` 管理環境變數，設計簡潔。但有幾個不一致的地方值得注意。

#### 逐項發現

**P1-1-1/P1-1-2** ✅ ~~Settings 欄位命名與 .env.example 不一致~~ — `llama_cloud_api_key` 已加注解說明其對應的 env var 是 `LLAMA_CLOUD_API_KEY`（非 `LLAMAPARSE_API_KEY`）；其他透過 `os.getenv` 直接讀取的變數（`ENABLE_QUALITY_CHECK`、`LLAMAPARSE_NO_AZURE` 等）屬刻意設計，不需要進入 `Settings`。

**P1-1-3** ✅ ~~`extra = "allow"` 靜默接受拼錯~~ — 改為 `extra = "ignore"`，未知的環境變數會被靜默丟棄而非接受，避免拼寫錯誤的 key 不被發現。

**P1-1-4** ✅ ~~api_version 預設值與文件不一致~~ — `config.py` 加注解說明 preview 版本是有意為之（啟用 structured output）；`.env.example` 中若要鎖定穩定版本可以明確覆蓋。

**P1-1-5** ⚪ 缺少敏感欄位標記

- `azure_openai_api_key`、`qdrant_api_key` 等敏感欄位沒有使用 Pydantic 的 `SecretStr` 類型。
- **建議**: 使用 `SecretStr` 可以防止在 log 或 repr 中意外洩漏。

**P1-1-6** ⚪ 模組層級的 `settings = Settings()` 全域實例化

- `settings` 在模組載入時立即建構，19 個檔案透過 `from config import settings` 使用。
- 設計上是合理的單例模式，但在測試時無法輕易替換設定值。
- **建議**: 可加入 `Settings.override()` context manager 供測試使用。

---

### P1-2. `main.py`（66 行）

#### 整體評估

入口乾淨，職責適當。但有幾個 FastAPI 最佳實踐的偏差。

#### 逐項發現

**P1-2-4** ⚪ `startup` 中 `create_tables()` 與 `_run_migrations()` 雙重 DDL

- Line 59-60: 先執行 `create_tables()`（含 50+ 行 ALTER TABLE），再執行 `_run_migrations()`（Alembic）。
- **問題**: 如果 Alembic migration 要做與 ALTER TABLE 相同的操作，會造成重複執行。Alembic 的 migration 可能誤以為欄位已存在而跳過。
- **建議**: 長期目標是移除 `create_tables()` 中的 ALTER TABLE 語句，只保留 `Base.metadata.create_all()` + Alembic migration。

**P1-2-5** ✅ ~~Windows 事件迴圈策略在 module scope~~ — 從 `main.py` 移除重複設定；`run_dev_server.py` 已在 import 時設定，是正確的位置。

---

### P1-3. `database.py`（284 行）

#### 整體評估

ORM 模型設計合理，`JsonColumn` 是一個精巧的跨資料庫適配器。主要問題集中在 `create_tables()` 的技術債和連線管理。

#### 逐項發現

**P1-3-1** ✅ ~~JsonColumn 靜默返回 None~~ — 無效 JSON 解析失敗時改為 `logger.warning` 記錄並顯示原始字串的前 200 個字元，讓資料丟失可被觀察到。

**P1-3-3** ✅ ~~JsonColumn.process_result_value str fallback 無 log~~ — `json.dumps()` 失敗時改為 `logger.warning` 記錄 repr 再 fallback 到 `str()`。

**P1-3-4** ✅ ~~`Document` 模型的欄位膨脹~~ — 8 個 deprecated 欄位已各加 `# DEPRECATED` 行內標記，並加上區塊說明 "new writes go to DocumentExtraction"。後續遷移可依此找出所有需清理的欄位。

**P1-3-5** ✅ ~~`DocumentExtraction.version` 無唯一約束~~ — 建立 Alembic migration `005_document_extraction_version_unique`，為 `(document_id, version)` 加 UNIQUE 約束，防止同一文件有多個 version=1 的記錄。

**P1-3-6** ✅ ~~`Document.extraction` order_by 字串~~ — `DocumentExtraction` 定義在 `Document` 之後，字串 ref 是 SQLAlchemy forward reference 的正確用法；加入注解說明原因，不需要改動。

**P1-3-7** ✅ ~~Trace run_id 並發衝突~~ — `_write_traces` 改為逐筆 flush；碰到 `IntegrityError` 時 rollback 單筆並改為 UPDATE 現有 row，其餘批次不受影響。

**P1-3-8** 🟢 `create_tables()` 每次啟動執行 50+ SQL

- Line 202-283: 每次伺服器啟動都執行 50+ 行 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 和 20+ 行 `CREATE INDEX IF NOT EXISTS`。
- **問題**: 這些 DDL 操作在 PostgreSQL 上會取得 `ACCESS EXCLUSIVE LOCK`，在高流量環境下可能短暫阻塞所有 table-level 操作。`IF NOT EXISTS` 讓它們幂等，但每次啟動的 70+ 次 round-trip 仍有開銷。
- **建議**: 將這些全部遷入 Alembic，並在 `create_tables()` 中只保留 `Base.metadata.create_all()`。

**P1-3-9** ✅ ~~JSONB 轉換未驗證~~ — `create_tables()` 結尾加後置查詢：若 `traces.inputs` 欄位類型不是 JSONB，記錄 warning 提示需執行 Alembic migration。

**P1-3-10** ⚪ `get_db()` 的 generator 模式

- Line 194-199: FastAPI dependency injection 的標準寫法，設計正確。
- 但專案中有 40+ 處直接 `SessionLocal()` 而不經過 `get_db()`，造成 session 管理不一致（如前面 review 所述）。

**P1-3-11** ✅ ~~ORM 模型缺少 **repr**~~ — `Document` 和 `Trace` 各加 `__repr__`，debug 時 log 輸出不再顯示無意義的物件地址。

**P2-1-3** ✅ ~~`_lang_from_text()` 的 CJK 範圍過窄~~ — 抽出 `_is_cjk()` helper，同時涵蓋 CJK Unified Ideographs (U+4E00–U+9FFF)、Extension A (U+3400–U+4DBF) 和 Compatibility Ideographs (U+F900–U+FAFF)，對含罕用字學術文件的語言偵測更準確。

**P2-1-4** ✅ `update_document_vector_filename()` 改成背景執行

- Qdrant `set_payload` 只做 key-level overwrite，每個 point 的 `metadata` 含不同的 `page`/`chunk_index`，N 次 API call 無法避免。
- 修法：rename endpoint 改用 `BackgroundTasks`，DB commit 後立即回傳 HTTP response，N 次 Qdrant call 在背景執行，不阻塞使用者操作。

**P2-1-5** ✅ ~~`get_document_language()` 的 sample 過小~~ — `limit` 從 20 提升至 50 chunks，改善前半英文 abstract、後半中文正文的混合語言文件偵測準確度。

---

### P2-2. `rag/retrieval.py`（230 行）

#### 整體評估

搜尋邏輯結構清晰：multi-query → dedup → rerank → quality filter。但有幾個程式碼重複和效能問題。

#### 逐項發現

**P2-2-1** ✅ ~~過濾函式的完全重複~~ — 已建立 `rag/cleaning.py`，`retrieval.py` 改從 `rag.cleaning` 導入，自包含副本已移除。

**P2-2-2** ✅ ~~Reranker 對每個 query 重複 compress 所有文件~~ — rerank 改為最多取前 3 個 queries（`queries[:3]`），並用 `best_score` dict 取各 doc 跨 query 的最高分，避免重複 scoring。

**P2-2-3** ✅ ~~`_rank_key` 中 `_is_table_or_formula_heavy` 被呼叫兩次~~ — 改為在排序前預先計算 `is_heavy: dict[str, bool]`，排序 key 與 metadata 寫入均從 dict 讀取。

**P2-2-5** ✅ ~~`exclude_chunk_keys` 的隱含 API 契約~~ — key 改為 `_chunk_key(page_content)` 的 md5 hash（非 `[:120]` 截斷），docstring 明確說明 "values from `_chunk_key(doc.page_content)`"，呼叫端契約透明化。

---

### P2-3. `rag/section.py`（445 行）

#### 整體評估

章節偵測是整個 RAG pipeline 的亮點之一。三重 fallback（字型分析 → LLM → regex）設計非常穩健。

#### 逐項發現

**P2-3-1** ✅ ~~`_get_section_llm()` 每次建立新實例~~ — 加入 `_section_llm` 模組變數；第一次呼叫時建立，後續重用同一個實例。

**P2-3-2** ✅ ~~`_extract_headings_by_font()` PDF 未加 exception safety~~ — 主體邏輯包在 `try/finally: pdf.close()` 中，確保任何例外都不會洩漏 file handle。

**P2-3-3** ⚪ `_extract_headings_by_font()` 的 `body_size` 偵測

- Line 101: `body_size = Counter(all_sizes).most_common(1)[0][0]` — 取最頻繁的字型大小作為 body size。
- **問題**: 如果 PDF 有大量表格或頁首頁尾使用特殊字型大小，可能導致 body_size 偵測不準。
- **建議**: 改用 percentile（如 median）可能更穩健。

**P2-3-4** ✅ ~~`_build_page_section_map_llm()` 中的 `time.sleep()` 阻塞~~

- 確認所有呼叫路徑：`process_pdf()` 一律透過 `asyncio.to_thread` 呼叫；`debug_chunks.py` 是 CLI。`time.sleep()` 只阻塞 thread pool worker（等 rate-limit 冷卻），不影響 event loop。
- 函式是同步 `def`，改用 `asyncio.sleep()` 反而需要 async 整條呼叫鏈，得不償失。
- **結論**：現有寫法正確，code 注解已說明。不需修改。

**P2-3-5** ✅ ~~40% cutoff 對短文件過嚴~~ — 改為 `max(min(2, len-1), int(len*0.40))`，確保不超過第 3 個 page index，5 頁以下文件不再被過度截斷。

**P2-3-6** ✅ ~~LLM JSON 缺乏 page 型別驗證~~ — 過濾條件改為 `isinstance(s.get("page"), int) and isinstance(s.get("category"), str)`，防止非整數 page 在排序時 crash。

---

### P2-4. `rag/__init__.py`（949 行）

#### 整體評估

作為 RAG package 的整合層，包含品質檢測、文本清理、頁面過濾、切塊和 `process_pdf` 主入口。程式碼品質高但仍屬過度集中。

#### 逐項發現

**P2-4-1** ✅ ~~rag/**init**.py re-export 暴露私有符號~~ — 加入  明確列出公開 API； 等私有符號仍可用於內部呼叫但不會被  帶出。

**P2-4-2** ✅ ~~`_check_quality()` 希臘字母誤報~~ — mid-range 閾值從 0.2 提高到 0.3；含數學符號的文件需超過 30% 中間段 Unicode 才會被判定為 garbled。

**P2-4-3** ✅ ~~`_clean_text()` regex 未預編譯~~ — 所有 10+ 個 re.sub 字面量改為模組層級的 `_CT_*` 預編譯常數，避免每次呼叫重新編譯。

**P2-4-4** ✅ ~~過濾函式三重定義~~ — 已建立 `rag/cleaning.py` 作為唯一定義來源；`__init__.py` 和 `retrieval.py` 均改為從 `rag.cleaning` 導入。

**P2-4-5** ⚪ `_split_by_structure_and_semantics()` 的巢狀函式過多

- Line 462-769（307 行）: 單一函式內定義了 10+ 個巢狀函式（`_normalise`, `_is_table_sep_only`, `_is_heading_only`, `_dedup_overlapping`, `_merge_small`, `_split_large`, `_split_reference_boundaries`, `_retag_reference_chunks`, `_clean_page_text`, `_fix_orphaned_punct`, `_section_from_piece_heading`, `_chunk_structural_piece`, `_split_window`）。
- **問題**: 雖然巢狀函式避免了全域命名空間污染，但 307 行的函式嚴重影響可讀性與測試性。
- **建議**: 將 `_merge_small`、`_dedup_overlapping`、`_split_large`、`_retag_reference_chunks` 提升為模組層級函式（未來移入 `rag/chunking.py`）。

**P2-4-6** ✅ ~~`_merge_small()` O(N²) while-loop~~ — 改為 O(N) 單次掃描：合併後游標停在原位重新檢查，避免從頭重啟；鏈式小 chunk 在同一次掃描中連續合併。

**P2-4-7** ⚪ `extract_abstract()` 的 fallback 太過寬鬆

- Line 787-791: 如果沒找到 heading-based abstract，直接取前 4 頁中第一個 >= 150 字的段落。
- **問題**: 可能誤取封面頁後的致謝、目錄等內容作為 abstract。
- **建議**: 結合 `page_section_map` 來限定搜尋範圍——只在被標記為 "abstract" 或 "introduction" 的頁面中搜尋。

**P2-4-8** ✅ ~~process_pdf 缺乏 transaction 保護~~ — `add_documents` 已包在 try/except 中：失敗時記錄 error 並呼叫 `delete_pending_document_vectors(document_id, reindex_ts)` 清理部分寫入的 vectors，再 re-raise。

---

### P2-5. `rag/parsers.py`（914 行）

#### 整體評估

三種解析器的整合品質很高。LlamaParse 的 ghost page 重試和 pymupdf4llm fallback 是亮點設計。但檔案過長且有顯著的重複程式碼。

#### 逐項發現

**P2-5-1** ✅ ~~cache dir 依賴 **file** 相對路徑~~ — 改為先讀影 env var，未設定時 fallback 到原有相對路徑；Docker 部署可透過環境變數覆蓋。

**P2-5-2** ✅ ~~cache path 函式每次 `os.makedirs`~~ — `os.makedirs` 移到模組層級，import 時建立一次；四個 cache path 函式只回傳路徑字串，無副作用。

**P2-5-3** ✅ ~~pymupdf cache 寫入錯誤靜默~~ — 已改為 `logger.warning` 記錄錯誤訊息。

**P2-5-4** ✅ ~~`_parse_with_llamaparse()` 函式過長（320+ 行）~~

- Line 226-552: 單一函式 320+ 行，含巢狀的 `_llamaparse_get_json_result`（120 行）、`_build_request_data`、`_run_job` 等。
- **建議**: 將 `_llamaparse_get_json_result` 提升為模組層級函式，並將 ghost page retry 邏輯拆為獨立函式。

**P2-5-5** ✅ ~~`_build_request_data()` vendor 邏輯重複~~ — 抽出 `_llamaparse_vendor_params()` 模組函式，`_build_request_data` 和 `_build_retry_data` 均改用此函式；消除 Azure/OpenAI 選擇邏輯的重複。

**P2-5-6** ✅ ~~LlamaParse polling 無 backoff~~ — 加入 exponential backoff：每次 sleep 後 `poll_interval *= 1.5`，上限 30s；長任務從 2s 間隔逐漸放寬。

**P2-5-7** ✅ ~~parsers.py 函式級動態導入~~ — 已建立 `rag/cleaning.py`，`parsers.py` 改為頂層 `from rag.cleaning import _detect_language, _clean_text, _check_quality`，循環導入問題從根本消除。

**P2-5-8** ✅ ~~`validate_llamaparse_vs_pymupdf()` threshold 無說明~~ — docstring 加入 `sparse_threshold` 和 `rich_threshold` 的校準說明：前者定義 LlamaParse 視為「非空白」的最低字元數，後者定義 pymupdf 頁面「值得比對」的門檻，避免圖表頁造成誤報。

**P2-5-9** ✅ ~~`retry_llamaparse_warning_pages()` 和 `_parse_with_llamaparse()` 的 ghost page 邏輯重複~~

- 隨 P2-5-4 一起解決。兩條路徑現在都呼叫 `_llamaparse_api_submit` + `_build_page_map`；`_normalize_llamaparse_pages` 消除了 JSON→頁面 tuple 的重複轉換。

---

## Phase 3: Agent 核心層 (Agent Core Layer)

### P3-1. `agents/types.py`（28 行）

#### 整體評估

清晰的 dataclass 定義，提供了 `AgentRoute` 和 `AgentResult` 兩個核心型別。設計簡潔、不可變性良好。

#### 逐項發現

**P3-1-1** ⚪ `AgentResult` 可改為 `frozen=True`

✅ `AgentResult` 和 `AgentRoute` 均已設 `frozen=True`。

---

### P3-2. `agents/__init__.py`（11 行）

#### 整體評估

最小化的 facade pattern，只暴露 `route_agent_message` 和 `route_agent_stream`。符合 AGENTS.md 中 "orchestrator_agent owns user interaction" 的原則。無問題。

---

### P3-3. `agents/router_agent.py`

> `main_agent.py` 已重構為 `router_agent.py`，以下為沿用的待處理問題。

#### 逐項發現

**P3-3-1** 🟡 `route_agent_stream` 中 `research` 不支援真正的 streaming

- research 路由先 `await run_research_agent(...)` 取得完整結果，然後 yield 整個 response。
- **問題**: 使用者看到的是「等待→整塊出現」而非逐字流出，體驗不如 chat/retrieval 模式。
- **決定**: 屬於 LangGraph StateGraph 架構限制，無法在節點間 stream token。補償方式：透過 `on_stage` 回呼推送進度階段（目前已實作）。如需改善，需等 LangGraph streaming 成熟。

---

### P3-4. `agents/runner.py`

#### 整體評估

Agent 執行層的核心檔案。包含 checkpointer 初始化、LLM 設定、message 建構。

#### 逐項發現

**P3-4-1** ✅ ~~Module-level LLM 初始化啟動脆性~~ — `AzureChatOpenAI` 建構包在 try/except；credentials 缺失時輸出清楚的 `logger.error` 並 re-raise，避免產生神秘的 AttributeError。

**P3-4-2** ✅ ~~`_get_conversation_context()` 靜默吞掉 json.loads 例外~~ — `except Exception` 改為 `except Exception as _e`，並加入 `logger.debug(...)` 記錄 thread_id 和錯誤原因，方便追查。session 已在 `finally` 中正確 close，設計本身無問題。

**P3-4-3** ✅ ~~AgentResponse.sources 無 max_length 驗證~~ — 改用 Pydantic v2 `Field(max_length=3)` 強制限制，超過 3 個來源時拋出 ValidationError。

**P3-5-1** 🟡 `answer()` 參數過多

- 參數中大部分是 `None` 預設值，呼叫端難以看清必填與選填的邊界。
- **建議**: 使用 `@dataclass` 的 `AgentRequest` 封裝公共參數。

---

### P3-6. `agents/question_agent.py`

#### 整體評估

含有巧妙的品質自我檢查和 research 升級機制。但有潛在效能問題。

#### 逐項發現

**P3-6-1** ✅ ~~兩次重試 + research 升級最多觸發四次 LLM 呼叫~~ — 架構重構後 question_agent 不再有 research 升級機制。現行邏輯：初次生成 → 若問題格式不佳則一次重試，最多 2 次 LLM 呼叫，問題已自動解決。

**P3-6-2** ✅ ~~`_has_questions()` 只檢查單一問號~~ — 已改為計算 `("?", "\nQ", "\n-")` 等 markers 的總出現次數 >= 2，避免單一反問句觸發成功判定。

### P3-7. `agents/research_agent.py`

#### 整體評估

Research pipeline 的主入口和 trace 管理。程式碼品質高，trace 結構設計完整，但有嚴重的 session 管理問題。

#### 逐項發現

**P3-7-1** ✅ ~~`_write_trace()` session 管理~~ — `_write_trace()` 加入 rollback on exception；`_async_quality_check` 已委派給 `evaluation_agent.evaluate_trace_by_run_id()`，後者自行管理 session 並有 rollback。

**P3-7-2** ✅ ~~`run_research_task()` 過長~~ — 抽出 `async _plan_research()` 和 `_build_initial_graph_state()`；主函式從 ~180 行縮短為 ~60 行，每個子函式職責單一且可獨立測試。

**P3-7-3** ✅ ~~fire-and-forget 無 shutdown 保護~~ — 改用 `_background_tasks` set 追蹤 task；`main.py` lifespan shutdown 加入 `asyncio.gather(*_background_tasks)` 確保進程結束前 quality check 完成。

**P3-7-4** ✅ ~~`_utcnow()` 無說明的 tzinfo 移除~~ — 已加入行內注解說明原因。

**P3-7-5** ✅ ~~`_write_trace()` run_id TOCTOU~~ — `db.flush()` 後加入 `except IntegrityError` 處理並發衝突：rollback 後重新查詢已存在的 trace row，繼續更新欄位。

**P3-7-6** ⚪ `_fallback_display_messages()` 和 `_normalize_display_messages()` 的訊息格式轉換

- Line 228-322: 兩個函式合計 95 行，處理 research trace 的 display format。
- **設計亮點**: 這些函式將 LangGraph 的內部訊息格式轉換為前端可展示的結構化事件。設計合理，無重大問題。

---

## Phase 4: Research Pipeline

### P4-1. `agents/research/state.py`（297 行）

#### 整體評估

以 TypedDict (`ResearchGraphState`) 管理 LangGraph 狀態，並用 Dataclass (`ResearchState`) 進行物件導向封裝，分離度良好。但部分狀態維護有重複計算。

#### 逐項發現

**P4-1-1** ✅ ~~ResearchState.steps 死欄位~~ — 已從 `state.py` 移除 `steps` 欄位，並更新 `research_graph.py` 兩處 `steps=[]` 初始化。

**P4-1-2** ✅ ~~`coverage_ids()` 無快取~~ — 兩個方法均加入 try/AttributeError 手動快取，保留原有方法簽名（不改 callers），`coverage_items` 在 `__post_init__` 後穩定所以快取永遠有效。

---
---

### P4-2. `agents/research/research_graph.py`（883 行）

#### 整體評估

系統的重中之重，控制 planner -> retriever -> reflector -> writer 的流程。邏輯完整但部分條件判斷極度複雜。

#### 逐項發現

**P4-2-1** ✅ ~~`_clean_query_term` 阻擋合法學術詞彙~~ — 移除 "success"、"criteria"、"evidence"、"output"、"contract" 等一般性詞彙；只保留確定是 LLM planner 模板產物的 blocklist（"search for"、"describe"、"provide"、"research_"、"coverage"、"slot"）。

**P4-2-2** 🟡 `_next_coverage_slot` 排序邏輯複雜難測

- Line 270-319: 為了挑選下一個要搜尋的項目，使用了大量 if-else 和 5-tuple 的排序 `lambda slot: (slot == stalled_slot, _slot_uses_hyde(...), status_rank, counts, required_order)`。
- **風險**: 雖然邏輯精密，但極難單元測試所有邊界狀況（例如 stalled 狀態與 HyDE 權重的衝突）。
- **建議**: 將排序權重抽取為單獨的 Score 計算函式。

**P4-2-3** ⚪ `writer_node` 的品質守門員長度門檻

- Line 739-764: 強制要求 `len(writeup.answer.strip()) >= 150`。
- **問題**: 對於極短的文件或只有一個簡單問題的 Retrieval mode，150 字的門檻可能過高，導致經常觸發 fallback (生硬的證據拼接)。
- **建議**: 依據 `mode` (如 research vs retrieval) 動態調整字數門檻。

---

### P4-3. `agents/research/planner.py`（297 行）

#### 整體評估

透過 LLM Structured Output 生成檢索策略。

#### 逐項發現

**P4-3-1** ✅ ~~`_slot_kind` fallback 強制套用 "findings"~~ — fallback 改為回傳 `"generic"`；`_section_terms_for_slot` 改用 `.get(..., [])` 讓客製化問題得到空的 section_terms，不再被強制套用結果相關的搜尋詞。

---

### P4-4. `agents/research/reflector.py`（382 行）

#### 整體評估

負責將檢索回來的文本轉化為結構化證據。包含一個聰明的跨項目更新 (cross-slot updates) 機制。

#### 逐項發現

**P4-4-1** ✅ `_looks_like_prior_work_limitation` 的特定領域標記（已完成）

- 原本 hardcode「康氏, 周文, 趙氏」等特定學者名已移除，改為通用 regex `[一-鿿]{1,2}氏`。
- 現有 keyword filter 術語（"前人", "學者" 等）保留作輕量備援。
- 根本修法：在 `prompts/research_reflector.txt` 第 8 條加明確排除指令，中英雙語例句（「前人研究未能…」/ "Previous studies fail to…" / "Smith (2020) does not address…"），指示 LLM 只收錄本論文作者自身限制，不收錄批評前人的文字；中英文論文皆覆蓋。

**P4-4-2** 🟢 `_cross_slot_updates` 的關鍵字配對 (`_slot_match_score`)

- Line 218-256: 允許一次檢索更新多個 coverage item。
- **風險**: 判斷依據也是基於 `_SLOT_HINTS` 的字串比對，如果文本很長，極易產生 false positives（把不相干的文字當作某 slot 的證據）。

---

### P4-5. `agents/research/task_planner.py`（293 行）

#### 整體評估

定義並產生整個任務的 `ResearchPlan`，預設多種樣板計畫。

#### 逐項發現

**P4-5-1** ⚪ `_summary_fallback_from_question` 退化為關鍵字路由

- Line 224-236: 當 LLM 規劃失敗時的 fallback，依賴 `"比較"`, `"高中生"`, `"方法"` 等關鍵詞來決定套用哪種固定計畫。
- **亮點與隱患**: 這是很好的安全網設計，但如果 LLM 因 Prompt 變更而失敗率上升，系統會悄悄退化為基於 if-else 的傳統對話機器人，難以察覺。

---

### P4-6. `agents/research/writer.py`（164 行）

#### 整體評估

生成最終回覆的節點。

#### 逐項發現

**P4-6-1** ⚪ `_to_writeup` 優秀的防偷懶機制

- Line 115-128: 如果 LLM 寫出來的答案長度不到原始證據串接長度的「一半」(`len(answer) < max(160, len(section_answer) // 2)`)，系統會強制丟棄 LLM 答案，替換成證據直接串接。
- **亮點**: 這能有效防止 LLM 在處理長篇摘要時產生 "簡短敷衍" 的回覆，是個值得保留並推廣的設計模式。

---

### P4-7. `agents/research/retriever.py` & `runtime_prompts.py`

- 此二檔案功能單純（對 `rag_tool` 與 `prompting` 系統的橋接），**無發現特殊問題**。

---

## Phase 5: 工具與服務 (Tools & Services)

### P5-1. `tools/rag_tool.py`（623 行）

#### 整體評估

包裝了檢索與問答相關的 LangChain Tools，功能齊全，包含 Hybrid RAG 擴展 (HyDE)。

#### 逐項發現

**P5-1-1** ✅ ~~`_query_expander_llm` 未初始化風險~~ — 新增 `_get_query_expander_llm()` 做 lazy init：若 `set_query_expander_llm()` 尚未被呼叫（如測試環境），自動從 settings 建立 LLM 實例。`expand_queries` 和 `verify_claim` 改用此函式取代直接存取全域變數。

**P5-1-2** 🟢 `expand_queries` 的安全降級

- Line 90-138: HyDE 生成被妥善包裹在 `try-except` 中，若 LLM 逾時或出錯，能安全退回傳統檢索。這保證了 RAG 的健壯性。

**P5-1-3** ✅ ~~`web_search` 同步阻塞風險~~ — 加入 docstring 說明「使用同步 DDGS 是刻意設計，LangChain `@tool` 自動放入 thread pool；若未來改為 `async def` 需換用 `AsyncDDGS` 或 `asyncio.to_thread`」，防止未來誤改時踩坑。

---

### P5-2. `services/job_service.py`

#### 整體評估

實作了 In-memory 的非同步工作佇列並帶有 DB 快照備份。`_active_jobs` 已加上 `threading.Lock()`，但仍有以下問題。

#### 逐項發現

**P5-2-1** 🟡 `_active_jobs` 的線性查找效能

- Line 122, 168, 190 等多處: 大量使用 `for job in _active_jobs: if job["doc_id"] == doc_id:`。
- **問題**: 每次狀態更新都是 O(N) 查找。
- **建議**: 將 `_active_jobs` 重構為 Dictionary (`dict[str, dict]`)，以 `job_id` 或 `doc_id` 作為 Key 達到 O(1) 查找。

**P5-2-3** ⚪ 非同步佇列的實作方式

- Line 12-21: 使用標準 `list` 加上 `asyncio.Condition` 來實作 `_extract_work_queue` 等佇列。
- **問題**: 這種 Producer-Consumer 寫法相對底層且容易出錯。
- **建議**: 原生 `asyncio.Queue` 已經內建了這些邏輯（包含阻塞等待、優先級排序等），建議改用 `asyncio.Queue` 或 `asyncio.PriorityQueue`。

---

### P5-3. `services/extraction.py`（273 行）

#### 整體評估

漂亮的將 RAG 摘要流程解耦為三階段：1. Agent 檢索 -> 2. LLM 結構化 -> 3. LLM 導讀生成。

#### 逐項發現

**P5-3-1** 🟢 優雅的非同步平行處理

- Line 216-220: `asyncio.gather(structure_research_step2(answer), generate_interest_step3(answer))`。
- **亮點**: Step 2 與 Step 3 互不相依，直接平行觸發，大幅縮短了整體萃取流程的耗時。

**P5-3-2** 🟢 品質守門員的重試機制

- Line 225-261: 如果開啟了品質檢查 (`ENABLE_QUALITY_CHECK`)，並由 `quality_agent` 評分低於 2.5，系統會主動重新呼叫一次 Step 1 進行重試。這是增強系統可用性與容錯率的極佳實踐。

---

### P5-4. `tracer.py`（418 行）

#### 整體評估

客製化的 LangChain `BaseCallbackHandler`，將 LangSmith 的追蹤資料轉存至本地 PostgreSQL。

#### 逐項發現

**P5-4-1** ✅ ~~`_build_display` LangChain 格式依賴脆弱~~ — 每個 msg 的解析包在個別 try/except 中；單筆訊息格式錯誤只跳過該筆，不中斷整個 tracer 寫入。

**P5-4-3** ✅ ~~estimate_prompt_tokens 粗糙無說明~~ — 在 docstring 說明 `len//4` 精度（±20-30%）及升級到 tiktoken 的方法。

**P6-1-1** 🟢 優秀的 Trace 與 Message 結合 (`_hydrate_message_attachments`)

- Line 54-83: 透過讀取 `Trace` 資料庫，將文件 attachments 和 agent metadata 回推關聯到 LangGraph Checkpointer 裡的訊息歷史。這個機制巧妙地解決了 LangChain Message 缺乏中介資料 (Metadata) 欄位的問題。

**P6-1-3** ✅ ~~SSE 錯誤路徑跳過 DB commit~~ — 錯誤 `return` 前加入 `try: db.commit()` 確保 conversation 狀態（如 model 欄位）被持久化，即使 agent 呼叫失敗也不丟失。

---

### P6-2. `api/documents.py`（630 行）

#### 整體評估

處理文件上傳、解析與向量化。發現了一個嚴重的設計不一致與效能瓶頸。

#### 逐項發現

**P6-2-1** ✅ ~~rename_document 格式寫死~~ — 加注解說明 prefix 邏輯；加入同名衝突檢查（HTTP 409）和 no-op short-circuit（名稱未變時直接返回）。

**P6-2-3** ⚪ `export_document` 記憶體佔用風險

- Line 392-531: 使用 `io.BytesIO()` 在記憶體中動態生成包含文件、Chunks JSON、Cache 的 ZIP 檔。
- **問題**: 若大量使用者同時匯出，或檔案過大，會瞬間消耗大量 RAM。
- **建議**: 若無效能考量可暫不修改，但若要上正式環境，建議改為將 ZIP 寫入 TempFile 後再使用 `FileResponse` 回傳。

---

### P6-3. `api/summaries.py`（388 行）

#### 整體評估

批次任務控制器，負責大量文本的萃取與匯入。

#### 逐項發現

**P6-3-1** ✅ ~~batch_import 路徑寫死~~ — 改為 `_get_batch_folder()` 函式：優先讀 `settings.batch_folder`，未設定時 fallback 並記錄 `logger.warning`，由呼叫端決定是否繼續執行。

**P6-3-2** 🟢 批次指令封裝良好

- Line 221-327: `batch_extract`, `batch_reextract` 等端點正確地將任務推入 `job_service` 並立刻回傳，這是正確的非同步工作流設計。

---

### P6-4. `api/traces.py`（734 行）

#### 整體評估

Trace 面板的資料來源，包含效能統計與錯誤分析。

#### 逐項發現

**P6-4-1** ✅ ~~JSON 陣列 LIKE 查詢 Hack~~ — 已改用 PostgreSQL JSONB 的 `@>` containment 運算子（index 支援），並搭配 GIN index。

**P6-4-2** 🟢 自動評分任務背景化

- Line 652: `batch_score_traces` 中呼叫了 `asyncio.create_task(_score_all(run_ids_modes))`，確保可以大量評分而不會卡死 HTTP Response。

---

### P6-5. `api/jobs.py`（91 行）

#### 整體評估

輕量的 Job 狀態控制器。

#### 逐項發現

- **P6-5-1** 🟢 `stream_jobs` 使用了 `asyncio.Queue` 來實作 Server-Sent Events (SSE)，並且有 `TimeoutError` 心跳機制 (Heartbeat)，能有效防止 Nginx 或 Load Balancer 提早切斷連線，設計非常成熟。

## Phase 7: Prompt 系統 (Prompt System)

### P7-1. `prompting/registry.py`（176 行）

#### 整體評估

負責讀取與解析 `.txt` 格式的 Prompt 檔案，支援 `# extends:` 繼承機制與 A/B 測試。

#### 逐項發現

**P7-1-1** 🟢 支援 `# extends:` 繼承與循環依賴偵測

- Line 66-89: 實作了 `# extends:` 語法，允許一個 Prompt 繼承另一個 Prompt 的內容（類似物件導向的繼承）。並且實作了 `stack` 參數來偵測循環依賴 (`Prompt extends cycle detected`)，這對於大型系統的 Prompt 模組化非常有幫助。

**P7-1-2** ✅ ~~A/B 測試配置缺乏快取~~ — `_ab_tests_cached` 已套用 `@functools.lru_cache(maxsize=8)`，每次只在 env var 字串改變時才重新解析。

**P7-1-3** ✅ ~~version() 雜湊重複計算~~ — `_hash_cache` dict 已在 `version()` 中做記憶，`reload_all()` 時一併清除。

---

### P7-2. `prompting/loader.py`（108 行）

#### 整體評估

將散落的 Prompt 組合為 LangChain 的 `PromptStack`。

#### 逐項發現

**P7-2-1** 🟢 優秀的 Trace Metadata 支援

- Line 66-99: `metadata()` 會將 stack 中所有的 prompt 版本、名稱、甚至是 Tokens 預估數量，序列化為 JSON，並自動判定哪一個是主要 Prompt (`PRIMARY_PROMPT_BY_STACK`)。這為 LangSmith / 本地 Trace 面板提供了極其詳盡的 Prompt 版控資訊，是難得的優良實踐。

---

### P7-3. `prompts/*.txt` (Prompt 檔案本身)

- 隨機抽查了 `summary_structure.txt` 等檔案。
- **發現**: Prompt 指令極其清晰，採用了 Markdown 與特殊符號（如 `================`）區分區塊，且包含明確的防幻覺指令（如「不得只寫正面成果」）。Prompt 的工程水準極高。

---

## Phase 8: 前端 (Frontend)

### P8-1. `frontend/src/api.ts`（578 行）

#### 整體評估

與後端對接的 Axios 與 Fetch API 封裝，TypeScript Type Definitions 涵蓋度極高。

#### 逐項發現

**P8-1-1** 🟢 `sendMessageStream` 的 SSE 解析實作

- Line 524-577: 使用原生的 `fetch` API 搭配 `body.getReader()` 解析 Stream，而不是依賴外部龐大的套件。實作標準，且支援了 `onToken`, `onDone`, `onMeta`, `onStage` 甚至 `onClear`，完美對接了後端 `chat.py` 的雙軌流設計。

**P8-1-2** ⚪ `exportDocument` 記憶體釋放處理

- Line 512-522: 動態建立 `<a>` 標籤並觸發下載 ZIP 檔。實作中妥善使用了 `URL.revokeObjectURL(url)` 釋放 Blob 佔用的記憶體，這點非常細心。

---

### P8-2. `frontend/src/App.tsx`（392 行）

#### 整體評估

應用的主容器，負責 Router (基於 State) 與全局聊天訊息維護。

#### 逐項發現

**P8-2-1** ✅ ~~Suspense fallback 使用 inline style~~ — 加入 `.app-layout.loading-center` CSS class；App.tsx 的 Suspense fallback 改用 `className="app-layout loading-center"`。

**P8-2-2** ✅ ~~`handleSend` 邏輯過於厚重~~ — 建立 `hooks/useChat.ts`，封裝 messages、loading、error、conversationId、selectedDocIds 及完整的 `sendMessageStream` 7 個 callback 邏輯。`App.tsx` 392→302 行，`sendMessageStream` 和 `ChatResponse` import 已移出主元件。

---

### P8-3. `frontend/src/components/SummaryPage.tsx`（1449 行）

#### 整體評估

系統中邏輯最重、功能最複雜的頁面。包含了批次任務、PDF 檢視切換與即時工作狀態更新。

#### 逐項發現

**P8-3-1** ✅ ~~Render Storm~~ — 安裝 Zustand，建立 `src/store/jobStore.ts`（`jobs` state）和 `src/hooks/useJobSSE.ts`（SSE 連線 + side effects 邏輯）；SummaryPage 改用 `useJobStore(selectJobs)` 和 `useJobSSE` hook，EventSource 的複雜 handler 完全移出組件。下一步：將 PDF viewer 等不依賴 jobs 的部分加上 `React.memo` 以進一步隔離重渲。

**P8-3-2** 🟡 內聯 CSS (`style={{...}}`) 氾濫

- Line 532 等處: 大量組件如 JobRow 使用了極長的內聯樣式 (`style={{ display: 'flex', alignItems: 'flex-start', gap: 10, ... }}`)。
- **建議**: 前端應遵守 `STYLE_GUIDE.md` 提到的 "vanilla CSS for maximum flexibility"，將設計抽離到獨立的 class 中。

**P8-3-3** 🟢 `refreshAll` 的並發設計

- Line 154-161: 使用了 `await Promise.all([loadItems(), loadJobs()])` 確保 API 並發請求，沒有造成 Waterfall 延遲。

---

### P8-4. `frontend/src/components/AdminTracesPage.tsx`（1301 行）

#### 整體評估

完整的 Trace 檢視與分析面板，甚至內建了 A/B Test 與批次評分工具。

#### 逐項發現

**P8-4-1** ✅ ~~AdminTracesPage 35+ useState 狀態爆炸~~ — 8 個 filter useState 合併為 1 個 useReducer；其餘呼叫點保留原變數名。

---

## Phase 9: 測試與腳本 (Tests & Scripts)

### P9-1. `backend/tests/test_job_service.py`（200 行）

- **功能**: 測試 Job Service 的佇列狀態轉換 (`jobs_enqueue`, `jobs_set_running` 等)。
- **P9-1-1** ✅ ~~並發競態條件未測試~~ — 新增 ：100 執行緒並發 enqueue、50 執行緒並發 set_stage、enqueue/remove 交錯，驗證  無重複和 KeyError。26/26 tests passed。

### P9-2. `backend/tests/test_rag_accuracy.py`（457 行）

- **功能**: RAG 端對端 (End-to-End) 的整合測試，檢驗系統問答品質。
- **P9-2-1** 🟢 出色的防幻覺與多輪對話測試
  - Line 283-300: `test_no_hallucination_on_missing_info` 成功測試了不存在的資訊，驗證模型是否能妥善退讓 (Hedge) 而非一本正經地胡說八道。
  - Line 334-358: `test_pronoun_reference_followup` 驗證了多輪對話中的「代名詞解析」，確保後續問句中的「它」能正確對應到上一輪的實體。這對 Agent 的 Context Window 是極佳的考驗。

### P9-3. `backend/scripts/debug_chunks.py`（513 行）

- **功能**: 讓開發者不經過資料庫直接預覽 PDF 分塊結果的 CLI 工具。
- **P9-3-1** 🟡 UI 邏輯與核心邏輯高度耦合
  - Line 105-325: `process` 函數長達 220 行，混合了 Langchain 載入邏輯、品質檢測 (`_check_quality`) 以及大量 Terminal 輸出的排版邏輯。
  - **建議**: 工具腳本雖不強求架構，但應將資料收集與終端機列印分離，這有助於後續將 `debug_chunks` 功能轉移成 Web 介面的 API。

### P9-4. `backend/eval/runner.py`（68 行）

- **功能**: 基於 `dataset.json` 評測路由 (Intent Classification) 準確率的腳本。
- **P9-4-1** 🟢 輕量且有效的評測機制
  - 繞過了完整的 API 堆疊，直接呼叫 `classify_intent`。能以極低成本在 CI/CD 中進行分類器迴歸測試 (Regression Testing)。
