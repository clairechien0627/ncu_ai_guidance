# Report Agent — 架構評估報告

**評估日期**：2026-05-21  
**評估依據**：12-Factor Agents（Dex Horthy / HumanLayer）  
**測試狀態**：278 passed, 18 skipped

---

## 12-Factor Agents 對照

| # | Factor | 說明 | 評分 | 備註 |
| --- | -------- | ------ | ------ | ------ |
| 1 | Natural Language → Tool Calls | NL 轉結構化工具指令 | ✅ | `_orchestrate()` → `RouterDecision(agent_name)`；工具用 Pydantic schema |
| 2 | Own Your Prompts | Prompt 版本控制 | ✅ | `prompts/` 目錄 + Langfuse versioning + 本地 txt fallback |
| 3 | Own Your Context Window | 主動管理 context | ✅ 強 | Summarization/ContextEditing middleware；memory_service 三層記憶 |
| 4 | Tools = Structured Outputs | 工具只是 JSON schema | ✅ | `SearchInput` Pydantic model；`@tool` 裝飾 |
| 5 | Unify Execution & Business State | 執行狀態與業務狀態統一 | ✅ | LangGraph checkpoint + `Conversation.last_agent_name`；routing state 已從 trace 解耦 |
| 6 | Launch, Pause, Resume | 可啟停恢復 | ✅ | `HumanInTheLoopMiddleware` + LangGraph checkpointer + `resume_conversation` API |
| 7 | Contact Humans via Tool Calls | 人類介入是一等公民 | 🟡 | 框架存在（`interrupt_on={}`），目前未啟用 |
| 8 | Own Your Control Flow | 明確控制 agent loop | ✅ | MAX_HANDOFFS + ModelCallLimitMiddleware；re-routing 邏輯明確且有 visited_agents 防護 |
| 9 | Compact Errors into Context | 錯誤壓縮回 context | ✅ | `AgentStatus.gaps` + research reflector 處理搜尋失敗 |
| 10 | Small, Focused Agents | 小而專注的 agent | ✅ | 5 個職責清晰的 agent；research 內部再分 planner/retriever/reflector/writer |
| 11 | Trigger from Anywhere | 任何地方觸發 | 🟡 | 目前只有 HTTP；無 webhook / message queue |
| 12 | Stateless Reducer | Agent 是純函式 | 🟡 | `AgentResult` frozen dataclass（好）；`runner.py` 有全局 LLM/checkpointer 單例 |

## 總結：符合 9/12，部分符合 3/12，缺失 0/12

---

## 架構優點

### 記憶體系（完全自足，可移植）

```text
LangGraph checkpoint     ← 完整對話訊息歷史
Conversation.context_summary   ← research 摘要（短期記憶）
Conversation.last_agent_name   ← routing state（本次新增）
memory_service (Qdrant)  ← 長期語意記憶
document_research_cache  ← 文件研究快取（per-document, per-coverage）
```

Agent 核心完全不讀 trace 做決策；trace 系統僅作 observability 用途。

### Middleware 堆疊

12 層清晰的 middleware：token cap、call limit、summarization、fallback、retry、human-in-the-loop。每層職責單一，可獨立測試。

### 路由架構

```text
route_request()          ← 公開 API（Orchestrator LLM 決策）
  └─ _orchestrate()      ← 帶 document context + agent_status
      └─ _route_for_agent() → AgentRoute
          └─ route_agent_message / route_agent_stream
              └─ _escalation_route_for()  ← 防 ping-pong
```

路由決策集中在 router，agent 不知道彼此的存在。

### AgentStatus 設計

每個 agent 回傳自我評估，讓 Orchestrator LLM 可以做出有根據的 re-routing 決策，而非 agent 直接指定下一步。

---

## 本輪改動清單

| 改動 | 說明 | Commit |
|------|------|--------|
| `Conversation.last_agent_name` | routing state 從 TraceV2 改讀 Conversation，agent 核心完全解耦 trace | `dd15b24` |
| `_escalation_route_for()` helper | 提取 escalation 決策邏輯，消除 route_agent_message/stream 重複 | `7c03a94` |
| `_visited_agents` tracking | 防止 retrieval→research→retrieval 等跨 hop 循環，frozenset 傳遞 | `7c03a94` |
| `AgentStatus` work_summary 動態化 | chat_agent 根據有無文件給出不同摘要 | `7c03a94` |

---

## 剩餘改進方向

### Factor 7：Human-in-the-Loop（中優先）

```python
# 現在
HumanInTheLoopMiddleware(interrupt_on={})   # 未啟用

# 目標
HumanInTheLoopMiddleware(interrupt_on={"high_risk_tool", "write_operation"})
```

需要定義哪些 tool 呼叫需要人類確認，並在前端實作確認 UI。

### Factor 12：Runner 全局狀態（低優先）

`runner.py` 的 `_checkpointer`、`_llm`、`_tool_agent` 等全局單例是模組初始化副作用。
嚴格的 stateless reducer 設計應將這些依賴注入，但對於 Python FastAPI 服務這是可接受的折衷。

### route_agent_stream 非串流路徑

`research_agent` 和 `evaluation_agent` 在 stream 函式內仍是 `await`（非真正串流），最終以 `yield result.response, False, []` 一次性輸出。未來可改為真正的 token-by-token 串流。

### 評估 Agent 獨立化

`_run_evaluation_agent` 目前在 `router_agent.py` 內，本質上是 evaluation_agent 的入口點。若 evaluation 邏輯增長，可考慮移到 `evaluation_agent.py` 並提供標準的 `answer()` 介面。

---

## 技術負債追蹤

| 項目 | 優先 | 說明 |
|------|------|------|
| Factor 7 HumanInTheLoop | 🟡 中 | 框架已就位，需定義 interrupt_on 規則 |
| route_agent_stream 真正串流化 | 🟢 低 | research/evaluation 目前是偽串流 |
| evaluation_agent 標準化介面 | 🟢 低 | 移出 router_agent.py |
| Factor 11 多管道觸發 | 🟢 低 | 加 webhook/Slack/排程支援 |
