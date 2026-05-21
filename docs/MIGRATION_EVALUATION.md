# 整合評估：Report Agent → NCU AI Guidance（完整版）

> 評估日期：2026-05-19

---

## 一、整合範圍（最終確認）

| 項目 | 搬移 | 說明 |
|------|------|------|
| 研究計畫摘要顯示 | ✅ 資料 | `summary_json` export → NCU Projects 頁面 |
| 適性測試題目 | ✅ 資料 | 摘要欄位轉 assessment 格式 |
| Qdrant chunk 資料 | ✅ 資料 | `reports` collection → NCU RAG 基礎 |
| 研究計畫問答（RAG + LangGraph） | ✅ 程式碼 | 完整 LangGraph 多代理系統 |
| Extraction pipeline（生成摘要） | ❌ 不搬 | 仍在 Report Agent 跑，輸出 export 出去 |
| Admin 後台監控系統 | ❌ 不搬 | NCU 用 LangSmith 監控 |

---

## 二、現有資料盤點

| 資料 | 數量 | 位置 | 欄位 | 狀態 |
|------|------|------|------|------|
| Qdrant `reports` chunks | 14,819 points（459 份） | Report Agent Qdrant Cloud | `page_content` + metadata（doc_id / section / page / parser） | ✅ 全部索引完成 |
| `Document.summary_json` | 459 筆 | Report Agent PostgreSQL | `motivation, method, results, tags`（無 intro/questions） | ✅ 舊版，部分可用 |
| `DocumentExtraction.summary_json` | 22 筆 | Report Agent PostgreSQL | `motivation, method, results, intro, questions, tags` | ✅ 新版完整 |

---

## 三、問答架構：現狀 vs 目標

### 現狀（NCU `projects.py:71-89`）
```python
# TODO: 後續整合真實的 RAG 功能
reply = random.choice(mock_replies)   # ← 純 Mock
```

### 目標（Report Agent LangGraph）
```
學生提問
   ↓
router_agent（LangGraph）
   ├── 簡單問答 → retrieval_agent（Hybrid RAG：HyDE + dense + sparse + rerank）
   ├── 複雜問題 → research_agent（多步驟 LangGraph workflow）
   └── 一般聊天 → chat_agent
   ↓
LangGraph AsyncPostgresSaver（對話記憶）
   ↓
回覆
```

---

## 四、需搬移的程式碼模組

### 4.1 核心 Agent 程式碼

| 模組 | 路徑 | 用途 |
|------|------|------|
| router_agent | `agents/router_agent.py` | 意圖分類 + 路由 |
| retrieval_agent | `agents/retrieval_agent.py` | 文件 QA（最常用路徑） |
| research_agent | `agents/research/` | 深度多步驟合成 |
| chat_agent | `agents/chat_agent.py` | 一般對話 + 最終組合 |
| question_agent | `agents/question_agent.py` | 問題生成 |
| no_tool_runner | `agents/no_tool_runner.py` | 無工具 LLM 執行 |
| request_context | `agents/request_context.py` | user_id ContextVar |

### 4.2 RAG 工具層

| 模組 | 路徑 | 用途 |
|------|------|------|
| rag_tool | `tools/rag_tool.py` | Hybrid RAG 主入口 |
| 其他 retrieval 工具 | `tools/` | HyDE、dense、sparse、rerank |

### 4.3 Prompt 系統

| 模組 | 路徑 | 用途 |
|------|------|------|
| prompting registry | `prompting/` | prompt 載入 + 版本管理 |
| prompt 文字檔 | `prompts/` | 各 agent 的 system prompt |

---

## 五、基礎設施比較

| 項目 | Report Agent | NCU Guidance | 衝突 |
|------|-------------|-------------|------|
| LangGraph | 使用 | **無** | 需新增 |
| LangChain | 使用 | **無** | 需新增 |
| PostgreSQL | 有 | 有 `psycopg[binary]` | ✅ 可共用 |
| Qdrant | Cloud（獨立） | Cloud URL 或本地 | 需確認是否同帳號 |
| Azure OpenAI | 相同 config | 相同 config | ✅ |
| LangSmith | 使用 | 使用 | ✅ 共用 |

### 需新增的套件

```txt
# 加入 ncu_ai_guidance/requirements.txt
langchain>=0.3.0
langgraph>=0.2.0
langchain-openai>=0.2.0
langchain-community>=0.3.0
langchain-core>=0.3.0
tiktoken>=0.7.0
fastembed>=0.4.0
```

---

## 六、資料遷移計畫

### Task A：Qdrant `reports` collection

**方式一（最簡單）：** 如果 Report Agent 和 NCU 用同一個 Qdrant Cloud 帳號，直接讓 NCU 的 retriever 指向 `reports` collection，不需要搬。

**方式二（不同帳號）：** Qdrant Snapshot API
```bash
# 1. 建立 snapshot
POST https://{report_qdrant}/collections/reports/snapshots
# 2. 下載 .snapshot 檔案
# 3. 上傳至 NCU Qdrant（可改名為 ncu_project_chunks）
POST https://{ncu_qdrant}/collections/ncu_project_chunks/snapshots/upload
```

### Task B：摘要 Export 腳本（`scripts/export/export_to_ncu.py`）

```
輸入：Report Agent PostgreSQL
輸出：
  - projects_enriched.json（459 筆，有欄位的填值，沒有的填 null）
  - assessment_additions.json（22 筆完整題目，含 questions）
  - doc_id_map.json（ncu proj id → report agent doc.id integer）
```

**欄位對映（`Document.summary_json` 459 筆）：**

| NCU 欄位 | Report Agent 來源 | 備注 |
|---------|-----------------|------|
| `motivation` | `summary_json.motivation` | |
| `method` | `summary_json.method` | |
| `results` | `summary_json.results` | |
| `tags` | `summary_json.tags` | |
| `intro` | `null` | 舊版無此欄 |
| `questions` | `null` | 舊版無此欄 |

**覆寫（`DocumentExtraction.summary_json` 22 筆）：** 補上 `intro` / `questions`。

### Task C：ID 對應

NCU id 格式為 `"proj001"` ~ `"proj459"`，Qdrant metadata 的 `document_id` 為 integer（1~459）。需建立對應表，讓 retriever filter 時能正確過濾。

```python
# doc_id_map.json
{ "proj001": 1, "proj002": 2, ... }
```

---

## 七、認證 / Thread 適配

| 面向 | Report Agent | NCU Guidance | 解法 |
|------|-------------|-------------|------|
| 使用者識別 | JWT（user.id integer） | Firebase uid（string） | router_agent 改接 Firebase uid 作 user_id |
| thread_id | conversation.id | Firebase push key | `f"{firebase_uid}:project:{project_id}"` |

---

## 八、NCU 端修改清單

### 後端
- `requirements.txt`：加 LangGraph / LangChain 套件
- `app/models/schemas.py`：`Project` 加 optional 欄位
- `app/routes/projects.py`：改讀 `projects_enriched.json`；`chat_with_project` 改呼叫 router_agent
- `app/services/retriever.py`：加 `reports` collection，filter 用 doc_id_map 轉換
- 新增 `app/agents/`：搬入 router/retrieval/research/chat/question agent
- 新增 `app/tools/`：搬入 RAG 工具
- 新增 `app/prompts/` + `app/prompting/`：prompt 系統
- PostgreSQL 連線 + `AsyncPostgresSaver.setup()`

### 前端
- `src/types/index.ts`：`Project` 加 6 個 optional 欄位
- `src/pages/ProjectsPage.tsx`：`ProjectSummary` 換真實欄位（fallback 佔位文字）
- `src/pages/AssessmentPage.tsx`：確認 `mode: "project"` 題目可顯示

---

## 九、實施 Phase 計畫

### Phase 1（2 天）：資料準備（agent 無關，先做）
- 確認 Qdrant 帳號是否共用
- 跑 `export_to_ncu.py`，產出三個 JSON 檔
- NCU 後端改讀新 JSON，schema 加欄位
- NCU 前端 ProjectSummary + Assessment 更新
- **驗證：** Projects 頁面 459 筆有部分摘要，22 筆完整；Assessment 多 22 道題

### Phase 2（3 天）：Agent 環境建立
- NCU requirements.txt 加套件
- PostgreSQL 連線 + LangGraph checkpointing setup
- 複製 agents/、tools/、prompts/、prompting/ 到 NCU

### Phase 3（3 天）：接線與適配
- NCU `chat_with_project` → 呼叫 router_agent
- Firebase uid 作 user_id，thread_id 格式適配
- Config 環境變數對應
- LangSmith tracing 確認

### Phase 4（2 天）：測試與部署
- 問答品質測試
- 多輪對話記憶驗證
- Render 部署確認

---

## 十、不搬移的部分

| 項目 | 理由 |
|------|------|
| Extraction pipeline（step1-4） | 留在 Report Agent 跑，export 結果就好 |
| Admin TracePanel / Evaluation / Dataset | NCU 不需要 |
| `traces_v2` / `observations` DB schema | LangSmith 負責 observability |
| Report Agent JWT 系統 | NCU 用 Firebase Auth |
| Alembic migrations（業務表） | 不搬 Report Agent 的業務 DB schema |

---

## 十一、工作量總覽

| Phase | 項目 | 估計 |
|-------|------|------|
| 1 | 資料 export + NCU 前後端更新 | 2d |
| 2 | Agent 環境 + 程式碼複製 | 3d |
| 3 | 接線 + 認證適配 | 3d |
| 4 | 測試 + 部署 | 2d |
| **合計** | | **~10 天** |
