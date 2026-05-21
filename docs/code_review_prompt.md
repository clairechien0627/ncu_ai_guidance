# Code Review Prompt

通用框架，不綁定任何架構。貼進任何 vibe coding 工具，讓它自己探索 repo 後依序回答。

分三個階段執行，每個階段是獨立的對話。輸出要**分兩個檔案**：

- `review-narrative.md`：人類閱讀用的分析說明
- `review-issues.md`：機器可讀的問題清單（讓 Claude Code 之後能直接處理）

---

## PART A｜系統級審查

```text
你是這個 repo 的資深架構師，負責做全面的 code review。

第一步：自己探索這個 repo 的結構。
讀 README、設定檔、進入點、主要模組目錄。
不要問我要讀哪些檔案，自己判斷。

探索完之後，依序回答以下問題。每個問題都要基於你實際讀到的程式碼，不能憑空推測。

---

A1. 畫出系統地圖

用文字畫出這個系統的架構圖：
- 有哪些主要層次或模組？
- 它們之間如何呼叫或傳遞資料？
- 哪個模組是核心？哪些是邊緣服務？

這是你後續所有分析的基礎，要畫得夠清楚。

---

A2. 找出入口到出口的完整路徑

選一個最核心的功能（你自己判斷哪個最重要），
追蹤一個請求從進入系統到回應的完整路徑。

標出：
- 每一跳在哪個檔案的哪個函數
- 資料的格式在哪裡轉換
- 哪個環節最容易出錯

---

A3. 架構框架合規性評估

先判斷這個 codebase 明顯在對標哪些架構風格（例如：分層架構、Clean Architecture、Event-Driven、Multi-Agent Orchestrator 等），然後依序評估以下框架的合規程度。

**12-Factor App（重點檢查以下 Factor）**

| Factor | 要檢查什麼 |
|--------|----------|
| III. Config | 設定值是從環境變數讀取，還是硬寫在程式碼或 config 檔裡？ |
| VI. Processes | 有沒有仰賴 in-process 記憶體（singleton、global state）跨請求共享狀態？這個限制有沒有明確文件？ |
| IX. Disposability | 是否有 graceful shutdown？SIGTERM 時能否不遺漏資料地停止？ |
| XI. Logs | log 是結構化輸出（JSON）還是純文字？有沒有集中設定？ |
| XII. Admin | 管理用的一次性任務（migration、資料修復）是否有獨立入口點，還是混在應用啟動邏輯裡？ |

列出每個 Factor 的實際狀況，標注：✅ 符合 / ⚠️ 部分符合 / ❌ 不符合。

**SOLID 原則**

- **S（Single Responsibility）**：找出承擔超過一個職責的模組或類別，說明它同時在做什麼。
- **O（Open/Closed）**：找出需要修改既有程式才能擴充行為的地方（不符合），以及可以透過組合或繼承擴充的地方（符合）。
- **D（Dependency Inversion）**：高層模組（業務邏輯）有沒有直接依賴低層實作（資料庫、外部 API）？還是透過介面或注入隔離？

**Clean Architecture / 分層邊界**

- 找出層次違反：上層被下層直接 import，或業務邏輯直接操作 I/O 細節。
- 找出洩漏抽象：框架物件（ORM model、HTTP request）穿透到業務邏輯層。
- 評估核心業務邏輯能否在不啟動外部服務（DB、API）的情況下被測試。

**設計模式正確性**

如果 codebase 使用了特定模式（Orchestrator-Worker、Publisher-Subscriber、State Machine、Retry with Fallback 等），評估這些模式是否被正確實作，有沒有常見的反模式（例如：Orchestrator 自己執行任務、State Machine 有跳轉邏輯散落在多個地方）。

---

A4. 評估模組職責

找出以下問題：
- 哪個模組承擔了不屬於它的職責（職責過重）
- 哪些功能被重複實作在不同模組裡
- 模組之間的依賴方向是否合理（有沒有下層依賴上層）
- 有沒有隱性耦合（沒有透過介面、直接使用另一模組的內部細節）

---

A5. 評估資料契約

找出系統中的資料結構定義（型別、schema、model 等）：
- 這些定義有沒有被一致地使用？
- 有沒有地方繞過型別直接用 dict、any、raw string？
- 不同模組對同一份資料的理解是否一致？

---

A6. 評估錯誤處理

追蹤這個系統的錯誤處理策略：
- 例外在哪裡被捕捉？在哪裡被忽略（silent failure）？
- 錯誤有沒有被正確轉換成使用者可理解的回應？
- 外部依賴（資料庫、API、LLM）失敗時系統怎麼反應？
- retry 策略是否存在且合理？

---

A7. 評估非同步與並發

找出系統中所有涉及並發、非同步、或背景執行的地方：
- 有沒有在非同步環境中呼叫同步阻塞操作？
- 共享狀態有沒有做好隔離？
- 連線池、session 的生命週期管理是否正確？

---

A8. 安全性評估

審查以下面向：
- 使用者輸入在哪些地方進入系統？有沒有驗證？
- 有沒有地方把外部輸入直接拼進指令、查詢、或 prompt？
- 敏感資料（金鑰、個資、token）有沒有出現在 log 或回應裡？
- 檔案操作有沒有路徑遍歷風險？

---

A9. 效能評估

找出以下問題：
- 迴圈中有沒有重複的 I/O（應該 batch 但逐筆做）
- 應該快取但每次重新計算的操作
- 不必要的重複外部呼叫（API、資料庫、LLM）
- 大型資料在記憶體中的不必要複製或持有

---

A10. 可觀測性評估

評估系統的 logging、tracing、monitoring：
- 核心路徑有沒有足夠的 log？
- 錯誤有沒有足夠的 context 可以事後追查？
- 如果線上出問題，你能不能從現有的可觀測性工具找出根因？

---

A11. 架構健康度總評

輸出這張表，每項 1-5 分，附上最關鍵的一句話說明：

| 維度 | 分數 | 最關鍵的問題或優點 |
|------|------|-----------------|
| 模組職責清晰度 | | |
| 資料契約一致性 | | |
| 錯誤處理完整性 | | |
| 非同步正確性 | | |
| 安全性 | | |
| 效能 | | |
| 可觀測性 | | |
| 可測試性 | | |
| 整體可維護性 | | |
| 12-Factor 合規性 | | |
| SOLID 遵循度 | | |
| 分層邊界清晰度 | | |

---

把上面 A1-A11 的分析結果寫進 review-narrative.md。

同時，把你在 A3-A10 發現的所有問題，按照以下格式寫進 review-issues.md（見下方 OUTPUT FORMAT 說明）。
```

---

## PART B｜逐檔審查

```text
繼續用你在 PART A 建立的系統理解。

現在對每一個非第三方的程式檔案做逐行審查。
自己列出所有要審查的檔案，排除 node_modules、venv、dist、build 等產生的檔案。

審查順序：
1. 先審核心資料結構和型別定義（最先）
2. 再審核心業務邏輯
3. 再審 I/O 邊界（API、資料庫、外部服務）
4. 最後審測試

每個檔案審查時，找以下類別的問題：
- 正確性（邏輯錯誤、邊界條件、None 未處理）
- 型別安全（繞過型別、隱性轉換）
- 非同步（missing await、blocking call）
- 效能（N+1、重複計算、缺少 batch）
- 安全性（injection、敏感資料外洩）
- 可維護性（過長函數、重複程式碼、魔術數字、死程式碼）
- 錯誤處理（silent failure、錯誤分類錯誤）
- LLM 特定（輸出未驗證就解析、token 超限風險、prompt 拼接不安全）
- 架構違反（層次越界、12-Factor 不符、SOLID 破壞、設計模式誤用）

把所有發現的問題追加到 review-issues.md，格式見 OUTPUT FORMAT。

審查完所有檔案後，在 review-narrative.md 尾部加上統計：
- Critical：X 個
- High：X 個
- Medium：X 個
- Low：X 個
```

---

## PART C｜總結

```text
你已經審查完整個 repo。在 review-narrative.md 補充以下內容：

1. 重複出現的反模式
   在多個檔案看到的相同問題，每種列出受影響的 issue ID。

2. 重構優先順序
   以「修這個能解鎖最多其他問題」為標準：
   列出 issue ID + 一句話理由，由高到低排序。

3. 下一步行動
   - 必做（直接影響穩定性）：列 issue ID
   - 應做（影響長期維護性）：列 issue ID
   - 有空可做（改善開發體驗）：列 issue ID
```

---

## OUTPUT FORMAT｜review-issues.md 格式規範

> 這個格式是給 Claude Code 等 AI 工具之後讀取並執行修改用的。
> 不要用 markdown table。每個問題是一個獨立的 block，用 `---` 分隔。

```markdown
# Review Issues

generated: YYYY-MM-DD
repo: [repo 名稱或路徑]

---

## 格式說明（此段之後不要出現在輸出中）

每個 issue block 的欄位：
- id: 編號格式 P[phase]-[sequence]，例如 P1-001
- severity: CRITICAL / HIGH / MEDIUM / LOW
- category: 類別（error-handling / type-safety / async / performance / security / maintainability / llm-specific / architecture）
- status: OPEN（不要改這個欄位，由 Claude Code 之後更新）
- file: 相對路徑，例如 backend/rag/parsers.py
- lines: 行號，例如 146-147 或 146
- title: 30 字以內的問題描述
- problem: 一句話說清楚問題是什麼
- impact: 不修的後果
- fix: 建議修法（一句話，具體）
- before: 修改前的程式碼片段（僅 CRITICAL / HIGH 需要，選填）
- after: 修改後的程式碼片段（僅 CRITICAL / HIGH 需要，選填）

---

id: P1-001
severity: CRITICAL
category: error-handling
status: OPEN
file: backend/rag/parsers.py
lines: 146-147
title: pymupdf cache write silently ignored
problem: except Exception: pass 吃掉磁碟空間不足或權限錯誤
impact: cache 寫入失敗後下次仍重新解析，浪費 API 配額且無法察覺
fix: 改為 logger.warning("cache write failed: %s", e)
before: |
  except Exception:
      pass
after: |
  except Exception as e:
      logger.warning("pymupdf4llm: cache write failed for doc %s: %s", doc_id, e)

---

id: P1-002
severity: HIGH
category: async
status: OPEN
file: backend/rag/section.py
lines: 372
title: time.sleep blocks event loop in async context
problem: rate-limit retry 使用同步 time.sleep，若在 event loop 上呼叫會阻塞整個伺服器
impact: 觸發 rate limit 時其他所有請求被阻塞最多 40 秒
fix: 確認 process_pdf 只在 asyncio.to_thread 中執行（已是）；加注解說明此依賴
before: ~
after: ~

---
```

> **重要規則：**
>
> 1. `id:` 在整個檔案中唯一，不重複
> 2. `status:` 只能是 OPEN，不要在這裡寫 RESOLVED（由使用者之後標記）
> 3. `file:` 必須是相對路徑，不帶引號
> 4. `lines:` 是數字，不帶引號
> 5. `before:` 和 `after:` 只在 CRITICAL / HIGH 時填，LOW / MEDIUM 留 `~`
> 6. `before:` 和 `after:` 用 YAML literal block（`|`）格式，縮排 2 格
> 7. 每個 block 之間用 `---` 分隔
> 8. 不要在 `title:`、`problem:`、`fix:` 中使用 markdown 語法（不要加 `**`、backtick 等）
> 9. 檔案最後要有一個 `---`

---

## 使用方式

把 PART A、B、C 的 prompt 文字分別貼進 vibe coding 工具執行。
完成後你會得到兩個檔案：

- `review-narrative.md`：閱讀用，了解架構和重構方向
- `review-issues.md`：行動用，直接交給 Claude Code 逐一處理
