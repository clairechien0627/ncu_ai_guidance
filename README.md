# Report Agent

學術 PDF 分析用 RAG 研究助理。使用者上傳 PDF 後，系統解析並切割文件、將 embedding 存入 Qdrant，多代理人系統負責回答問題、合成研究、生成學習導讀。

詳細多代理人架構請參閱 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 啟動

### PostgreSQL（含 pgvector）

```powershell
cd D:\try
docker-compose up -d
```

首次啟動後執行一次：

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### Backend

```powershell
cd backend
.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8200
```

### Frontend

```powershell
cd frontend
npm run dev
```

- 前端：<http://localhost:5173>
- API 文件：<http://localhost:8200/docs>
- 健康檢查：<http://localhost:8200/health>

---

## 技術棧

| 層次 | 技術 |
| ---- | ---- |
| Web 框架 | FastAPI |
| Agent 框架 | LangGraph + LangChain |
| LLM | Azure OpenAI (GPT-4.x / GPT-4o-mini) |
| 向量搜尋 | Qdrant Cloud（dense + sparse hybrid） |
| 資料庫 | PostgreSQL 16 + pgvector（Docker） |
| Checkpoint | LangGraph AsyncPostgresSaver |
| 長期記憶 | LangGraph AsyncPostgresStore（pgvector 語意搜尋） |
| 可觀測性 | Langfuse（prompt 版本、token、quality） |

---

## 重要目錄

| 目錄/檔案 | 說明 |
| --------- | ---- |
| `agents/` | 各 agent 進入點與工具型 runner |
| `agents/research/` | Research graph（scheduler → executor → writer） |
| `api/` | FastAPI 路由（chat、documents、traces、health） |
| `services/` | 背景任務、記憶服務、文件摘取管線 |
| `prompts/` | 所有 prompt 文字檔（runtime 熱載入） |
| `prompting/` | Prompt registry、stack loader、Langfuse 整合 |
| `rag/` | PDF 解析、切割、embedding、Qdrant 存取 |
| `db/` | SQLAlchemy models、migration（Alembic） |
| `tools/` | AgentContext、RAG tool 定義 |

---

## 資料存儲

| 存儲 | 用途 | 預設位置 |
| ---- | ---- | ------- |
| PostgreSQL（pgvector） | 文件、對話、traces、checkpoint、長期記憶 | `127.0.0.1:5432/reportdb`（Docker） |
| Qdrant | 文件 RAG 向量搜尋（dense + sparse） | Cloud（`QDRANT_URL` + `QDRANT_API_KEY`） |
| Disk / Azure Blob | PDF 原始檔案 | `backend/uploads/` 或設定的 blob container |

---

## 環境變數

```text
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AZURE_OPENAI_API_VERSION
AZURE_CHAT_DEPLOYMENT
AZURE_MINI_DEPLOYMENT
AZURE_EMBEDDING_DEPLOYMENT
DATABASE_URL
QDRANT_URL
QDRANT_API_KEY
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
LANGSMITH_API_KEY
PROMPT_AB_TESTS
ENABLE_QUALITY_CHECK
AZURE_STORAGE_CONNECTION_STRING
AZURE_STORAGE_CONTAINER
CORS_ORIGINS
```

---

## Prompt 系統

Prompt 檔案：`backend/prompts/`  
Prompt 基礎設施：`backend/prompting/registry.py`、`backend/prompting/loader.py`

```text
chat_default:       core + chat_mode
question_default:   core + question_skill
retrieval_default:  core + retrieval_capability
evaluation_default: core + evaluation_agent
router_default:     core + route_coordinator
research_runtime:   core + task_planner + research_scheduler + research_planner + research_reflector + research_writer
extract_step2:      core + summary_structure
extract_step3:      core + question_generator
extract_step4:      core + summary_quality
```

Prompt 熱載入：

```http
POST /api/prompts/reload
```

---

## 測試

語法檢查：

```powershell
cd backend
.venv\Scripts\python.exe -m py_compile agents\runner.py agents\router_agent.py agents\research\agent.py agents\research\research_graph.py agents\research\scheduler.py agents\research\graph_nodes.py agents\research\state.py
```

單元測試：

```powershell
$env:PYTHONPATH='D:\try\backend;D:\try\backend\.venv\Lib\site-packages'
$env:AZURE_OPENAI_API_KEY='test'
$env:AZURE_OPENAI_ENDPOINT='https://example.openai.azure.com/'
$env:AZURE_OPENAI_API_VERSION='2024-02-15-preview'
$env:AZURE_OPENAI_CHAT_DEPLOYMENT='test'
D:\try\backend\.venv\Scripts\python.exe -m pytest backend\tests\test_agent_router.py backend\tests\test_prompt_versions.py backend\tests\test_traces_api.py backend\tests\test_research_graph.py -q
```

路由準確率評估：

```powershell
$env:PYTHONPATH='D:\try\backend;D:\try\backend\.venv\Lib\site-packages'
D:\try\backend\.venv\Scripts\python.exe backend\eval\runner.py
```

或透過 API：`POST /api/eval/run`

---

## 工具腳本

| 腳本 | 用途 |
| ---- | ---- |
| `scripts/sync_prompts.py` | 將所有 prompt 上傳到 Langfuse（production label） |
| `scripts/pull_prompts.py` | 從 Langfuse 拉取最新 prompt 到本地 |
| `scripts/extract_abstracts.py` | 從上傳 PDF 提取摘要文字 |
| `scripts/import_abstracts.py` | 匯入摘要文字到 Document.abstract_text |
| `scripts/batch_llamaparse.py` | 用 LlamaParse 重新解析 PDF |
| `scripts/scan_quality.py` | 掃描 extraction quality score |
| `scripts/debug_chunks.py` | 除錯 RAG chunk 檢索 |
| `scripts/compare_rrf.py` | 比較 equal-weight vs weighted RRF 檢索結果 |
