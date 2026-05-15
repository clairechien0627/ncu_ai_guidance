# 系統架構文件

## 概覽

Report Agent 是一套 RAG-first 多代理人學術研究助理，核心設計原則：

- **檢索品質決定回答品質**：所有回答都建立在真實文件 evidence 上
- **代理人分工明確**：Router 負責協調，各 Agent 只做自己的事
- **Research Graph 是核心**：多輪 coverage-driven 搜尋，不憑空生成

---

## 整體請求流程

所有 agent 的結果都回傳給 router，由 router 統一回應使用者。唯一特殊的是 question+文件流程中，retrieval 的 evidence 先回 router，router 再傳給 question。

```mermaid
flowchart TD
    U["使用者訊息"] --> API["api/chat.py"]
    API --> Router["router_agent\n意圖分類 + ExecutionPlan\n唯一的協調中心"]

    Router -->|chat| Chat["chat_agent"]
    Router -->|retrieval| Ret["retrieval_agent\n文件 Q&A"]
    Router -->|"question＋文件\nStep 1"| Ret
    Router -->|"question（無文件）"| QA["question_agent"]
    Router -->|research| Res["research_agent"]
    Router -->|evaluation| Eval["evaluation_agent"]
    Router -->|"compose_after（選用）"| Chat

    Chat -->|"回傳結果"| Router
    Ret -->|"回傳結果 / evidence"| Router
    QA -->|"回傳結果"| Router
    Res -->|"回傳結果"| Router
    Eval -->|"回傳結果"| Router

    Router -->|"Step 2（含 evidence）"| QA
    Router --> OUT["回應使用者"]
    Res --> RG["research_graph\n排程 → 執行 → 撰寫"]
    Router -.->|"背景"| Eval

    style Router fill:#e8f4f8,stroke:#2196F3
    style RG fill:#f0f8e8,stroke:#4CAF50
```

---

## 各代理人職責

### router_agent

**檔案：** `agents/router_agent.py`  
**Prompt：** `core` + `route_coordinator`（fallback 時才呼叫 LLM）

**職責：**

- 擁有整個請求的協調權，是唯一知道完整 ExecutionPlan 的角色
- 意圖分類：先走 keyword fast path，uncertain 才呼叫 `route_coordinator` LLM
- 建立 `ExecutionPlan`，決定哪些 agent 按哪個順序執行
- 寫入 root router trace（其他 agent 的 trace 以此為 parent）
- 決定是否在任務後執行 evaluate_after（限 research / retrieval / question）

**設計決策：**

- `route_coordinator` 不是 agent，是 router 內部使用的 fallback prompt
- Router 不做實際回答，只做協調
- `evaluate_after` 有 deterministic policy 門檻，防止濫用 LLM

---

### chat_agent

**檔案：** `agents/chat_agent.py`  
**Prompt：** `core` + `chat_mode`

**職責：**

- 純文字對話（無 RAG tools）
- 格式化最終回應（`compose_final_response`）—— 只有 router 呼叫，不被其他 agent 直接呼叫

**設計決策：**

- 不持有 agent-to-agent routing 邏輯
- Streaming 透過 `no_tool_runner.py` 的 `stream_no_tool_agent()` 實現
- `compose_after` 是 router 控制的選用 Step 3，chat_agent 在這裡扮演格式化角色

---

### retrieval_agent

**檔案：** `agents/retrieval_agent.py`  
**Prompt：** `core` + `retrieval_capability`  
**Tools：** `search_report`（RAG），`AgentContext` 含文件 metadata

**職責：**

- 單點文件 Q&A：搜尋特定資訊、引用、方法、結果
- 當 intent=question 且有文件時，作為 Step 1 收集 evidence（**不直接呼叫 question_agent**）

**設計決策：**

- 用 LangGraph tool agent（`runner.py`），有 checkpoint + 記憶注入
- Middleware stack 保護：token limit、model fallback、retry
- 永遠回傳到 router_agent，由 router 決定是否把 evidence 傳給 question_agent

---

### question_agent

**檔案：** `agents/question_agent.py`  
**Prompt：** `core` + `question_skill`

**職責：**

- 學生導向：出題、測驗、互動導讀
- 無 RAG tools，使用 router 傳入的 evidence 或純 LLM 知識

---

### evaluation_agent

**檔案：** `agents/evaluation_agent.py`  
**Prompt：** `core` + `evaluation_agent`

**職責：**

- 對任務 trace 進行品質評分（0-1）
- 只在 deterministic policy 允許時由 router 觸發（背景執行）
- 或由使用者明確要求評估時直接路由

---

### research_agent

**檔案：** `agents/research/agent.py`  
**Prompt stack：** `research_runtime`（5 個 prompt 組合）

**職責：**

- 執行多輪 coverage-driven 研究圖
- 載入 document research cache（跳過已填充的 slot）
- 完成後寫入長期記憶

→ 詳見「Research Graph 詳解」

---

## Research Graph 詳解

### 架構圖

```mermaid
flowchart TD
    START([▶ START]) --> TP

    subgraph PRE["前置：research_agent.py"]
        TP["task_planner<br>（LLM）<br>建立 coverage items + output contract"]
    end

    TP --> SCH

    subgraph GRAPH["research_graph（LangGraph）"]
        SCH["scheduler_node<br>（LLM）<br>對所有 candidate slots 排序<br>輸出 pending_slots"]
        EX["slot_executor_node<br>（asyncio.Semaphore 3）<br>依序執行 pending_slots<br>void 搜尋不計數"]
        SC{should_continue}
        WRI["writer_node<br>（LLM）<br>依 evidence + contract 產生報告"]
    end

    SCH --> EX
    EX --> SC
    SC -->|"未完成且未超預算"| SCH
    SC -->|"完成或超預算"| WRI
    WRI --> END([⏹ END])

    style SCH fill:#e8f4f8,stroke:#2196F3
    style EX fill:#f0f8e8,stroke:#4CAF50
    style SC fill:#fff8e1,stroke:#FF9800
    style WRI fill:#e8f4f8,stroke:#2196F3
```

### slot_executor 內部（asyncio reactive 排程）

```mermaid
flowchart TD
    PS["pending_slots<br>（已按優先序排列）"] --> INIT["初始啟動<br>min(3, n) 個 slots"]

    subgraph WORKER["每個 asyncio Task（最多 3 個同時）"]
        SEM["acquire Semaphore(3)"]
        SEM --> RS["_run_single_slot<br>plan → retrieve → reflect"]
        RS --> Q{搜尋品質}
        Q -->|"USEFUL / PARTIAL<br>有 evidence"| OK["merge 到 accumulated<br>search_count += 1<br>release semaphore"]
        Q -->|"NO_RESULTS / NOT_USEFUL<br>且 evidence = 0"| VOID{void_attempts?}
        VOID -->|"< 4 次"| BACK["放回 void 通知<br>scheduler 下輪重排<br>release semaphore"]
        VOID -->|"≥ 4 次"| DEAD["標記 NOT_FOUND / OMITTED<br>永久結束此 slot<br>release semaphore"]
    end

    INIT --> WORKER
    OK --> NEXT["semaphore 空 → 啟動下一個 pending slot"]
    NEXT --> WORKER
    WORKER --> DONE{"所有 task 完成？"}
    DONE -->|"否"| WORKER
    DONE -->|"是"| RET["回傳 merged state + void_slot_attempts"]
```

### 各節點職責與 Prompt

| 節點 | 類型 | Prompt | 核心職責 |
| ---- | ---- | ------ | ------- |
| **task_planner** | LLM | `task_planner` | 分析問題，建立 coverage items（研究維度）和 output contract |
| **scheduler_node** | LLM | `research_scheduler` | 對所有 candidate slots 按搜尋方向明確度排序，輸出完整 pending_slots |
| **slot_executor_node** | asyncio | — | 管理 Semaphore(3)，reactive 執行，void 計數，結果合併 |
| **_run_single_slot** | async fn | `research_planner` + `research_reflector` | plan query → RAG 搜尋 → 反思更新 evidence |
| **writer_node** | LLM | `research_writer` | 依 evidence 和 output contract 產生最終報告，品質門檻重試 |
| **should_continue** | deterministic | — | 路由：scheduler / writer（無 verification 節點） |

### SlotStatus 語意

| 狀態 | 含義 | Writer 行為 |
| ---- | ---- | ---------- |
| `FILLED` | 有充足 evidence | 完整輸出 |
| `PARTIAL` | 有部分 evidence，全局預算到頂時停止 | 有多少說多少 |
| `EXHAUSTED` | 達 per_slot_cap（7次）停止 | 有多少說多少 |
| `NOT_FOUND` | 4 次 void，required slot，文件可能無此資訊 | 說明「未提及」 |
| `OMITTED` | 4 次 void，optional slot，文件可能無此資訊 | 省略 |

---

## 工具代理人 Middleware Stack

`runner.py` 建立的 tool agent（retrieval_agent 使用）具有以下 middleware 層：

```mermaid
flowchart LR
    REQ["使用者請求"] --> M1
    subgraph MIDDLEWARE["Middleware Stack（由上到下）"]
        M1["@dynamic_prompt _memory_prompt<br>動態 system prompt + 記憶注入"]
        M2["@after_model _track_model_cost<br>Token 計費 log"]
        M3["@before_model _trim_messages<br>訊息數量上限（20 條）"]
        M4["SummarizationMiddleware<br>舊訊息壓縮"]
        M5["ContextEditingMiddleware<br>清除過期 tool results"]
        M6["ModelCallLimitMiddleware<br>模型呼叫上限（15 次）"]
        M7["ModelFallbackMiddleware<br>主模型失敗 → mini 模型"]
        M8["ModelRetryMiddleware<br>結構化輸出解析失敗重試"]
        M9["ToolRetryMiddleware<br>瞬態 tool 錯誤重試"]
        M10["HumanInTheLoopMiddleware<br>interrupt_on={}<br>（目前全唯讀工具，無中斷）"]
    end
    M1 --> M2 --> M3 --> M4 --> M5 --> M6 --> M7 --> M8 --> M9 --> M10 --> LLM["LLM"]
```

---

## 記憶系統

三層記憶，由 `services/agent_memory.py` 統一以 MemoryPolicy 管理：

```mermaid
flowchart LR
    subgraph MEM["記憶系統"]
        ST["短期記憶<br>context_summary<br>（per-thread PG 欄位）<br>Research 完成後更新<br>不經 LLM 壓縮"]
        LT["長期記憶<br>AsyncPostgresStore<br>（per-user, pgvector）<br>語意搜尋過去研究"]
        DC["文件 Cache<br>document_research_cache<br>（PG 表）<br>per-document-set + coverage-template<br>已填充 slot 跳過重搜"]
    end

    RES["research_agent"] -->|"寫入"| ST
    RES -->|"寫入"| LT
    RES -->|"讀取（跳過重搜）"| DC
    RES -->|"寫入（完成後更新）"| DC
```

| Intent | 讀 context_summary | 讀長期記憶 | 讀 doc cache | 寫 context_summary | 寫長期記憶 |
| ------ | :----------------: | :-------: | :----------: | :----------------: | :-------: |
| research | ✓ | ✓ | ✓ | ✓ | ✓ |
| retrieval | ✓ | — | ✓ | — | — |
| question | ✓ | — | — | — | — |
| chat | ✓ | ✓ | — | — | — |

---

## 文件摘取管線

索引一份 PDF 時，背景依序執行四個 step：

```mermaid
flowchart LR
    UP["PDF 上傳"] --> S1
    S1["Step 1<br>research_agent<br>原始研究摘要"] --> S2 & S3
    S2["Step 2<br>summary_structure<br>結構化欄位"] --> S4
    S3["Step 3<br>question_generator<br>導讀 + 問題"] --> S4
    S4["Step 4<br>summary_quality<br>品質評分"]

    style S1 fill:#e8f4f8,stroke:#2196F3
    style S2 fill:#f0f8e8,stroke:#4CAF50
    style S3 fill:#f0f8e8,stroke:#4CAF50
    style S4 fill:#fff8e1,stroke:#FF9800
```

Step 2 和 Step 3 平行執行。`summary_quality` 是摘取 step，不是獨立 agent。

---

## Prompt 架構設計

所有 prompt 使用 `# extends:` 繼承機制，runtime 熱載入：

```text
core.txt                    ← 所有 stack 的共用基底
  ├── chat_mode.txt         ← chat_default
  ├── retrieval_capability.txt ← retrieval_default
  ├── question_skill.txt    ← question_default
  ├── evaluation_agent.txt  ← evaluation_default
  ├── route_coordinator.txt ← router_default（fallback 分類）
  ├── task_planner.txt      ┐
  ├── research_scheduler.txt│
  ├── research_planner.txt  ├─ research_runtime（5個合一）
  ├── research_reflector.txt│
  ├── research_writer.txt   ┘
  ├── summary_structure.txt ← extract_step2
  ├── question_generator.txt← extract_step3
  └── summary_quality.txt   ← extract_step4
```

**Prompt 職責分工（Research）：**

| Prompt | 決策類型 | 呼叫頻率 |
| ------ | ------- | ------- |
| `task_planner` | 建立研究維度（coverage items） | 每次任務 1 次 |
| `research_scheduler` | 排序全部 candidate slots | 每輪 1 次 |
| `research_planner` | 規劃單一 slot 的搜尋查詢 | 每個 slot 1 次 |
| `research_reflector` | 評估 chunk 品質、更新 evidence | 每個 slot 1 次 |
| `research_writer` | 根據 evidence 撰寫最終報告 | 每次任務 1-2 次 |

---

## Trace 追蹤架構

所有 LLM 呼叫都透過 `observability.ainvoke_traced_generation()` 記錄到 PostgreSQL `traces` 表和 Langfuse。

```text
traces
├── run_id（唯一）
├── parent_run_id（階層關係）
├── thread_id（對話緒）
├── task_type（chat_turn / research_task / document_extraction / ...）
├── route_intent（chat / retrieval / question / research）
├── agent_name（router_agent / research_agent / ...）
├── prompt_stack_name + prompt_stack_json（完整 prompt 快照）
├── llm_call_count, tool_count
└── quality_score, quality_detail（評估結果）
```

Router 寫 root trace，其他 agent trace 以 router 的 `run_id` 為 `parent_run_id`，形成完整的請求追蹤樹。
