# Prompt 審查報告

根據 `prompt_engineering_guide.md` 整理的原則，逐一審查 `backend/prompts/` 下的 14 份 prompt 檔案。

審查維度：
- 指令清晰度（具體 vs 模糊）
- 正負指令寫法
- 結構（Identity / Instructions / Examples / Context）
- 數字門檻與校準錨點
- 與 Pydantic schema 的一致性
- 內部術語外洩
- 跨 prompt 一致性

---

## 一、跨 prompt 全域問題

這些問題出現在多個 prompt，應統一修正。

### 1.1 Section Delimiter 不一致

| Prompt | 目前用法 |
|--------|---------|
| core.txt | `【誠實】` `【證據優先】` |
| chat_mode.txt | `【核心角色】` `【互動原則】` |
| research_reflector.txt | 純數字列表，無 section header |
| research_scheduler.txt | 純段落 |
| summary_structure.txt | `==================================================` 分隔 |
| research_writer.txt | 已改為純列表 |

**建議**：統一用 Markdown `###` 做次層標題。`【】`和`====`在 LLM 解析時效果不一定差，但視覺一致性有助於維護。`===========`這種長分隔線屬於 summary_structure.txt 的早期遺留，可以清除。

**優先級**：低（功能性影響小）

---

### 1.2 模糊量詞

以下模糊詞出現在多份 prompt：

| 出處 | 模糊詞 | 建議替換 |
|------|--------|---------|
| core.txt L36 | 「盡量引用相關來源」 | 「若系統提供來源資訊，每個主要論斷後必須引用對應來源」 |
| retrieval_capability.txt L44 | 「多次無新資訊」 | 「連續 2 次搜尋均未找到新資訊」 |
| research_reflector.txt | 「多輪搜尋後仍沒有直接證據」（EXHAUSTED 定義） | 已在 status 判斷中有 3 個具體條件，此處為自由文字，可保留 |

---

### 1.3 負面指令比例偏高

以下 prompt 用「不要做 X」多於「做 Y」：

| Prompt | 例子 | 建議 |
|--------|------|------|
| core.txt | 「不猜測文件未寫內容」 | 「只根據文件中明確出現的內容回答」 |
| evaluation_agent.txt | 「不要補寫新答案。不要引入文件外知識。」 | 「只評估已提供的答案品質；所有評估判斷必須基於提供的 evidence 和 trace_summary」 |
| research_writer.txt | 原本有多個「不要」，已改善 | OK |

---

### 1.4 Output Format 未明確

下列 prompt 沒有說明輸出格式，依賴 Pydantic schema 隱性約束：

| Prompt | 問題 |
|--------|------|
| chat_mode.txt | 沒有輸出格式指引（自由文字，合理） |
| question_skill.txt | 沒有輸出格式，但 agent 可能有結構化輸出需求 |
| evaluation_agent.txt | 末尾只說「請輸出符合 schema 的結果」，但沒說 schema 長什麼樣 |

---

## 二、core.txt

### 現狀評估：⭐⭐⭐⭐（良好）

Identity 清楚，規則精簡，是所有 prompt 的基礎層。

### 問題

**P1：「已知資訊」定義不明**

```
誠實 1. 只根據已知資訊回答。
```

「已知資訊」可指模型的訓練知識、文件、對話記錄，三者都叫「已知」。對 RAG 系統來說，應明確說是「提供的文件或 evidence」。

**建議改法**：
```
【誠實】
1. 只根據系統提供的文件內容、evidence 或對話記錄回答。
```

**P2：「盡量引用」過於軟性**

```
【來源】
若系統提供來源資訊，盡量引用相關來源，不只依賴最後一次搜尋結果。
```

「盡量」給了模型逃避引用的空間。

**建議改法**：
```
【來源】
若系統提供來源資訊，每個主要論斷後必須引用對應來源頁碼或文件名稱，不只依賴最後一次搜尋結果。
```

**P3：職責說明太窄**

```
【職責】
此層只負責：可靠、清楚、誠實、一致。
```

這句話是給人讀的，不是給 LLM 讀的指令。可以刪掉或改成對 LLM 有意義的限制，例如：「本層的規則在所有模式中均適用，各模式的具體規則在模式 prompt 中另行說明。」

**優先級**：P1 高、P2 中、P3 低

---

## 三、chat_mode.txt

### 現狀評估：⭐⭐⭐⭐（良好）

Identity 清楚（「像真人助理」），互動原則合理，避免方式具體。

### 問題

**P1：Rule 2 和 Rule 3 隱性衝突**

```
【互動原則】
2. 問題不清楚時，先釐清重點。
3. 問題清楚時，直接幫忙，不拖延。
```

這兩條本身沒錯，但後面的「需求釐清」段列了三個問題，卻說「詢問應精簡，不要連問太多題」。如果模型只知道「最多一題」，應直接說「若需釐清，一次只問最關鍵的一個問題」。

**建議改法**：
```
若問題不清楚，一次只問最關鍵的一個釐清問題，不要連問多題。
```

**P2：【適用場景】可能導致限制性解讀**

```
【適用場景】
* 一般聊天
* 文件相關提問
...
```

如果模型把這個列表讀成「只能在這些場景使用」，可能拒絕處理不在列表的合理請求。建議改成「常見使用場景包括但不限於：」。

**P3：compose_final_response 路徑缺乏說明**

`chat_agent.compose_final_response` 用於格式化 task_result（router 在 research/retrieval 後呼叫），這個使用情境在 prompt 中完全沒有說明。模型在接收到 task_result 時，不知道應該如何處理。

**考量**：這部分的 human message 有另外注入說明（在 `chat_agent.py:163`），所以也許不必放進 system prompt。但如果有人只看 prompt，會看不懂這個 agent 的完整用途。

**優先級**：P1 中、P2 低、P3 待議

---

## 四、question_skill.txt

### 現狀評估：⭐⭐⭐（尚可）

目標清楚，互動原則合理，但缺乏具體範例和 edge case 處理。

### 問題

**P1：「先回答，再引導思考」與導讀目標矛盾**

```
【互動原則】
1. 先回答，再引導思考。
```

如果目標是 Socratic 教學（讓學生自己思考），先給答案會破壞這個目標。建議改為：

```
若使用者問的是知識性問題（研究內容），先簡短回答，再引導進一步思考。
若使用者表達想法或猜測，先確認方向，再補充或糾正。
```

**P2：缺乏輸出格式指引**

question_skill 的 agent 是自由文字回應，但沒有說明：
- 回答應該多長？
- 要分段嗎？
- 要 markdown 嗎？

建議補充：
```
【輸出格式】
回應控制在 150-300 字之間，視問題複雜度調整。
若要分步驟說明，使用數字列表。
不要加標題或章節符號，保持對話感。
```

**P3：缺乏「文件不在 context」的 edge case**

如果使用者沒有選取任何文件，question_skill 應該怎麼做？目前 prompt 完全沒有說明。建議加：

```
若沒有提供文件內容，以一般學習引導方式回應，並說明「需要選取文件才能針對具體研究內容出題或討論」。
```

**優先級**：P1 中、P2 中、P3 高

---

## 五、retrieval_capability.txt

### 現狀評估：⭐⭐⭐⭐（良好）

清楚、具體、規則可執行。Query 生成規則和停止條件特別好。

### 問題

**P1：「slot 詞」是 research graph 內部術語，不應出現在 retrieval agent prompt**

```
【Query 生成】
第一輪：slot 詞 + 文件名或題目關鍵詞。
```

`slot` 是 research graph 的概念，retrieval agent 不知道 slot 是什麼。這行對 retrieval agent 沒有意義。

**建議改法**：
```
【Query 生成】
第一輪：使用者問題的核心主題詞 + 文件名稱或題目關鍵詞。
```

**P2：USEFUL/NOT USEFUL 的寫法與 research graph 不一致**

Retrieval prompt 用 `USEFUL` / `NOT USEFUL`（無底線），reflector 用 `USEFUL` / `NOT_USEFUL` / `NO_RESULTS`（有底線）。這只是視覺不一致，沒有功能問題（這個判斷是人工閱讀用，不是 Structured Output）。

**優先級**：P1 高、P2 低

---

## 六、route_coordinator.txt

### 現狀評估：⭐⭐⭐⭐（良好）

決策樹清楚，各 agent 的使用時機有關鍵詞範例，JSON 輸出格式明確。

### 問題

**P1：關鍵詞列表過於具體，可能漏掉沒列的詞**

```
research_agent — 關鍵詞：「摘要」「總結」「研究動機」...
```

模型可能把不在列表的「幫我了解這篇」路由到 chat_agent。建議改成「典型關鍵詞（不限於此）：」並補充意圖描述。

**P2：reason 欄位沒有語言指定**

```
"reason": "一句話說明為什麼選這個 agent"
```

不確定模型會用中文還是英文。建議加「用繁體中文說明」。

**P3：多文件情境的路由規則不明確**

當使用者選了 2 份文件且問「比較這兩篇的方法」，應走 research（比較型）還是 retrieval？目前沒有明確說明。

**建議補充**：
```
多文件情境：
- 需要比較兩份文件的整體差異 → research_agent（比較型分析）
- 需要從兩份文件中找特定事實 → retrieval_agent（compare_documents tool）
```

**P4：evaluation_agent 的觸發條件「注意事項」冗長**

```
- 注意：只有明確針對「系統自己的回答」做評估才選此，不是文件內容評估
```

這句話好但放在說明後面容易漏讀。建議提前到 `evaluation_agent` 定義的第一行：

```
evaluation_agent — 評估「系統上一個回答」的品質（非評估文件本身）
```

**優先級**：P1 中、P2 低、P3 高、P4 低

---

## 七、evaluation_agent.txt

### 現狀評估：⭐⭐⭐（尚可）

七個維度的設計很好，但缺乏每個維度的數字校準錨點，導致模型對「3分」和「4分」的判斷可能不一致。

### 問題

**P1：缺乏 per-dimension 數字校準錨點**

`summary_quality.txt` 有這樣的設計：
```
5 — 明確說出研究缺口、現實問題或學術問題，有具體脈絡
3 — 有動機但模糊
1 — 套話
0 — 缺失
```

但 `evaluation_agent.txt` 的七個維度都沒有這種 5/3/1/0 錨點，只有文字描述。建議至少為最重要的 `grounding` 和 `task_fit` 加上錨點。

**建議補充範例（grounding）**：
```
grounding（0-5）：
5 — 每個主要論斷都有直接 evidence 或 source 支撐，無明顯臆測
3 — 多數論斷有支撐，但有 1-2 處無 evidence 的推論
1 — 大部分內容為 LLM 推測，sources 幾乎沒有被實際引用
0 — 完全無 evidence 支撐，或 answer 與 sources 相互矛盾
```

**P2：負面指令過多**

```
請避免：
- 不要補寫新答案。
- 不要引入文件外知識。
- 不要因為答案寫得好看就給高分。
- 不要只說「不錯」或「可改善」，必須指出具體 issue 或 gap。
```

四條都是「不要」。可以改寫為：
```
評估準則：
- 只評估提供的答案，不補充或改寫
- 所有判斷基於 evidence 和 trace_summary，不引入外部知識
- 分數反映實際品質，不因文字流暢就加分
- issues 必須指出具體問題（檔案頁碼、哪個論斷無根據）
```

**P3：overall 的計算方式未說明**

Pydantic schema 有 `overall` 欄位，但代碼中用 `_weighted_overall()` 計算（動機 0.25、任務 0.18、完整 0.17、具體 0.12、來源 0.10、誠實 0.10、格式 0.08）。模型填入的 `overall` 會被代碼覆蓋，所以模型不需要計算。

**考量**：這個欄位有 `default=0.0`，代碼會覆蓋。可以在 schema description 說明「此欄位由系統計算，請填 0」，避免模型浪費 tokens 計算。

**優先級**：P1 高、P2 中、P3 中

---

## 八、summary_structure.txt

### 現狀評估：⭐⭐⭐⭐⭐（優秀）

這是所有 prompt 中最完整、最詳細的一份。欄位規則清楚，有正負範例，誠實性規則和 LaTeX 規則特別好。

### 問題

**P1：`=====` 分隔符號不符合 Markdown 規範**

整份 prompt 用 `==================================================` 作分隔線（21 個字以上）。功能上沒問題，但不符合目前其他 prompt 的風格，也較難維護。

**建議**：改為 `---` 或 `###` 標題，視覺更清晰。

**P2：Rule 8「可依領域調整重點」描述片段**

```
8. 可依領域調整重點：工程、自然科學、資訊類研究要保留研究對象、模型或設備...；
   人文、社會、文本類研究要保留材料範圍...
```

這個規則與我們剛做的 task_planner P3 方向一致，但 summary_structure 沒有「社會科學」的對應說明，只有「理工 vs 人文」。

**建議補充**：
```
社會科學（心理、教育、管理、財務）：保留樣本規模、統計方法、量表工具、假設驗證結果與推論範圍。
```

**P3：重要規則被埋在後段**

「重要保留規則」（保留分類架構）和「誠實性規則」都放在 prompt 後段，但這些是最容易出錯的地方（模型最容易壓縮分類或省略限制）。

**建議**：將這兩個規則提前至「全域原則」區塊，或在欄位規則中直接引用。

**優先級**：P1 低、P2 中、P3 中

---

## 九、question_generator.txt

### 現狀評估：⭐⭐⭐⭐⭐（優秀）

結構完整，有正反範例，跨領域調整規則細緻，是所有 prompt 中設計最精良的之一。

### 問題

**P1：字數規範與 Pydantic schema 不一致**

Prompt 說：「每題 90 到 120 字」  
Pydantic schema 說：`InterestQuestion = Annotated[str, Field(min_length=35, max_length=120)]`

Schema 的 `min_length=35` 比 prompt 的 90 字低很多。如果模型遵守 prompt，生成的題目會符合 90-120，但 schema 不會拒絕 35 字的短題目。

**考量**：這是「prompt 更嚴格，schema 更寬鬆」的情況，實際上沒有問題（schema 是下限保護，prompt 是理想範圍）。但如果有人修改 prompt 降低字數，schema 不會阻止，可能被忽略。

**建議**：在 prompt 中加一行說明：「每題字數下限 90 字（schema 允許 35 字，但品質考量至少 90 字）」。或者直接把 schema 的 `min_length` 改成 90。

**P2：「你會收到一份由 research_agent 產生的摘要」過度耦合**

```
你會收到一份由 research_agent 產生的高資訊量研究摘要。
```

如果將來 question_generator 被用在其他 pipeline（例如直接給原始文件），這句話會造成混淆。建議改為「你會收到一份高資訊量的研究摘要」。

**優先級**：P1 中（schema 一致性）、P2 低

---

## 十、summary_quality.txt

### 現狀評估：⭐⭐⭐⭐⭐（優秀）

四個維度的 5/3/1/0 錨點設計是所有 prompt 中最佳的校準設計，和我們在 prompt_engineering_guide 建議的完全一致。

### 問題

**P1：overall 加權分數未說明**

與 evaluation_agent 同樣的問題：代碼用 `_weighted_overall()` 計算（motivation 0.25、method 0.30、results 0.25、limitations 0.20），但 prompt 沒有說明這個加權邏輯，模型的 `overall` 欄位填寫是無意義的（會被覆蓋）。

**建議**：在 prompt 末尾加一行：「`overall` 欄位由系統自動計算，請填 0.0，不需要手動計算加權分數。」

**P2：「若有提供 abstract_text」的反面情況未說明**

```
若有提供 abstract_text（原文節錄）：...
```

沒說「若沒有提供 abstract_text 時怎麼做」。實際上大多數呼叫都沒有 abstract_text（只有在文件已完成 Step 1 且有摘要時才有）。

**建議補充**：
```
若未提供 abstract_text：僅根據 summary_dict 的內容評分，
無法進行原文一致性驗證，limitations_honesty 的評分應相對保守。
```

**優先級**：P1 中、P2 中

---

## 十一、research_planner.txt

### 現狀評估：⭐⭐⭐⭐（良好）

已在之前整合討論中看過。規則清楚，有 query 類型說明（keyword vs semantic），停止條件明確。

### 問題

**P1：規則中有內部欄位名稱外洩**

```
- 不要把 coverage、expected_evidence、slot、research_ 等內部欄位詞當成檢索 query。
```

這條規則本身是正確的，但它暗示了模型「知道這些欄位」，這其實是 prompt 把 state 結構當作黑盒的一種間接說明。功能上沒問題。

**P2：`use_hyde` 的觸發條件可以更具體**

```
6. use_hyde：只有在已有 evidence_brief、next_search_angle 或弱檢索上下文時才可使用；
   第一輪不要憑空使用 HyDE。
```

「弱檢索上下文」是模糊的。建議改為：
```
6. use_hyde：只在以下情況設為 true：
   - 前一輪 quality = NOT_USEFUL 或 NO_RESULTS
   - evidence_brief 完全空白
   - 第一輪禁止使用 HyDE（direct search 優先）
```

**優先級**：P1 低、P2 中

---

## 十二、research_reflector.txt

### 現狀評估：⭐⭐⭐⭐（良好，剛更新過）

P4、P5 已在本 session 修正（成果報告書限制、FILLED 門檻）。

### 剩餘問題

**P1：EXHAUSTED 狀態的觸發條件未說明**

```
- EXHAUSTED：多輪搜尋後仍沒有直接證據，或文件很可能未明示此 item。
```

「多輪搜尋後」的判斷依據不明。實際上 EXHAUSTED 是由代碼控制（`same_slot_no_new >= max_consecutive_no_new`），不是 reflector 決定的。Reflector 不應該設定 EXHAUSTED——那是 `_run_single_slot` 的 slot_status override。

**考量**：讓 reflector 設定 EXHAUSTED 可能導致提前結束搜尋（模型覺得夠了就標 EXHAUSTED，但其實只搜了一次）。建議移除 EXHAUSTED 選項，讓 reflector 只能標 FILLED / PARTIAL / NOT_FILLED。

**這是個架構決策**：如果移除 EXHAUSTED，reflector 的選項更清晰，但 EXHAUSTED 的控制完全落到代碼層。目前代碼的 `slot_status` override 會蓋掉 reflector 的設定，所以讓 reflector 設 EXHAUSTED 其實是安全的（代碼會再覆蓋）——只是浪費模型的推理資源。

**優先級**：待議（架構選擇）

---

## 十三、research_scheduler.txt

### 現狀評估：⭐⭐⭐⭐（良好，剛更新過）

Rule 5（避免重複選同一 PARTIAL slot）剛加入。

### 剩餘問題

**P1：Scheduler 輸出 token 過高**

觀察：每次 scheduler 呼叫輸出約 820-828 tokens（所有候選 slot 的排序 + rationale）。這個成本來自要求「輸出 ALL candidate slots」。

**考量（拿不定主意）**：
- **方案 A**：維持現狀，輸出全部 candidates，讓 slot_executor 只取第一個。好處：scheduler 視角完整，可以看到所有 slot 的 hint。壞處：每次呼叫多 700+ tokens 的無用輸出。
- **方案 B**：改為只輸出「最優先的 2 個 slot」，降低 output token。好處：省 50-60% 的 scheduler output 成本。壞處：移除了所有 candidate 的排序視角，可能讓 hint 質量下降。
- **方案 C**：保持完整 slots 輸出，但加一個 `next_slot_id` 欄位讓執行層直接讀，避免解析 slots[0]。無成本影響，只是更明確。

目前代碼取 `ranked[0]`，方案 A 是現狀。建議議題留待壓測後再決定。

---

## 十四、research_writer.txt

### 現狀評估：⭐⭐⭐⭐（良好，剛更新過）

coverage_status-aware 規則剛加入，繁體中文語言指示已加入。

### 剩餘問題

**P1：缺乏長度和格式指引**

writer 的輸出格式完全依賴 `output_contract`（由 task_planner 產生），但 task_planner 的 output_contract 品質不穩定。如果 task_planner 產生的 output_contract 太簡單（例如只說「結構化摘要」），writer 不知道每個 section 應多長。

**建議**：加一個 fallback 規則：
```
若 output_contract 未指定長度，每個 section 以 150-300 字為目標，
整體答案不超過 1000 字，除非 output_contract 另有要求。
```

**P2：sections 和 answer 欄位的關係未說明**

輸出 schema 有 `sections: list` 和 `answer: string` 兩個欄位，但 prompt 沒有說明兩者的關係。

- `sections` 是給前端結構化顯示的
- `answer` 是給下游使用的純文字版本

如果 writer 不知道這個關係，可能在兩個欄位寫完全不同的內容，或者 `answer` 只是複製 `sections` 的文字。

**建議補充**：
```
answer 是最終給使用者閱讀的完整答案，應整合所有 section 的內容。
sections 是結構化版本，供前端分段顯示，與 answer 內容應一致。
```

**優先級**：P1 中、P2 高

---

## 十五、task_planner.txt

### 現狀評估：⭐⭐⭐⭐（良好，本 session 大幅更新）

P1（search_hints domain 術語）、P2（英文論文）、P3（人文/工程/社科 field-neutral template）都已修正。

### 剩餘問題

**P1：fallback 計畫（task_planner.py 的代碼）與 prompt 不完全對齊**

`task_planner.py` 的 `_summary_fallback_from_question()` 仍用固定的 `default_summary_coverage()` 作為最終 fallback，其中的 search_hints 是理工導向的（「研究動機」「研究背景」）。

如果 LLM plan 失敗，fallback 的 search_hints 不會享受到 P3 的 field-neutral 設計。

**這是一個代碼層問題**，不是 prompt 問題，但應該注意：改了 prompt 不等於改了 fallback 計畫。

**P2：Rule 2 的四個面向範例很長，可能讓 LLM 分心**

Rule 2 現在有 4 個面向 × 4 個領域 = 16 個子要點。對於一個「規劃任務」的 planner，這些範例是好的指引，但在 GPT-4.1 這種「更字面遵從指令」的模型上，可能會導致 LLM 每次都嘗試對應所有領域來選術語，增加不必要的推理成本。

**考量**：目前沒有足夠的測試資料判斷這是否是問題。建議跑幾份不同領域的文件後再評估。

---

## 十六、新增 Prompt 建議

### 16.1 建議新增：research_agent 通用說明 prompt

目前 research_agent 沒有自己的 system prompt——它是 `research_runtime` stack 的組合（由多個 prompt 疊加）。但整個 research graph 的整體目標、誠實性要求、輸出語言等，目前分散在各個 node prompt 裡，且有些 node（如 scheduler）根本沒有這些說明。

**建議**：建立一個 `research_base.txt` 或在 `core.txt` 的 `extends` 鏈中加一層 `research_core.txt`，統一說明：
```
你是 research graph 的一個節點，正在執行多輪 coverage-driven 文件研究。
你的所有輸出最終服務於 writer_node 的最終報告。
只使用文件中的直接證據，不補充訓練知識。
```

### 16.2 建議新增：一份「Prompt Quick Reference」

把所有 prompt 的關鍵規則（各一行）整理成一份供人讀的快速參考，類似測試 cheatsheet。這不是要上到 Langfuse 的東西，而是維護文件。

---

## 十七、優先修改清單

依影響程度排序：

| 優先 | Prompt | 問題 | 說明 |
|------|--------|------|------|
| 🔴 高 | research_writer.txt | sections 和 answer 關係未說明 | 可能導致兩欄位內容不一致 |
| 🔴 高 | core.txt | 「已知資訊」定義不明 | 影響所有模式的誠實性行為 |
| 🔴 高 | retrieval_capability.txt | slot 詞術語外洩 | retrieval agent 不知道 slot |
| 🔴 高 | question_skill.txt | 無文件時的 edge case 處理 | 目前會靜默失敗 |
| 🟡 中 | evaluation_agent.txt | 缺乏 per-dimension 數字錨點 | 評分一致性可能差 |
| 🟡 中 | evaluation_agent.txt / summary_quality.txt | overall 欄位應說明由系統計算 | 節省 LLM output tokens |
| 🟡 中 | research_writer.txt | 缺乏 fallback 長度規範 | 依賴 output_contract 品質 |
| 🟡 中 | route_coordinator.txt | 多文件路由規則不明 | 比較型 research vs retrieval 邊界 |
| 🟡 中 | research_planner.txt | use_hyde 觸發條件不夠具體 | 可能在不適當時使用 HyDE |
| 🟡 中 | question_generator.txt | 字數規範與 schema 不一致 | min_length 35 vs 90 |
| 🟡 中 | summary_quality.txt | 無 abstract_text 時的處理未說明 | 多數呼叫都沒有 abstract_text |
| 🟢 低 | 全域 | Section delimiter 不一致 | 視覺維護問題 |
| 🟢 低 | 全域 | 部分負面指令改為正面 | 輕微影響指令遵從率 |
| ⚪ 待議 | research_reflector.txt | 是否移除 EXHAUSTED 選項 | 架構決策，需要更多測試資料 |
| ⚪ 待議 | research_scheduler.txt | 輸出 token 過高（820/呼叫） | 需要壓測確認影響 |
