# Multi-Agent Chat 系統遷移說明書

## 遷移目標

將 `D:\try\backend` 的 multi-agent chat 核心（路由、串流、多輪對話、取消機制、tracing）整合進 `D:\try\_reference\ncu_ai_guidance` 的 Projects 頁 AI 對話區，使其具備與 PDF Chat 相同的基礎能力。

**不在本次遷移範圍內：**
- 文件處理 pipeline（upload / parse / extract / reindex / job queue）
- Qdrant chunk 索引與 PDF 段落搜尋工具
- Alembic migration history（僅攜帶必要的新 migration）
- Langfuse / LangSmith observability（可選，本文標示 `[選用]`）
- Research Graph（LangGraph multi-hop 研究流程，目前課程場景不需要）
- 評估實驗框架（EvaluationRun / Dataset / ExperimentRun）

---

## 一、現況分析

### 1.1 來源系統（D:\try\backend）

| 層面 | 技術 |
|------|------|
| Framework | FastAPI + Pydantic v2 |
| LLM | Azure OpenAI（gpt-4o + gpt-4o-mini） |
| ORM | SQLAlchemy 2.x + Alembic |
| 資料庫 | PostgreSQL |
| Vector DB | Qdrant（文件 chunk 搜尋） |
| 串流 | Server-Sent Events（SSE） |
| Auth | JWT（自管 users 表） |
| 並發控制 | asyncio.Semaphore(2)（llm_gate） |
| 觀測 | Langfuse + 自製 LocalTracer，雙寫 Trace/TraceV2 |
| 提示管理 | 版本化 PromptStack（prompting/loader.py + prompting/registry.py） |

**核心 agent 流程：**

```
使用者訊息
  ↓
route_request()          ← Orchestrator LLM 決定派哪個 agent
  ↓
route_agent_stream()     ← while loop（最多 MAX_HANDOFFS=3 跳）
  ├── chat_agent         ← 無工具直接回答；遇 [INSUFFICIENT_CONTEXT] → 升級
  ├── retrieval_agent    ← RAG 搜尋文件 chunks
  ├── question_agent     ← 生成導讀題目
  ├── research_agent     ← LangGraph 深度研究（本次不遷移）
  └── evaluation_agent   ← 品質評分（背景任務）
  ↓
compose_after（選用）     ← chat_agent 彙整其他 agent 的輸出
  ↓
_finalize_plan()         ← 寫 Trace、觸發背景評估
```

### 1.2 目標系統（ncu_ai_guidance）

| 層面 | 技術 |
|------|------|
| Framework | FastAPI |
| LLM | Azure OpenAI（GPT-4o） |
| ORM | SQLAlchemy（直接建表，尚無 Alembic） |
| 資料庫 | PostgreSQL（Render 託管） |
| Vector DB | Qdrant（課程語意搜尋） |
| 串流 | SSE（已有基礎實作） |
| Auth | Firebase Authentication（Google OAuth） |
| 部署 | 前端 → Firebase Hosting；後端 → Render |
| 現有 AI | 單層 ReAct loop（llm_service.py + tools.py） |

**現有 chat 工具（tools.py）：**
- `tool_search_courses` — 向量搜尋 + 技術篩選
- `tool_get_dept_courses` — 系所課程列表
- `tool_search_programs` — 學程搜尋
- `tool_get_program_info` — 學程詳情
- `tool_get_graduation_requirements` — 畢業門檻
- `tool_get_course_detail` — 課程大綱 + 先修
- `tool_find_similar_courses` — 相似課程推薦
- `tool_get_teacher_info` — 教師資訊
- `tool_ppr_explore` — Knowledge Graph 探索
- `tool_get_course_knowledge_map` — 課程概念圖

**現有資料表（chat 相關）：**
```
chat_sessions : session_id, user_id, title, created_at, updated_at
chat_turns    : session_id, user_msg, assistant_msg, course_cards,
                course_pool, tools_used, debug_trace, created_at
```

---

## 二、需要遷移的資源清單

### 2.1 資料表（需新增）

目標系統目前沒有以下表格，必須新增以支援 multi-agent 運作：

| 表格 | 用途 | 優先度 |
|------|------|--------|
| `conversations` | 取代 `chat_sessions`，記錄 thread_id、last_agent_name、steering lock | 必要 |
| `agent_messages` | 每輪 Q&A 輕量記錄（供 evaluation_agent 評估） | 必要 |
| `traces_v2` | Multi-agent run 根 trace（輕量 observability） | 建議 |
| `observations` | 各 agent span 詳情（LLM tokens、latency） | 建議 |
| `scores` | evaluation_agent 輸出的品質分數 | 建議 |
| `trace_events_outbox` | 非同步 trace 寫入佇列 | 若用 tracing 則必要 |

**是否廢棄現有表格？**  
`chat_sessions` 和 `chat_turns` 建議保留並平行運行，舊版 chat endpoint 繼續用舊表，新版 multi-agent endpoint 用新表。等驗證穩定後再遷移歷史資料或棄用舊表。

**DDL（Alembic migration 範例）：**

```python
# alembic/versions/001_add_conversations.py
def upgrade():
    op.create_table(
        'conversations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('thread_id', sa.String(), unique=True, nullable=False, index=True),
        sa.Column('user_id', sa.String(), nullable=True, index=True),
        sa.Column('model', sa.String(), default='openai'),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('message_count', sa.Integer(), default=0),
        sa.Column('context_summary', sa.Text(), nullable=True),
        sa.Column('last_agent_name', sa.String(), nullable=True),
        sa.Column('stream_started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), default=lambda: datetime.utcnow()),
    )

    op.create_table(
        'agent_messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('message_id', sa.String(36), unique=True, nullable=False, index=True),
        sa.Column('thread_id', sa.String(), nullable=False, index=True),
        sa.Column('user_id', sa.String(), nullable=True, index=True),
        sa.Column('agent_name', sa.String(), nullable=False),
        sa.Column('user_question', sa.Text(), nullable=True),
        sa.Column('agent_answer', sa.Text(), nullable=True),
        sa.Column('sources', postgresql.JSONB(), nullable=True),
        sa.Column('trace_summary', postgresql.JSONB(), nullable=True),
        sa.Column('observation_id', sa.String(), nullable=True, index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)),
    )
```

```python
# alembic/versions/002_add_tracing.py  （若需要 observability）
def upgrade():
    op.create_table(
        'traces_v2',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('trace_id', sa.String(), unique=True, nullable=False, index=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('thread_id', sa.String(), nullable=True, index=True),
        sa.Column('user_id', sa.String(), nullable=True, index=True),
        sa.Column('environment', sa.String(40), nullable=False, default='default'),
        sa.Column('input', postgresql.JSONB(), nullable=True),
        sa.Column('output', postgresql.JSONB(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(), nullable=True),
        sa.Column('start_time', sa.DateTime(), nullable=False, index=True),
        sa.Column('end_time', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )

    op.create_table(
        'observations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('observation_id', sa.String(), unique=True, nullable=False, index=True),
        sa.Column('trace_id', sa.String(), nullable=False, index=True),
        sa.Column('parent_observation_id', sa.String(), nullable=True, index=True),
        sa.Column('type', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('level', sa.String(20), nullable=False, default='DEFAULT'),
        sa.Column('input', postgresql.JSONB(), nullable=True),
        sa.Column('output', postgresql.JSONB(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(), nullable=True),
        sa.Column('model', sa.String(), nullable=True),
        sa.Column('prompt_tokens', sa.Integer(), nullable=True),
        sa.Column('completion_tokens', sa.Integer(), nullable=True),
        sa.Column('input_cost', sa.Float(), nullable=True),
        sa.Column('output_cost', sa.Float(), nullable=True),
        sa.Column('status_message', sa.Text(), nullable=True),
        sa.Column('start_time', sa.DateTime(), nullable=False),
        sa.Column('end_time', sa.DateTime(), nullable=True),
    )

    op.create_table(
        'scores',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('score_id', sa.String(), unique=True, nullable=False, index=True),
        sa.Column('trace_id', sa.String(), nullable=True, index=True),
        sa.Column('observation_id', sa.String(), nullable=True, index=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False, index=True),
    )

    op.create_table(
        'trace_events_outbox',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_id', sa.String(), unique=True, nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('body', postgresql.JSONB(), nullable=False),
        sa.Column('status', sa.String(20), default='pending'),
        sa.Column('attempts', sa.Integer(), default=0),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('locked_at', sa.DateTime(), nullable=True),
        sa.Column('locked_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
    )
```

**是否需要為目標系統引入 Alembic？**  
是。目標系統目前用直接建表，但新增 multi-agent 所需的表格之後，未來維護需要版本化的 migration。建議在本次遷移時同步設置 Alembic。

---

### 2.2 後端檔案（遷移清單）

#### A. 直接複製（不需改動）

| 來源路徑 | 目標路徑 | 說明 |
|----------|----------|------|
| `agents/types.py` | `app/agents/types.py` | AgentRoute、AgentResult、AgentStatus dataclass |
| `agents/steering.py` | `app/agents/steering.py` | Mid-run steering 訊息存儲 |
| `agents/chat_jobs.py` | `app/agents/chat_jobs.py` | 進行中 chat 任務追蹤 + cancel signal |
| `agents/request_context.py` | `app/agents/request_context.py` | user_id contextvars |
| `services/llm_gate.py` | `app/services/llm_gate.py` | asyncio.Semaphore 並發控制 |
| `utils.py` | `app/utils.py` | `new_id()` UUID 生成工具 |
| `prompting/loader.py` | `app/prompting/loader.py` | PromptStack 載入器 |
| `prompting/registry.py` | `app/prompting/registry.py` | 版本化 prompt 登錄 |

#### B. 複製後需修改

| 來源路徑 | 目標路徑 | 需修改的地方 |
|----------|----------|--------------|
| `agents/router_agent.py` | `app/agents/router_agent.py` | 見 §2.2.1 |
| `agents/chat_agent.py` | `app/agents/chat_agent.py` | 見 §2.2.2 |
| `agents/question_agent.py` | `app/agents/question_agent.py` | 見 §2.2.3 |
| `agents/evaluation_agent.py` | `app/agents/evaluation_agent.py` | 見 §2.2.4 |
| `agents/no_tool_runner.py` | `app/agents/no_tool_runner.py` | 見 §2.2.5 |
| `agents/runner.py` | `app/agents/runner.py` | 見 §2.2.6 |
| `services/memory_service.py` | `app/services/memory_service.py` | 見 §2.2.7 |
| `services/trace_capture.py` | `app/services/trace_capture.py` | 見 §2.2.8 |
| `services/trace_ingestion.py` | `app/services/trace_ingestion.py` | 見 §2.2.8 |
| `services/trace_repositories.py` | `app/services/trace_repositories.py` | 見 §2.2.8 |
| `services/quota_service.py` | `app/services/quota_service.py` | 見 §2.2.9 |
| `observability/__init__.py` | `app/observability/__init__.py` | 見 §2.2.10 |
| `api/chat.py` | `app/routes/chat.py` | 見 §2.2.11 |
| `db/models.py`（部分） | `app/models/db_models.py` | 見 §2.2.12 |
| `config.py` | `app/config.py` | 見 §2.2.13 |
| `main.py`（lifespan 部分） | `app/main.py` | 見 §2.2.14 |
| `logging_config.py` | `app/logging_config.py` | 直接複製 |

#### C. 重新撰寫（不適合直接移植）

| 需建立的檔案 | 說明 |
|-------------|------|
| `app/agents/retrieval_agent.py` | 來源版本搜尋 PDF chunks，目標需搜尋課程；以現有 `tools.py` 重寫 |
| `app/prompts/route_coordinator.txt` | 路由 prompt 改為課程導向 |
| `app/prompts/chat_mode.txt` | 系統提示改為課程助手語境 |
| `app/prompts/retrieval_capability.txt` | 改為課程搜尋策略 |

---

#### §2.2.1 `agents/router_agent.py` 修改點

1. **移除 `research_agent` 分支**（streaming 和 non-streaming 各一處）：
   - `route_agent_stream` 中的 `if current_route.agent_name == "research_agent":` 整塊刪除
   - `route_agent_message` 中同樣刪除
   - `_route_for_agent` 函式中移除 `research_agent` 對應的 `AgentRoute` 建構

2. **新增 `course_search_agent`**（替代 `retrieval_agent`）：
   - 在 `_route_for_agent` 加入 `if agent_name == "course_search_agent":` 分支
   - 在 `route_agent_stream` / `route_agent_message` 的執行區加入對應的 agent 呼叫
   - 保留 compose_after 邏輯（course_search_agent 也需要彙整）

3. **`_write_router_trace` 的 DB session**：
   - 來源使用 `db.db_session`（自有模組），目標需改為目標系統的 session factory
   - 目標系統使用 `get_db()` 依賴，需改成 `with SessionLocal() as db:`

4. **移除文件相關依賴**：
   - 移除所有 `document_ids` 傳給 research graph 的邏輯
   - `_get_document_abstracts()` 在 `no_tool_runner.py` 用於注入文件摘要；課程場景改為注入學生背景資訊

5. **`route_request()` 修改**：
   - 來源的 `route_request` 讀取 document_ids 作為 routing 依據
   - 目標改為讀取 `college`、`dept`、`include_grad` 等課程搜尋參數

#### §2.2.2 `agents/chat_agent.py` 修改點

1. **移除文件摘要注入**：`answer()` / `stream()` 收到 `document_ids` 後去查 DB 讀摘要；課程場景改為注入學生系別、學年資訊

2. **`[INSUFFICIENT_CONTEXT]` 標記**：保留，chat_agent 依然需要在資訊不足時升級到 `course_search_agent`

3. **`compose_final_response()`**：邏輯不變，但 system prompt 需改為課程場景（見 `prompts/chat_mode.txt`）

4. **DB session 依賴**：同 router，改為目標系統的 session factory

#### §2.2.3 `agents/question_agent.py` 修改點

1. **工具呼叫改為課程工具**：來源版本呼叫 `retrieval_agent.answer()` 取得 evidence；目標改為呼叫 `course_search_agent.answer()` 或直接從對話 context 取 evidence

2. **`evidence_context` 注入格式**：來源注入的是文件段落，目標改為注入課程描述

3. **`question_skill` prompt stack name**：維持，但 prompt 內容改為課程場景（見 §2.3）

#### §2.2.4 `agents/evaluation_agent.py` 修改點

1. **基本上可直接複製**，評估邏輯是通用的

2. **`_trace_payload()` 和 `evaluate_trace_by_observation_id()`**：這兩個函式讀舊版 `Trace` 表；目標系統只有 `traces_v2`，需移除或只保留 `TraceV2` 查詢路徑

3. **`evaluate_latest_thread_message()`**：讀 `AgentMessage` 表，需確認表格有建立

#### §2.2.5 `agents/no_tool_runner.py` 修改點

1. **`_get_document_abstracts()`**：這個函式讀文件 DB；課程場景改為 `_get_student_context(college, dept, year)` 回傳學生背景 SystemMessage

2. **`_prepare_no_tool_call()`**：改為注入學生背景而非文件摘要

3. **`astream_traced_generation` import**：確認 `observability/__init__.py` 有正確 export

#### §2.2.6 `agents/runner.py` 修改點

1. **`AsyncPostgresSaver`（LangGraph checkpointer）**：來源用 psycopg3，目標的 DB driver 需確認（Render PostgreSQL 通常支援）；若不想維護，可暫時用 `MemorySaver`（重啟後 HITL 狀態會遺失）

2. **`_build_messages()`**：保留核心邏輯，但 `document_ids`（注入文件摘要）改為 `student_context`（注入學生背景）

3. **TOOLS 匯入**：來源匯入 `tools/rag_tool.py`；目標需改為匯入 `app/services/tools.py`（已有課程工具）

4. **`setup_checkpointer()` / `shutdown_checkpointer()`**：搬入 `main.py` lifespan

#### §2.2.7 `services/memory_service.py` 修改點

1. 核心功能（儲存對話摘要、research cache）可保留

2. `_save_summary()` 用 `Conversation.thread_id` 查詢（已在來源修正過）——直接複製修正後版本

3. `load_document_research_cache()` / `save_document_research_cache()`：課程場景不需要（沒有研究 graph），可整個移除

4. pgvector 部分（`ensure_memory_collection()`）：若目標 PostgreSQL 有安裝 pgvector 擴充則可用，否則簡化為不使用向量記憶

#### §2.2.8 Tracing 相關 (`trace_capture`, `trace_ingestion`, `trace_repositories`)

這三個檔案是 multi-agent observability 的骨幹，**整體可複製**，但需要注意：

1. `trace_capture.py` 的 `LocalTracer`：來源用 LangChain `BaseCallbackHandler` 攔截所有 LangGraph 節點呼叫；課程場景若不用 LangGraph，這部分可以 stubbed（傳 `tracer=None` 給 agent 呼叫）

2. `trace_ingestion.py` 的 `_worker_session()`：使用來源系統的 `SessionLocal`；需改為目標系統的 session factory

3. `trace_repositories.py` 的 `TraceRepository`：讀寫 `TraceV2` 表，只需確認 ORM model 名稱一致

4. **最簡方案**：若暫時不需要完整 observability，可只保留 `AgentMessage` 寫入（記錄每輪問答），跳過 `TraceV2`/`Observation` 的雙寫

#### §2.2.9 `services/quota_service.py` 修改點

1. 來源讀 `Trace` 表統計當日 token 用量；目標可改為讀 `agent_messages` 表的記錄數，或直接略去（Render 付費方案通常不需要 token 配額）

2. 若不需要 quota 檢查，在 `api/chat.py` 移除 `check_quota()` 呼叫即可

#### §2.2.10 `observability/__init__.py` 修改點

1. 直接複製，但確認 `LANGFUSE_*` 環境變數在 Render 有設置

2. 若不用 Langfuse，設定 `LANGFUSE_ENABLED=false`，相關函式會自動 stub（來源已有 graceful fallback）

#### §2.2.11 `api/chat.py` 整合進 `app/routes/chat.py`

這是最複雜的整合點，需要把來源的 multi-agent 流程接入目標已有的 chat endpoint：

**目標現有 endpoint：**
```
POST /api/chat         → 非串流，回傳完整 answer
POST /api/chat/stream  → 串流 SSE
GET  /api/chat/history → 對話歷史
```

**需要新增的 endpoint：**
```
POST /api/chat/active                     → 列出進行中的串流任務
POST /api/chat/{thread_id}/cancel         → 取消串流
DELETE /api/conversations/{thread_id}     → 刪除對話
PATCH /api/conversations/{thread_id}/title → 更新標題
```

**Auth 整合注意事項（見 §2.4）：**
- 來源用 `Depends(get_current_user)` 取得 JWT 使用者
- 目標用 `Depends(get_optional_user)` 取得 Firebase 使用者
- 需統一，建議在目標新增一個 `get_user_id()` helper，接受 Firebase UID 或 JWT sub 都回傳 `str`

**`event_stream()` 整合模式：**
```python
# app/routes/chat.py（改後）

@router.post("/api/chat/stream")
async def chat_stream(req: ChatRequest,
                      user: AuthUser | None = Depends(get_optional_user)):
    # 取得或建立 Conversation
    thread_id = req.session_id or new_id()
    user_id = user.user_id if user else None

    # 取得或建立 conversations 記錄
    conv = _get_or_create_conversation(thread_id, user_id, req.model)

    # Mid-run steering 偵測（複製自來源）
    if conv.stream_started_at and _is_within_ttl(conv.stream_started_at):
        steering.set(thread_id, req.question)
        async def _steering_resp():
            yield f"data: {json.dumps({'token': '您的補充已收到，將在下一步驟納入考量。', 'done': True})}\n\n"
        return StreamingResponse(_steering_resp(), media_type="text/event-stream")

    # 設定 stream lock
    _set_stream_lock(conv)
    chat_jobs.start(thread_id, req.question, user_id=user_id, title=conv.title)

    # 取得前一個 agent（routing hint）
    prev_agent = _get_previous_agent(thread_id)

    # student_context：來源是 document_ids，這裡改為課程背景
    student_context = StudentContext(
        college=req.college,
        dept=req.dept,
        include_grad=req.include_grad,
    )

    async def event_stream():
        from services.llm_gate import get_gate
        _gate = get_gate()
        await _gate.acquire()
        full_response = ""
        sources = []
        try:
            async for token, is_done, src in route_agent_stream(
                req.question,
                thread_id=thread_id,
                student_context=student_context,
                previous_agent_name=prev_agent,
            ):
                if is_done:
                    sources = src
                else:
                    full_response += token
                    yield f"data: {json.dumps({'token': token})}\n\n"

            # 存入 session store（保持向下相容）
            session_store.save(thread_id, req.question, full_response,
                               sources=sources, user_id=user_id)
            yield f"data: {json.dumps({'done': True, 'sources': sources, 'session_id': thread_id})}\n\n"
        finally:
            _gate.release()
            chat_jobs.finish(thread_id)
            _clear_stream_lock(conv)
            asyncio.create_task(push_jobs())

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

#### §2.2.12 DB Models 整合

來源的 `db/models.py` 有很多文件相關的 model（Document、JobHistory、DocumentExtraction 等）**不需要**遷移。

需要遷移的 ORM class：

```python
# app/models/agent_models.py（新增）

class Conversation(Base):
    __tablename__ = "conversations"
    # ... 欄位同 §2.1 DDL

class AgentMessage(Base):
    __tablename__ = "agent_messages"
    # ... 欄位同 §2.1 DDL

class TraceV2(Base):    # [選用]
    __tablename__ = "traces_v2"
    # ...

class Observation(Base):  # [選用]
    __tablename__ = "observations"
    # ...

class Score(Base):       # [選用]
    __tablename__ = "scores"
    # ...

class TraceEventOutbox(Base):  # [若用 trace_ingestion]
    __tablename__ = "trace_events_outbox"
    # ...
```

#### §2.2.13 `config.py` 修改點

目標已有 `AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_ENDPOINT` 等。需確認或新增：

```python
# 目標目前有的（確認）
AZURE_OPENAI_API_KEY
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_VERSION   # 確認版本 >= 2024-12-01-preview
QDRANT_URL
DATABASE_URL

# 需要新增的
AZURE_MINI_DEPLOYMENT      # gpt-4o-mini，用於 composition / evaluation
JWT_SECRET_KEY             # 若要支援 JWT auth（除了 Firebase）
JWT_ALGORITHM              # HS256
LANGFUSE_ENABLED           # true/false
LANGFUSE_PUBLIC_KEY        # [選用]
LANGFUSE_SECRET_KEY        # [選用]
REDIS_URL                  # [選用] 快取用
```

#### §2.2.14 `main.py` lifespan 修改

```python
# app/main.py（新增至 lifespan）

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 現有初始化...
    await init_db()

    # 新增
    from agents.runner import setup_checkpointer, shutdown_checkpointer
    from services.trace_ingestion import init_trace_ingestion_worker, stop_trace_ingestion_worker
    from services.memory_service import ensure_memory_collection  # [選用]
    from services.redis_service import init_redis, close_redis    # [選用]

    await setup_checkpointer()      # LangGraph checkpointer
    ensure_memory_collection()      # [選用] pgvector
    await init_redis()              # [選用]
    init_trace_ingestion_worker()   # 背景寫 Trace 的 worker

    yield

    await stop_trace_ingestion_worker()
    await shutdown_checkpointer()
    await close_redis()  # [選用]
```

---

### 2.3 Prompt 檔案

目標系統目前沒有版本化 prompt 系統，需要：

1. **建立 `app/prompts/` 目錄**
2. **建立 `app/prompting/` 目錄**（複製來源的 `loader.py` + `registry.py`）

**需要建立的 prompt 檔：**

| 檔案 | 來源對應 | 需要修改的部分 |
|------|---------|----------------|
| `prompts/route_coordinator.txt` | `prompts/route_coordinator.txt` | 移除 `research_agent`；新增 `course_search_agent`；移除文件/論文相關語境；改為課程引導語境 |
| `prompts/chat_mode.txt` | `prompts/chat_mode.txt` | 移除 `[INSUFFICIENT_CONTEXT]` 以外的文件摘要描述；改為「中央大學課程助手」語境 |
| `prompts/core.txt` | `prompts/core.txt` | 術語規則（「課程名稱不得意譯」）可沿用，但領域改為課程 |
| `prompts/question_skill.txt` | `prompts/question_skill.txt` | 改為課程導讀題目生成；保留「出題需附頁碼或課程來源」的規則 |
| `prompts/evaluation_agent.txt` | `prompts/evaluation_agent.txt` | 幾乎直接複製，但 evidence 來源改為課程資料庫而非文件 |

**新增（不在來源）：**

| 檔案 | 用途 |
|------|------|
| `prompts/course_search.txt` | 課程搜尋 agent 的 system prompt（取代 `retrieval_capability.txt`） |

**`prompts/route_coordinator.txt` 修改重點：**

```
移除：
  - research_agent 相關段落
  - 「文件」「論文」「PDF」等字眼
  - document_ids 相關說明

新增：
  - course_search_agent：「當使用者詢問特定課程、系所課程、修課建議時使用」
  - 學生背景（college, dept, year）作為 routing 依據

原有保留：
  - agent_status 解讀規則（completed=False 表示資訊不足需升級）
  - visited_agents 防迴圈說明
  - [INSUFFICIENT_CONTEXT] 標記的解讀
```

---

### 2.4 Auth 整合

**核心差異：**

| | 來源 | 目標 |
|---|------|------|
| 機制 | JWT（自管 users 表） | Firebase Auth（Google OAuth） |
| user_id | PostgreSQL users.id | Firebase UID（字串） |
| token 格式 | `Bearer <JWT>` | `Bearer <Firebase ID Token>` |
| 驗證 | `jose.decode()` | Firebase Admin SDK `verify_id_token()` |

**整合方案：**

目標系統已有 `auth_service.py` 做 Firebase token 驗證，回傳 `AuthUser(email, name, picture, user_id)`。Multi-agent 程式碼用到 `user_id` 的地方統一改為 `AuthUser.user_id`（Firebase UID 字串）。

來源中 `get_current_user()` 回傳 SQLAlchemy `User` 物件（有 `id`、`role` 等欄位）；目標中改為直接用 Firebase UID 字串，不需要查自有 `users` 表。

**需修改的依賴呼叫：**

```python
# 來源
from api.dependencies import get_current_user
current_user: User = Depends(get_current_user)
user_id = str(current_user.id)  # 整數 ID

# 目標（改為）
from app.services.auth_service import get_optional_user
current_user: AuthUser | None = Depends(get_optional_user)
user_id = current_user.user_id if current_user else None  # Firebase UID 字串
```

**`agents/request_context.py` 不需修改**（存取 contextvars，與 auth 無關）。

---

### 2.5 Tools 整合

來源系統的 `tools/rag_tool.py` 是文件搜尋工具，**全部不遷移**。

目標系統已有完整的課程工具（`app/services/tools.py`），這些工具需要被包裝成 multi-agent 架構可以呼叫的形式。

**新建 `app/agents/retrieval_agent.py`（課程版）：**

```python
# app/agents/retrieval_agent.py
from app.services.llm_service import generate_with_tools
from app.agents.types import AgentResult, AgentStatus

AGENT_NAME = "course_search_agent"

async def answer(
    user_message: str,
    thread_id: str,
    student_context: StudentContext | None = None,
    *,
    observation_id: str | None = None,
    trace_id: str | None = None,
    on_stage=None,
    use_mini: bool = False,
) -> AgentResult:
    if on_stage:
        on_stage("搜尋課程中")

    # 直接使用目標系統已有的 ReAct tool runner
    result = await asyncio.to_thread(
        generate_with_tools,
        question=user_message,
        history=[],
        context_hint=_build_context_hint(student_context),
    )

    sources = [
        f"{c['name']}（{c.get('dept', '')}）"
        for c in result.get("course_cards", [])
    ]
    return AgentResult(
        response=result["answer"],
        sources=sources,
        task_type="course_search",
        agent_name=AGENT_NAME,
        observation_id=observation_id or new_id(),
        status=AgentStatus(
            completed=bool(sources),
            gaps=[] if sources else ["未找到相關課程"],
            agent_limitation="" if sources else "課程資料庫查詢範圍有限",
        ),
    )
```

**`stream()` 版本**：可先用 `run_in_executor` 包裝 `generate_with_tools()`，待後續替換為真正的 streaming tool runner。

---

### 2.6 Frontend 修改

目標系統前端已有 chat UI 和 SSE 解析邏輯，但 SSE 事件格式需要配合 multi-agent 更新。

**現有 SSE 格式（目標系統）：**
```json
{"type": "token", "text": "..."}
{"type": "done", "sources": [...], "session_id": "..."}
```

**新的 SSE 格式（來源系統，需對齊）：**
```json
{"thread_id": "...", "agent_name": "course_search_agent", "task_type": "course_search"}
{"stage": "搜尋課程中"}
{"token": "中央大學資訊工程學系..."}
{"done": true, "sources": ["計算機概論（資訊工程學系）"], "session_id": "..."}
```

**前端需要修改的部分：**

1. **SSE 解析邏輯**（Projects 頁的 AI 對話元件）：
   - 接收 `stage` 事件 → 顯示進度指示器
   - 接收 `agent_name` → 可選顯示「目前使用：課程搜尋」標籤
   - `done` 事件改為讀 `sources`（來源是課程名稱字串，不是頁碼）

2. **取消按鈕**：
   - 呼叫 `POST /api/chat/{thread_id}/cancel`
   - 前端需維持 `thread_id` 狀態

3. **向下相容**：
   - 在 `done` 事件中繼續回傳 `session_id` 欄位（目標前端已在用）

**SSE 解析更新範例（TypeScript）：**

```typescript
// 在 AI 對話元件的 fetch handler 中

for (const line of lines) {
  if (!line.startsWith("data: ")) continue;
  const data = JSON.parse(line.slice(6));

  if (data.stage) {
    setStageMessage(data.stage);       // 新增：顯示 agent 進度
  } else if (data.agent_name) {
    setAgentName(data.agent_name);     // 新增（選用）：顯示使用的 agent
  } else if (data.token) {
    setAnswer(prev => prev + data.token);
  } else if (data.done) {
    setSources(data.sources ?? []);
    setSessionId(data.session_id ?? data.thread_id);  // 相容兩種格式
    setIsLoading(false);
  }
}
```

---

### 2.7 Render 部署環境

**目前缺少的基礎設施：**

| 服務 | 目前狀態 | 需要動作 |
|------|---------|---------|
| PostgreSQL | ✅ 已有（Render） | 確認擴充：`pgvector`（若用記憶功能） |
| Redis | ❌ 無 | Render 上加 Redis 附加服務（或暫時跳過 Redis 功能） |
| Qdrant | ✅ 已有 | 不需改動（課程向量索引保持） |
| LangGraph checkpointer | ❌ 無 | 用 PostgreSQL 建立（Render 已有 pg） |
| Langfuse | ❌ 無 | 可選：在 cloud.langfuse.com 建立 project，設定環境變數 |

**Render 需要新增的環境變數：**
```
AZURE_MINI_DEPLOYMENT    = gpt-4o-mini
JWT_SECRET_KEY           = <隨機字串，若要支援 JWT>
LANGFUSE_ENABLED         = true
LANGFUSE_PUBLIC_KEY      = pk-lf-...
LANGFUSE_SECRET_KEY      = sk-lf-...
```

**Build command 更新（加上 Alembic migration）：**
```bash
pip install -r backend/requirements.txt && python -m alembic upgrade head
```

**Redis 若先不用**：在 `services/redis_service.py` 的 `init_redis()` 加判斷：
```python
async def init_redis():
    url = os.environ.get("REDIS_URL")
    if not url:
        return  # Skip Redis initialization gracefully
```

---

### 2.8 Python 依賴（`requirements.txt` 新增）

```
# 新增的依賴

# Multi-agent / LLM
langchain-core>=0.2.0
langchain-openai>=0.1.0
langgraph>=0.1.0           # LangGraph checkpointer
langchain>=0.2.0           # 若 runner.py 用到 LangChain agents

# Observability
langfuse>=2.0.0            # 選用

# Database / Schema
alembic>=1.13.0            # Migration 管理
psycopg[binary]>=3.2.0     # LangGraph checkpointer 需要 psycopg3（非 psycopg2）

# Caching
redis>=5.0.0               # 選用

# Auth
python-jose[cryptography]>=3.3.0  # 若需要 JWT 支援
```

**注意**：LangGraph checkpointer（`AsyncPostgresSaver`）需要 `psycopg3`，但目標系統可能用 `psycopg2`。需確認或加入兩者並存的相容層。

---

## 三、遷移執行順序

建議依以下順序進行，每個階段完成後驗證才進入下一階段：

### 階段一：基礎設施（1-2 天）

1. 在目標系統引入 Alembic（`alembic init`，配置 `alembic.ini` 指向 Render DB）
2. 執行 migration 001（conversations + agent_messages）
3. 執行 migration 002（traces_v2 + observations + scores + outbox）[選用]
4. 確認 PostgreSQL 表格建立正確

### 階段二：服務層（1-2 天）

1. 複製 `utils.py`（`new_id()`）
2. 複製 `agents/types.py`、`agents/steering.py`、`agents/chat_jobs.py`、`agents/request_context.py`
3. 複製 `services/llm_gate.py`
4. 複製並修改 `services/memory_service.py`（移除 document cache 部分）
5. 複製並修改 `services/trace_ingestion.py`（改 session factory）[選用]
6. 更新 `app/config.py` 加入新環境變數
7. 確認可 import 不報錯

### 階段三：提示系統（0.5 天）

1. 建立 `app/prompting/` 目錄，複製 `loader.py` + `registry.py`
2. 建立 `app/prompts/` 目錄
3. 修改並放入 `route_coordinator.txt`、`chat_mode.txt`、`core.txt`、`question_skill.txt`、`evaluation_agent.txt`
4. 新建 `course_search.txt`

### 階段四：Agents（2-3 天）

1. 複製並修改 `agents/no_tool_runner.py`（document_ids 改為 student_context）
2. 複製並修改 `agents/chat_agent.py`（DB session、document_ids 移除）
3. 新建 `agents/retrieval_agent.py`（課程版，包裝既有 tools.py）
4. 複製並修改 `agents/question_agent.py`
5. 複製並修改 `agents/evaluation_agent.py`
6. 複製並修改 `agents/runner.py`（tools 改為課程工具、psycopg3 注意）
7. 複製並修改 `agents/router_agent.py`（移除 research_agent、加入 course_search_agent）

### 階段五：API 整合（1-2 天）

1. 更新 `app/routes/chat.py` 整合 multi-agent streaming
2. 新增 cancel、active 等 endpoint
3. 更新 `app/main.py` lifespan（加入 checkpointer、worker 初始化）
4. 本地測試 `/api/chat/stream` 基本流程

### 階段六：前端更新（0.5-1 天）

1. 更新 SSE 解析加入 `stage` 和 `agent_name` 處理
2. 加入取消按鈕（呼叫 cancel endpoint）
3. 測試 Projects 頁 AI 對話區完整流程

### 階段七：部署驗證（0.5 天）

1. 在 Render 加入新環境變數
2. 確認 Alembic migration 在 Render 正確執行
3. 確認 SSE streaming 在 Firebase Hosting → Render 的跨域設定正確（CORS）
4. 執行手動 end-to-end 測試

---

## 四、遷移後驗證清單

### 功能驗證

- [ ] 使用者提問觸發 routing → 選擇適當 agent
- [ ] `chat_agent` 對一般知識問題直接回答（不搜尋課程）
- [ ] `course_search_agent` 對課程查詢觸發工具呼叫（`tool_search_courses` 等）
- [ ] `[INSUFFICIENT_CONTEXT]` 觸發升級到 `course_search_agent`
- [ ] `question_agent` 對「出題」需求生成題目附來源
- [ ] `evaluation_agent` 評估回答品質並寫入 `scores` 表
- [ ] SSE streaming 事件格式正確（stage → token → done）
- [ ] 取消功能正常（cancel endpoint → cancel signal → generator 停止）
- [ ] 對話歷史正確存入 `conversations` + `agent_messages`
- [ ] 重新開啟對話可讀取歷史

### 相容性驗證

- [ ] 原有 `/api/chat/stream` 舊格式仍可用（若前端尚未更新）
- [ ] Firebase Auth 使用者可正常識別
- [ ] Render 部署後 CORS 設定正確（Firebase Hosting domain 在 allow list）

### 效能驗證

- [ ] TTFB（第一個 token）< 1.5 秒
- [ ] 完整回應 < 5 秒（course_search_agent 含工具呼叫）
- [ ] 並發 2 個串流不超出 LLM gate 限制

---

## 五、已知風險與緩解

| 風險 | 可能影響 | 緩解方案 |
|------|---------|---------|
| psycopg2 vs psycopg3 衝突 | LangGraph checkpointer 需要 psycopg3，但目標可能只有 psycopg2 | 先用 `MemorySaver` 替代，重啟後 HITL 狀態遺失，但功能不受影響 |
| Firebase UID 與 conversation 對應 | 跨裝置同一使用者 UID 一致，無問題；但匿名使用者無 UID | `user_id` 設為 nullable，允許匿名對話 |
| Render 冷啟動（spin-down） | worker 重啟後 in-memory `chat_jobs`、`steering._pending` 清空 | 設為 graceful（客戶端 polling 發現連線斷開後重試），不影響資料完整性 |
| Redis 暫無 | job_service 的 SSE broadcast 改為 local queue，多 instance 部署時 chat jobs 廣播不跨 instance | Render 單 instance 部署無問題；多 instance 時再加 Redis |
| Qdrant 課程向量索引 | 現有課程索引格式若與來源 `rag_tool.py` 期望的 metadata 格式不同 | 新的 `retrieval_agent.py` 直接呼叫既有 `tools.py`，繞過 `rag_tool.py` |
