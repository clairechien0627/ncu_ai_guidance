# Prompt Quick Reference

14 份 prompt 的角色與關鍵規則速查。詳細審查與設計原則見 `docs/prompt_review.md` 與 `docs/prompt_engineering_guide.md`。

---

## 共用基礎層

### core.txt
所有模式的共用誠實性規則，由 `runtime_prompts.py` 注入 research graph 所有節點。
- 只根據系統提供的文件內容、evidence 或對話記錄回答
- 推測必須明確標示，不以肯定語氣陳述
- 有來源時，每個主要論斷後必須引用來源頁碼或文件名稱

---

## 聊天與互動層

### chat_mode.txt
一般對話、文件提問、追問的互動模式。
- 問題不清楚時，一次只問最關鍵的一個釐清問題
- 適用場景「常見，不限於此」，不拒絕列表外的合理請求

### question_skill.txt
研究導讀問答，目標是引導思考而非直接給答案。
- 知識性問題：先簡短回答，再引導進一步思考
- 使用者表達猜測：先確認方向，再補充或糾正
- 無文件時：說明需先選取文件，並以一般學習引導方式回應
- 回應控制在 150-300 字，使用數字列表時不加標題符號

---

## 路由層

### route_coordinator.txt
根據使用者意圖選擇 task agent，輸出 `{intent, evaluate_after, reason}`。
- 多文件比較整體差異 → `research_agent`；多文件查特定事實 → `retrieval_agent`
- 訊息模糊優先選 `chat_agent`，不亂猜 intent
- `evaluation_agent` 只用於明確針對「系統上一個回答」做評估

---

## 專才 Agent 層

### retrieval_capability.txt
精準文件查找，先找證據再回答。
- 第一輪：核心主題詞 + 文件名稱或題目關鍵詞
- 第二輪起：從上一輪找到的內容抓具體詞（方法名、分類名、章節名）
- 連續 2 次搜尋均未找到新資訊 → 停止
- `NOT_USEFUL` 時改用章節詞或 `search_by_section`

### evaluation_agent.txt
評估系統輸出的品質，7 個維度各 0-5 分。
- `grounding` 和 `task_fit` 有 5/3/1/0 錨點（其餘維度文字描述）
- 只評估已提供的答案，不補充或改寫
- `overall` 填 0.0，由系統加權計算（`_weighted_overall()`）

---

## 摘要與生成層

### summary_structure.txt
把高資訊量研究摘要結構化成 `motivation / method / results / tags`，目標讀者高中生。
- 先判斷領域（工程/自然科學/人文/社會科學）再用對應重點
- 格式由內容決定：有多個並列項目才用條列，否則用段落
- 分類架構必須完整保留（不可壓縮成一句話）
- 限制、失敗、未完成事項不可省略，不得反向改寫

### summary_quality.txt
評估結構化摘要的品質，4 個維度各 0-5 分。
- `motivation_clarity / method_specificity / results_concreteness / limitations_honesty`，各有 5/3/1/0 錨點
- 無 `abstract_text` 時：只根據 summary_dict 評分，`limitations_honesty` 應保守
- `overall` 填 0.0，由系統加權計算

### question_generator.txt
生成高中生導讀（intro）與 3 題興趣量表（questions）。
- 每題 90-120 字，三題維度：情境吸引力 / 研究方式吸引力 / 思考方式吸引力
- 不出知識考題，不可杜撰摘要沒提到的內容
- 只根據提供的研究摘要生成

---

## Research Graph 節點層

> 所有節點都透過 `runtime_prompts.py` 自動注入 `core.txt`。

### task_planner.txt
把使用者問題轉成 3-6 個 coverage items（`ResearchPlan`）。
- 先判斷文件領域，再只參考該領域對應的術語，不混用多個領域
- `search_hints` 必須優先使用文件中出現的具體術語（不用通用章節詞）
- 英文文件 → `search_hints` 全用英文術語，label 仍繁體中文
- 不直接回答問題，只做規劃

### research_planner.txt
為指定 coverage slot 生成 `keyword_query + semantic_query + section_terms`（`PlannerDecision`）。
- 第一輪禁用 HyDE；HyDE 只在前輪 `quality = NOT_USEFUL/NO_RESULTS` 時使用
- 第二輪起必須從上一輪結果抓具體詞，不重複 `used_queries`
- `assigned_slot` 存在時，`next_slot` 必須等於 `assigned_slot`

### research_reflector.txt
判斷本輪 chunks 是否支撐 coverage items，輸出 `Reflection`（含 `updates / quality / missing_gap`）。
- `status` 只有三個值：`FILLED / PARTIAL / NOT_FILLED`（EXHAUSTED 由代碼控制）
- `FILLED`：3 個以上直接文件證據，無明顯缺口
- `PARTIAL`：1 個以上直接證據但不完整
- 成果報告書的「計畫成果自評」「未來展望」「建議」等章節可作為限制 item 的有效 evidence

### research_scheduler.txt
排序候選 slots，決定下一輪要搜尋哪個（`SchedulerDecision`）。
- 只輸出最優先的 1 個 slot（系統自動補回其餘 candidates）
- `NOT_FILLED` 且有明確 `search_hints` 優先；`void_attempts > 0` 排後面
- 某 PARTIAL slot 連續 2 輪無 evidence 成長 → 降低優先級

### research_writer.txt
根據 evidence 撰寫最終答案（`WriterOutput`：`sections + answer + sources`）。
- `FILLED / PARTIAL / EXHAUSTED`：正常撰寫，不加「資料有限」說明
- `NOT_FOUND`：明確說「文件未明示」，不推論補齊
- `OMITTED`：跳過，不寫 section
- `sections` 和 `answer` 內容必須一致；`answer` 整合所有 sections
- 無長度規範時：每個 section 150-300 字，整體不超過 1000 字
