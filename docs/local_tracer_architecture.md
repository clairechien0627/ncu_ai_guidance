# LocalTracer / Trace v2 架構守則

> 目標：統一後端 trace 變數命名，讓 router、task agents、LocalTracer、outbox、DB schema、tests 都使用同一套語意。

---

## 核心命名

全系統只保留三層觀測 ID：

```text
thread_id                    一段對話 / session，可跨多個 request
└── trace_id                 一次使用者 request，由 router 建立
    └── observation_id       一個執行單位：Router SPAN / Agent SPAN / GENERATION / TOOL / CHAIN
        └── parent_observation_id
```

| 名稱 | 語意 | 主要來源 |
|---|---|---|
| `thread_id` | 對話線程 / session | API request、LangGraph configurable |
| `trace_id` | 單次 request 的 root trace | `router_agent.route_agent_message()` / `route_agent_stream()` |
| `observation_id` | 單一觀測節點 | `ExecutionStep.observation_id`、LangChain callback `run_id` 轉換後 |
| `parent_observation_id` | 父觀測節點 | router span、agent span、LangChain `parent_run_id` 轉換後 |

### 保留的框架名稱

LangChain / LangGraph callback API 仍然使用 `run_id`、`parent_run_id`，這是框架介面，不要改。

在我們自己的 domain model、DB、API、tests 中，除非測試正在直接模擬 LangChain callback，否則不要再使用 `router_run_id`、`task_run_id`、`parent_run_id`、`trace_run_id` 代表系統觀測 ID。

---

## Trace v2 資料模型

Trace v2 寫入兩張主要表：

```text
traces_v2
└── trace_id                 request root

observations
├── observation_id           node id
├── trace_id                 belongs to traces_v2.trace_id
└── parent_observation_id    parent node id, null 表示 trace root 下第一層
```

`traces_v2` 是 request/root 容器，只保存全局欄位與 request-level extras，例如 `agent_name`、`document_ids`、route 結果。它不保存 prompt stack、display、run_type、token、error、tool_count 或 llm_call_count。這些資料的來源如下：

- prompt identity：`Observation.prompt_name` / `Observation.prompt_version`
- status/error：由 observations 的 `status` / `status_message` 聚合
- tokens/cost/counts：由 observations 聚合
- quality/feedback：由 scores 重建
- UI preview/sources：由 root input/output 與 child observations 組成

Router 建立 `trace-create` 事件，並建立一個 Router SPAN observation。刻意讓：

```text
Router SPAN observation_id == trace_id
```

這樣所有 task agent 的 `parent_observation_id` 都可以直接指向 `trace_id`，形成穩定的 root observation。

---

## Router 執行架構

Router 是唯一負責跨 agent 協調的地方。Task agents 不互相當工具呼叫，也不自行建立新的 request trace。

```text
route_agent_message / route_agent_stream
├── route_request()
├── trace_id = request trace id
├── _build_execution_plan(route, trace_id=...)
│   ├── ExecutionPlan.trace_id
│   ├── ExecutionStep.observation_id
│   └── optional evidence / composition steps
├── _write_router_trace()
│   ├── trace-create
│   └── observation-create: Router SPAN, observation_id == trace_id
└── invoke selected task agent with:
    ├── trace_id=step.trace_id
    └── observation_id=step.observation_id
```

`ExecutionPlan` / `ExecutionStep` 的正式欄位：

```python
@dataclass(frozen=True)
class ExecutionStep:
    agent_name: str
    observation_id: str
    trace_id: str
    kind: str = "primary"
    reason: str = ""

@dataclass(frozen=True)
class ExecutionPlan:
    trace_id: str
    route: AgentRoute
    steps: tuple[ExecutionStep, ...]
```

### Question route 的 evidence step

文件型 question 會先跑 retrieval evidence step，再跑 question step：

```text
trace_id
├── Router SPAN                     observation_id = trace_id
├── Retrieval Agent SPAN            parent_observation_id = trace_id
│   └── retrieval LangGraph runs
└── Question Agent SPAN             parent_observation_id = trace_id
    └── no-tool generation
```

### Composition step

`compose_after=True` 時，router 會在 primary task 後建立 chat composition step：

```text
trace_id
├── Router SPAN
├── Research / Retrieval Agent SPAN
└── Chat Composition SPAN
```

Composition 不是 task agent 自行 handoff；它仍由 router plan 建立，並使用自己的 `ExecutionStep.observation_id`。

---

## Agent 寫入路徑

不同 agent 的內部執行方式不同，但對外合約一致：接收 `trace_id` 和本次 agent 的 `observation_id`。

| Agent 類型 | 執行器 | Agent SPAN | 子節點 |
| --- | --- | --- | --- |
| `chat_agent` | `no_tool_runner` | `write_agent_span()` | `GENERATION` observation |
| `question_agent` | `no_tool_runner` | `write_agent_span()` | `GENERATION` observation |
| `retrieval_agent` | `runner.run_tool_agent()` | `write_agent_span()` | `LocalTracer` 收集 LangGraph CHAIN / GENERATION / TOOL |
| `research_agent` | research LangGraph workflow | `write_agent_span()` | `LocalTracer` 或 workflow 節點觀測 |
| `evaluation_agent` | evaluation service | 由 caller 提供 `observation_id` | scores / evaluator observations |

每個 task agent 的第一層 SPAN 應該符合：

```python
write_agent_span(
    observation_id=observation_id,
    trace_id=trace_id,
    thread_id=thread_id,
    parent_observation_id=trace_id,
    name="Retrieval Agent",
    start_time=agent_start,
    input_data={...},
)
```

完成或錯誤時用相同 `observation_id` upsert：

```python
write_agent_span(
    observation_id=observation_id,
    trace_id=trace_id,
    thread_id=thread_id,
    parent_observation_id=trace_id,
    name="Retrieval Agent",
    start_time=agent_start,
    end_time=agent_end,
    output_data={...},
    error=None,
)
```

---

## LocalTracer 職責

`LocalTracer` 是 LangChain `BaseCallbackHandler`，只負責把 LangChain / LangGraph callback events 轉成 Trace v2 outbox events。

目前主要用於 tool / graph 型 agents：

- `retrieval_agent` 透過 `agents/runner.py::_build_tracer()`
- `research_agent` 透過 research workflow 內部建立 tracer

No-tool agents 不依賴 LocalTracer；它們透過 `write_agent_span()` 和手動 `GENERATION` observation 寫入。

---

## LocalTracer 內部 ID 規則

`LocalTracer._events` 裡的 key 和 `"run_id"` 欄位仍是 LangChain callback 的 run UUID。這是暫存格式，不是系統公開合約。

```python
{
    "run_id": str,                         # LangChain run UUID
    "parent_observation_id": str | None,   # 暫存 LangChain parent_run_id
    "type": "CHAIN" | "GENERATION" | "TOOL",  # 暫存 callback type，寫入 Observation.type
    "name": str,
    "inputs": str | None,
    "outputs": str | None,
    "error": str | None,
    "start_time": datetime,
    "end_time": datetime | None,
    "prompt_tokens": int | None,
    "completion_tokens": int | None,
    "metadata": dict | None,
}
```

轉換只發生在 `_build_trace_events()` 內：

```python
"observation_id": row["run_id"]
```

因此：

- callback method signature 保留 `run_id` / `parent_run_id`
- `_events` 暫存保留 `"run_id"`
- outbox body 和 DB 欄位使用 `observation_id` / `parent_observation_id`
- LocalTracer 只把 `prompt_name` / `prompt_version` 寫到 observation body，不再接收或保存 prompt stack/hash/root prompt 快照

---

## LocalTracer 模式

### Sub-agent 模式

當 `LocalTracer(trace_id=..., parent_observation_id=...)` 有收到 `trace_id` 時，表示它掛在 router 建立的 request trace 底下。

```text
trace_id
└── Agent SPAN                         observation_id = parent_observation_id
    └── LangChain root run             parent_observation_id = Agent SPAN
        ├── LLM call                   GENERATION
        └── Tool call                  TOOL
```

所有 LangChain runs 都會寫成 `observation-create`，且 body 使用同一個 `trace_id`。

Root LangChain run：

```text
parent_observation_id = self.parent_observation_id
```

Child LangChain run：

```text
parent_observation_id = LangChain parent_run_id
```

### Root 模式

當 `trace_id is None` 時，LocalTracer 會自行建立 root trace。這只適合獨立測試或非 router 管理的臨時執行。

```text
trace-create: trace_id = root LangChain run_id
└── observations for non-root child runs
```

Router 管理的正式 request 不應依賴 root 模式。

---

## TraceEventOutbox 合約

所有 Trace v2 寫入都先進 `trace_event_outbox`，再由 `TraceIngestionWorker` 寫 DB。

```python
{
    "event_id": "observation-create:<observation_id>",  # 建議穩定；缺省會自動 UUID
    "event_type": "observation-create",
    "body": {
        "observation_id": "...",
        "trace_id": "...",
        "parent_observation_id": "...",
        "type": "SPAN|GENERATION|TOOL|CHAIN|EVALUATOR",
        "name": "...",
        "input": {...},
        "output": {...},
        "metadata": {...},
        "status": "DEFAULT|WARNING|ERROR|DEBUG",
        "status_message": None,
        "start_time": "...",
        "end_time": "...",
    },
}
```

`TraceRepository.upsert_trace()` 處理 `trace-create`。
`ObservationRepository.upsert_observation()` 處理 `observation-create` / `observation-update`。

---

## Observation 欄位

| 欄位 | 說明 |
|---|---|
| `observation_id` | 單一 observation 唯一 ID |
| `trace_id` | 所屬 request trace |
| `thread_id` | 對話 ID |
| `parent_observation_id` | 父 observation ID |
| `type` | `SPAN` / `GENERATION` / `TOOL` / `CHAIN` / `EVALUATOR` |
| `name` | 節點名稱 |
| `model` | LLM deployment / model name |
| `model_parameters` | temperature 等模型參數 |
| `usage` | token usage JSON |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | token 欄位快取 |
| `completion_start_time` | streaming 第一個 token 時間 |
| `input_cost` / `output_cost` / `total_cost` | 成本欄位 |
| `status` | `DEFAULT` / `WARNING` / `ERROR` / `DEBUG` |
| `status_message` | 錯誤訊息 |

---

## AgentContext 合約

LangGraph tools 層透過 `AgentContext` 取得目前 agent observation：

```python
@dataclass
class AgentContext:
    observation_id: str | None = None
    thread_id: str | None = None
    document_ids: list[int] | None = None
```

`runner.run_tool_agent()` 建立 context 時必須傳入：

```python
AgentContext(
    thread_id=thread_id,
    observation_id=observation_id,
    document_ids=document_ids,
)
```

---

## Tests 遷移規則

後端變數名統一後，tests 應以新合約斷言。只有直接測 LangChain callback 時才保留 `run_id` / `parent_run_id`。

| 舊測試名稱 / 參數 | 新名稱 / 參數 |
|---|---|
| `router_run_id` | `trace_id` |
| `task_run_id` | `observation_id` |
| `run_id`，用來呼叫 router/task agent | `trace_id` 或 `observation_id`，依語意拆開 |
| `parent_run_id` | `parent_observation_id` |
| `trace_run_id` | `observation_id` |
| `AgentResult.trace_run_id` | `AgentResult.observation_id` |
| `ExecutionStep.run_id` | `ExecutionStep.observation_id` |
| `ExecutionStep.parent_run_id` | 不存在；父節點由 `trace_id` / `parent_observation_id` 在寫入時決定 |

### Router plan 測試範例

舊寫法：

```python
plan = _build_execution_plan(route, router_run_id="router-run", task_run_id="research-run")
assert plan.primary_step.run_id == "research-run"
assert plan.primary_step.parent_run_id == "router-run"
```

新寫法：

```python
plan = _build_execution_plan(route, trace_id="trace-1", observation_id="research-obs")
assert plan.trace_id == "trace-1"
assert plan.primary_step.trace_id == "trace-1"
assert plan.primary_step.observation_id == "research-obs"
```

### Router 呼叫 task agent 測試範例

Task agent fake 應檢查：

```python
assert kwargs["trace_id"] == "trace-1"
assert kwargs["observation_id"]
```

不要再檢查：

```python
kwargs["parent_run_id"]
kwargs["run_id"]
```

### AgentResult 測試範例

新 `AgentResult` 只有 `observation_id`：

```python
return AgentResult(
    response="answer",
    agent_name="retrieval",
    observation_id=kwargs.get("observation_id"),
)
```

---

## 禁止事項

- 不要在 LangChain callback method signature 裡改名 `run_id` / `parent_run_id`
- 不要把 `LocalTracer._events` 內部暫存 key `"run_id"` 改成 `"observation_id"`
- 不要把 LangGraph `config["run_id"]` 改名；這是框架 API key
- 不要在 router/task agent 的 domain code 裡新增 `router_run_id`、`task_run_id`、`parent_run_id`、`trace_run_id`
- 不要讓 task agent 自行建立新的 request-level `trace_id`；正式路徑必須由 router 建立並傳入
- 不要讓 task agent 互相以 tool 形式呼叫；跨 agent 流程必須由 router plan 表達
- 不要跳過 `write_agent_span()`；否則 observation tree 會缺少 task agent 的第一層 SPAN

---

## 最終目標樹

```text
thread_id
└── trace_id                                  request trace
    ├── Router SPAN                           observation_id = trace_id
    ├── Chat Agent SPAN                       observation_id = chat step id
    │   └── GENERATION
    ├── Retrieval Agent SPAN                  observation_id = retrieval step id
    │   └── LangGraph root CHAIN
    │       ├── GENERATION
    │       └── TOOL
    ├── Question Evidence Retrieval SPAN       optional
    │   └── LangGraph root CHAIN
    ├── Question Agent SPAN                    observation_id = question step id
    │   └── GENERATION
    ├── Research Agent SPAN                    observation_id = research step id
    │   └── Research workflow / LangGraph observations
    ├── Chat Composition SPAN                  optional final response composition
    │   └── GENERATION
    └── Evaluation Agent / EVALUATOR           optional explicit or background evaluation
```

這棵樹是 tests 應該守住的架構：router 擁有 `trace_id`，每個 agent invocation 擁有自己的 `observation_id`，所有子節點透過 `parent_observation_id` 串回 agent span。
