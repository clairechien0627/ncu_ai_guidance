# Prompt Engineering 實戰指南

整合四個來源與本專案（RAG-first 多代理人研究助理）的實際情境：

- OpenAI Best Practices for Prompt Engineering
- OpenAI Platform Docs — Prompt Engineering
- Agentic Workers — Mastering the Art of Communication with ChatGPT-4o
- OpenAI GPT-4.1 Prompting Guide

---

## 一、核心法則

### 1. 說要做什麼，不說不能做什麼

❌ 不好：

```text
DO NOT ASK USERNAME OR PASSWORD. DO NOT REPEAT.
```

✅ 好：

```text
The agent will attempt to diagnose the problem and suggest a solution,
whilst refraining from asking any questions related to PII.
Instead of asking for PII, refer the user to the help article.
```

本專案例子：

```text
# ❌
不要自行補充文件外的知識

# ✅
只能使用 evidence 與 sources 中可支持的內容。
若沒有直接證據，明確說「文件未明示」，不可推論補齊。
```

---

### 2. 具體、可測試，避免模糊形容詞

❌ 不好：`"The description should be fairly short, a few sentences only"`  
✅ 好：`"Use a 3 to 5 sentence paragraph to describe this product."`

| 模糊 | 具體 |
| ---- | ---- |
| 「充足的證據」 | 「3 個以上直接文件證據」 |
| 「簡短的 hints」 | 「3-8 個具體詞，包含至少 1 個來自標題的術語」 |
| 「如果沒有找到就說沒有」 | 「NOT_FOUND 狀態：寫「文件未明示」，OMITTED 狀態：跳過不寫」 |

---

### 3. Zero-shot 先，Few-shot 再，Fine-tune 最後

```text
✅ Zero-shot：先用清楚指令直接試
✅ Few-shot：模型理解錯誤時再加 1-3 個範例
✅ Fine-tune：有大量任務特定資料且品質要求極高時才考慮
```

本專案的 Structured Output 本身就有示範作用——Pydantic schema + Field description 等同 few-shot 告訴模型「輸出長什麼樣子」。

---

### 4. 指令放最前面，用 `###` / `"""` / XML tag 分隔資料

```text
Summarize the text below as a bullet point list.

Text: """
{text input here}
"""
```

本專案 Human Message 的慣例：用 JSON 包住所有資料，指令放在 `"instruction"` 欄位最後：

```python
HumanMessage(content=json.dumps({
    "question": question,
    "state": state.planner_prompt_dict(),
    "chunks": compact_chunks,
    "instruction": "列出所有被 chunks 直接支撐的 coverage item 更新。",
}, ensure_ascii=False))
```

---

## 二、常見錯誤與解法

| 錯誤 | 問題 | 解法 |
| ---- | ---- | ---- |
| 模糊指令 | 「Summarize this article」 | 「Summarize this article in 3 bullet points, under 50 words」 |
| 省略 context | 沒說角色或情境 | 「Context: You are a technical support agent. Respond with a concise, empathetic answer.」 |
| 任務堆疊 | 一個 prompt 五件事 | 拆成多個 prompt，或用 Prompt Chaining 依序執行 |
| 過寬的問題 | 「Tell me about marketing」 | 「List the top 3 digital marketing strategies for small businesses and explain their benefits」 |
| 忽略 AI 限制 | 假設 AI 什麼都知道 | 在 prompt 裡提供所有必要資料，不依賴 AI 的訓練知識 |
| 敏感資料直接貼入 | 違反保密協議 | 用匿名化或虛構資料替代真實資料 |

---

## 三、進階技術

### 3.1 Role-based Prompting

讓 AI 扮演特定角色，輸出更符合該視角的內容：

```text
Act as an HR recruiter: Generate three technical questions
to assess a junior Python developer.
```

```text
Context: You are a technical support agent.
Respond to this customer complaint with a concise, empathetic answer
and propose one practical solution.
```

本專案應用：每個 agent 的 Identity 段落就是 role-based prompting。

```text
你是 research reflector。
你會收到本輪 query、target coverage item、目前 state，以及搜尋到的 chunks。
```

---

### 3.2 Chain-of-Thought（CoT）Prompting

要求模型**先說明推理過程**再給結論，提高複雜任務的準確性：

```text
Explain the cause of the 2008 financial crisis.
First, outline key events; then summarize major outcomes;
finally, discuss implications for today.
```

```text
As a project manager, break down the project launch process:
first, list prerequisites; then, describe execution steps;
finally, outline the post-launch review protocol.
```

本專案適用場景：

- **scheduler** 的 `rationale` 欄位：要求說明排序理由，而非直接輸出順序
- **reflector** 的 `missing_gap` / `next_search_angle`：要求明確指出缺少什麼、下一步往哪搜

---

### 3.3 Meta Prompting（自我批評）

要求模型**批評自己的輸出**，提高可靠性：

```text
Reason step by step and critique your own answer.
```

```text
Can you review this response for accuracy and completeness?
```

**適合使用時機**：

- Writer 的品質門檻重試（writer_node 已實作：品質不過 → feedback 回傳 → 重試）
- Step 4 quality check：`score_extraction` 評估自己產出的摘要

---

### 3.4 Prompt Chaining（拆分複雜任務）

把一個大任務拆成多個依序的 sub-prompt，每個 sub-prompt 只做一件事：

```text
Step 1: "List the key market trends in renewable energy."
Step 2: "Based on those trends, suggest potential product strategies."
Step 3: "For each strategy, identify potential risks and mitigation approaches."
```

**注意事項**：

- 每個 sub-prompt 必須邏輯上銜接前一個輸出
- 不要跳過步驟或在中途新增無關任務

本專案就是 Prompt Chaining 的架構：

```text
task_planner → scheduler → planner → retriever → reflector → writer
```

每個 agent 的輸出是下一個 agent 的輸入。

---

### 3.5 Contextual Continuation（延伸對話）

在多輪對話中，明確告訴模型要延續前一輪的內容：

```text
Based on your last reply, can you elaborate on the implementation steps?
```

```text
Given the limitations you mentioned, what alternative approaches exist?
```

本專案應用：reflector 的 `next_search_angle` 和 `suggested_query_terms` 就是讓下一輪 planner 「基於上一輪找到的資訊繼續延伸」的機制。

---

### 3.6 格式混搭

混合使用 list、header、sequential instructions 可以提高輸出的一致性：

```text
Provide a step-by-step process for setting up a small home office.
Use bullet points for each step, with a bolded title for each step.
```

```text
Format the response as a JSON object with 'summary', 'challenges',
and 'recommendations' fields.
```

本專案：所有 Structured Output 都是「格式混搭」的極致版——Pydantic schema 完全定義輸出格式，Field description 定義每個欄位的填寫規則。

---

## 四、Prompt 結構設計

### Developer message 標準結構（依序）

#### Identity → Instructions → Examples → Context

```text
# Identity
你是 research reflector。你的工作是評估 chunks 是否支撐 coverage items。

# Instructions
1. 只收錄有直接文件根據的 evidence
2. NOT_USEFUL 時指出具體缺口
3. 給 planner 下一輪的具體搜尋方向

# Examples（選填，模型持續誤解時才加）
<example>
input: { "quality": "NO_RESULTS", "chunks": [] }
output: { "quality": "NO_RESULTS", "updates": [], "missing_gap": "未找到研究方法的直接描述" }
</example>

# Context（每次呼叫動態帶入）
chunks: ...
state: ...
```

---

### Developer vs User message 分工

| Developer message | User/Human message |
| ----------------- | ----------------- |
| 角色定義（Identity） | 當次任務的動態資料 |
| 不變的規則和限制 | 文件內容、evidence、chunks |
| 輸出格式要求 | 本輪 state（slot_status、evidence_brief） |
| Edge case 處理方式 | 使用者的 question |

> 本專案：System prompt 從 Langfuse 載入（可版本控制），Human message 從 `prompt_dict()` 動態產生。

---

### 提示快取（Prompt Caching）

OpenAI 對重複出現在 prompt 開頭的內容有快取機制。**靜態的規則放最前面，動態資料放最後**——本專案架構已符合這個設計：

```text
[System messages from Langfuse — 靜態，快取命中]
[Human message with state/chunks — 動態，每次不同]
```

---

## 五、Structured Output 的 Field Description 即 Prompt

本專案大量使用 `with_structured_output(PydanticModel)` + `strict=True`，Field description 直接決定模型行為。

### 寫 description 的原則

**好的判斷標準要互斥且完整：**

```python
# ❌ 模糊
quality: str = Field(description="品質評分")

# ✅ 明確定義每個值
quality: Literal["USEFUL", "NOT_USEFUL", "NO_RESULTS"] = Field(
    description=(
        "USEFUL：chunks 至少支撐一個 coverage item。\n"
        "NOT_USEFUL：有 chunks，但內容不支撐任何 coverage item。\n"
        "NO_RESULTS：沒有 chunks。"
    )
)
```

**帶數字門檻比模糊形容詞可靠：**

```python
# ❌
description="有足夠的直接文件證據"

# ✅
description=(
    "FILLED：已有 3 個以上直接文件證據覆蓋，且無明顯缺口。"
    "PARTIAL：有 1-2 個直接證據，但缺少細節或範例。"
    "NOT_FILLED：本輪片段不能支撐此 item。"
)
```

---

## 六、GPT 模型 vs Reasoning 模型

| | GPT 模型（gpt-4o, gpt-4o-mini） | Reasoning 模型（o1, o3, o4-mini） |
| -- | -- | -- |
| 比喻 | 新進員工，需要明確指令 | 資深員工，給目標即可 |
| Prompt 風格 | 詳細、有範例、逐步說明 | 高層目標即可，不需手把手 |
| temperature | 多數情況用 0（事實提取、分類） | 通常不調 |
| 適合任務 | 快速提取、分類、格式化 | 複雜推理、多步驟規劃 |

**本專案目前全部使用 GPT 模型**（gpt-4o / gpt-4o-mini），因此每個 agent 都需要**明確、詳細的指令**，不能假設模型會自行推理。

---

## 七、RAG 系統的 Prompt 特殊注意

### 5.1 Evidence-first 規則（每個 RAG prompt 都要有）

```text
只能使用 evidence 與 sources 中可支持的內容。
若沒有直接證據，說「文件未明示」，不可推論補齊。
```

### 5.2 避免空泛評語

```text
# ❌ 不好（模型會自行填充）
「這個發現具有重要意義」

# ✅ 好（要求有 evidence 才能說意義）
「若要寫成果的價值或意義，必須接著說明由哪個 evidence 支撐；
 無法從 evidence 推導的意義請省略。」
```

### 5.3 Context Window 管理

只傳必要資料給每個 agent：

| Agent | 需要的資料 |
| ----- | --------- |
| task_planner | question, task_context, document_context |
| scheduler | candidate_slots, evidence_counts, void_attempts, used_queries |
| planner | assigned_slot, hint, evidence_brief, used_queries, avoid_terms |
| reflector | query, target_slot, chunks（最多 6 筆）, coverage_items, slot_status |
| writer | 所有 evidence, coverage_status, output_contract, sources |

過多 context 不只浪費 token，也讓模型「注意力分散」。

---

## 八、多代理人系統的邊界設計

### 每個 Agent 只做一件事

每個 prompt 最後一行明確劃定邊界：

```text
不要直接回答問題，只做檢索規劃。   ← task_planner
不要執行搜尋，只輸出排序結果。     ← scheduler
不要選擇 slot，只規劃 query。     ← planner
不要撰寫最終答案，只評估 chunks。  ← reflector
不要做新搜尋，只根據 evidence 撰寫答案。 ← writer
```

### Agent 間資料傳遞

傳給 LLM 的資料統一用 JSON 格式，避免中英混合文字在解析時出問題：

```python
HumanMessage(content=json.dumps({
    "question": question,
    "task_context": task_context,
    "document_context": document_context,
    "instruction": "...",
}, ensure_ascii=False))
```

---

## 九、Parameters 設定參考

| 參數 | 建議值 | 說明 |
| ---- | ------ | ---- |
| `temperature` | `0` | 事實提取、分類、structured output 用 0；創意生成可提高到 0.3-0.7 |
| `temperature` | `0.2-0.3` | chat_agent、question_generator 等需要一點多樣性的場景 |
| `max_completion_tokens` | 不要設太低 | 是 hard cutoff，設太低會截斷輸出 |
| `stop` | 通常不設 | structured output 不需要 |

本專案的 `temperature=0` 場景：scheduler、planner、reflector、writer（事實導向）  
本專案的 `temperature=0.2`：chat_agent、evaluation_agent

---

## 十、Few-shot 範例格式（需要時才加）

```text
# Examples

<input id="example-1">
{ "slot": "research_methods", "chunks": [...], "evidence_brief": {} }
</input>

<output id="example-1">
{
  "quality": "USEFUL",
  "updates": [{"item_id": "research_methods", "status": "PARTIAL", "notes": [...]}],
  "missing_gap": "缺少方法的操作細節與步驟順序",
  "next_search_angle": "查找實驗步驟章節"
}
</output>
```

**只在模型持續誤解特定 case 時才加範例。** 範例要多樣——不要五個範例全部是 USEFUL，要涵蓋 NOT_USEFUL、NO_RESULTS。

---

## 十一、版本管理與測試

### 改 prompt 的流程

```bash
# 1. 修改 backend/prompts/*.txt
# 2. 在本機確認 diff
git diff backend/prompts/

# 3. 推到 Langfuse（選擇性）
cd backend
python scripts/sync_prompts.py task_planner reflector writer

# 4. 重啟後端讓 cache 重新載入
```

### 一次只改一個 prompt

改多個同時生效很難定位是哪個造成變化。觀察指標：

- Slot 達到 FILLED 的比例
- 平均搜尋輪數（search_count）
- Writer quality gate 通過率（不 retry 的比例）
- 每份文件的 LLM call count 和 token 成本

### 版本固定（Production 環境）

Langfuse 使用 `production` label，而非 `latest`，確保不因為調整 prompt 意外影響正在使用的服務。

---

## 十二、Agentic Workflow 三要素

GPT-4.1 指南實測：加上以下三條可讓 SWE-bench 通過率提升近 20%。任何 Agent prompt 都應包含。

### 1. Persistence（堅持完成）

```text
You are an agent - please keep going until the user's query is completely
resolved, before ending your turn and yielding back to the user.
Only terminate your turn when you are sure that the problem is solved.
```

防止模型在任務未完成時提前交回控制。

### 2. Tool-calling（用工具，不要猜）

```text
If you are not sure about file content or codebase structure,
use your tools to read files and gather the relevant information:
do NOT guess or make up an answer.
```

減少幻覺，強制模型先查再答。

### 3. Planning（計畫再行動，選填）

```text
You MUST plan extensively before each function call, and reflect extensively
on the outcomes of the previous function calls.
DO NOT do this entire process by making function calls only.
```

讓模型「思考出聲」而非默默串連 tool calls。實測提升 4% 通過率。

**本專案應用**：research agent 的設計本身就是這三個原則的體現——scheduler（計畫）、executor（行動）、reflector（反思）。

---

## 十三、指令撰寫的細節規則（GPT-4.1 特性）

**GPT-4.1 比 GPT-4o 更字面地遵從指令**——舊 prompt 可能行為改變。

### 指令衝突：後者優先

當 prompt 中有兩條互相衝突的指令時，**位置較後的指令**優先。

```text
# ❌ 結尾前說「簡短」，結尾後說「詳細列出所有項目」
→ 模型會傾向遵從「詳細列出」（因為在後面）

# ✅ 統一在 output_contract 或最後一段說清楚格式
```

### 工具描述的最佳實踐

- **透過 API `tools` 欄位傳工具**，不要手動注入工具描述到 system prompt（實測 2% 提升）
- 工具名稱清楚表達用途
- `description` 欄位：完整但精簡，說明何時用、用在什麼情境
- 工具用法範例放 system prompt 的 `# Examples` 段，不要塞進 description

### 不需要用 ALL CAPS 或給小費

GPT-4.1 對指令夠敏感，一句清楚的「必須」通常就夠：

```text
# 不必要
ALWAYS FOLLOW THESE RULES WITHOUT EXCEPTION!!!

# 足夠
請在每次回應的最後列出所有引用來源。
```

### 常見失敗模式

| 失敗情況 | 原因 | 解法 |
| -------- | ---- | ---- |
| 模型沒有足夠資料時仍呼叫工具（亂填參數） | 指令說「always call a tool」但沒說沒資料時怎辦 | 加「若資訊不足，先詢問使用者再呼叫工具」 |
| 反覆使用相同 sample phrase，聽起來很機械 | 只提供範例語句但沒說要變化 | 加「vary sample phrases to avoid sounding repetitive」 |
| 輸出比預期多很多說明文字或格式 | 模型預設加解釋 | 明確說明「只輸出 X，不加額外說明」並可附範例 |

---

## 十四、Long Context 的特殊技巧

### 指令位置：頭尾都放

長 context 時，模型對「中間」的指令注意力下降：

```text
[System 指令]

[大量 context 文件...]

[再次重申關鍵指令]  ← 加在最後，效果顯著優於只放前面
```

### Context 依賴程度調控

```python
# 只用外部 context（嚴格 RAG 模式）
"Only use the documents in the provided External Context to answer.
If you don't know the answer based on this context, respond
'I don't have the information needed to answer that'."

# 外部 context 為主，允許補充基礎知識
"By default, use the provided external context to answer.
If basic background knowledge is needed and you're confident,
you can use your own knowledge to supplement."
```

### 文件格式選擇

| 格式 | 適用 | 效果 |
| ---- | ---- | ---- |
| XML（帶 id/title 屬性） | 多份文件 | ✅ 最佳 |
| `ID: 1 \| TITLE: X \| CONTENT: Y` | 多份文件 | ✅ 很好 |
| Markdown | 一般 prompt 結構 | ✅ 好 |
| JSON | 文件列表 | ❌ 效果最差 |

XML 範例：

```xml
<doc id="1" title="研究方法">
這篇論文使用 XPS 分析...
</doc>
```

---

## 十五、標準 Prompt Structure 模板

OpenAI GPT-4.1 指南推薦的起始模板（依需求增刪段落）：

```text
# Role and Objective
（角色定義 + 任務目標）

# Instructions
（規則列表）

## Sub-categories
（更細節的規則分類，例如 Tone / Output Format / Prohibited Topics）

# Reasoning Steps
（若需要 CoT：列出推理步驟）

# Output Format
（輸出格式規範，包含欄位、長度、語言）

# Examples
（few-shot 範例，用 XML tag 包裹）

# Context
（動態資料：每次請求不同的 document、evidence、state 等）

# Final Instructions
（收尾提示，例如「Think step by step before answering」）
```

**本專案 Prompt 與此模板的對應**：

| 模板段落 | 本專案實作位置 |
| -------- | ------------ |
| Role and Objective | System prompt 第一行（你是 X，你的工作是 Y） |
| Instructions | System prompt 的規則列表（Rules 1-12） |
| Output Format | Pydantic schema + Field description |
| Examples | system prompt 的 `# Examples` 段（需要時） |
| Context | Human message（JSON：question、state、chunks） |
| Final Instructions | Human message 的 `"instruction"` 欄位 |

---

## 十六、常見陷阱速查

| 陷阱 | 說明 | 解法 |
| ---- | ---- | ---- |
| 只說不能做什麼 | 負面指令比正面指令弱 | 改成「請做 X」而非「不要做 Y」 |
| 門檻模糊 | 「足夠」「清楚」「簡短」 | 加數字：「3 個以上」「50 字以內」 |
| 指令衝突 | System 說簡短，output_contract 說詳細 | 統一放最後一段；GPT-4.1 以後出現的指令優先 |
| System prompt 太長 | 超過一定長度模型會漏讀規則 | 最重要的規則放前面（和最後面）；long context 頭尾都要放 |
| 任務混合 | 一個 prompt 又要規劃又要執行 | 拆成兩個 LLM 呼叫，各司其職 |
| 沒有 fallback | Structured output 解析失敗就整個爆掉 | 每個 `ainvoke_traced_generation` 都有對應 fallback 邏輯 |
| 靜態資料放最後 | 快取命中率低 | 靜態 System prompt 放最前，動態 Human message 放最後 |
| Agent 無資料仍呼叫工具 | 指令說「always call tool」但沒說沒資料怎辦 | 加「若資訊不足，先詢問使用者」 |
| Sample phrase 被逐字重複 | 沒指示要變化語句 | 加「vary phrases to avoid sounding repetitive」 |
| 工具描述塞太多範例 | description 欄位過長 | 範例移到 system prompt 的 `# Examples` 段 |
| 手動注入工具描述到 prompt | 脫離模型訓練分佈，增加解析錯誤 | 透過 API `tools` 欄位傳工具 schema |
