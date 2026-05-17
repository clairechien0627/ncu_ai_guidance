# Scripts

工具腳本依功能分類，全部從 `backend/` 目錄執行。

```
scripts/
├── prompts/       Langfuse prompt 管理
├── data/          資料匯入匯出
├── llamaparse/    LlamaParse 文件解析
└── debug/         Debug 與分析工具
```

---

## prompts/ — Prompt 管理

### sync_prompts.py
推送本地 prompt 到 Langfuse（production label）。預設只推內容有變動的。

```bash
python scripts/prompts/sync_prompts.py                        # 只推有變動的（SHA 比對）
python scripts/prompts/sync_prompts.py task_planner reflector # 指定 prompt（支援部分名稱）
python scripts/prompts/sync_prompts.py --all                  # 強制全部推
python scripts/prompts/sync_prompts.py --dry-run              # 預覽，不實際寫入
python scripts/prompts/sync_prompts.py --list                 # 列出所有本地 prompt
```

### pull_prompts.py
從 Langfuse 拉回 prompt 到本地 .txt 檔（反向同步）。

```bash
python scripts/prompts/pull_prompts.py                        # 拉回全部
python scripts/prompts/pull_prompts.py chat_mode              # 指定 prompt
python scripts/prompts/pull_prompts.py --dry-run              # 預覽差異
```

### smoke_langfuse_prompt_links.py
驗證 Langfuse prompt-linked generations 是否正常，不需要啟動 FastAPI server。

```bash
python scripts/prompts/smoke_langfuse_prompt_links.py
```

---

## data/ — 資料管理

### import_data.py
從 data-package 匯入文件資料到系統。

```bash
python scripts/data/import_data.py
```

### export_data.py
匯出系統資料到 data-package。

```bash
python scripts/data/export_data.py
```

### extract_abstracts.py
批次從資料庫文件抽取 PDF 摘要，輸出至 `scripts/data/pdf_abstracts.json`。

```bash
python scripts/data/extract_abstracts.py
```

### import_abstracts.py
將 `scripts/data/pdf_abstracts.json` 的摘要匯入資料庫。

```bash
python scripts/data/import_abstracts.py
```

---

## llamaparse/ — LlamaParse 文件解析

### batch_llamaparse.py
批次預快取 PDF（LlamaParse），用於掃描圖片型或亂碼文件。

```bash
python scripts/llamaparse/batch_llamaparse.py                 # 全部未快取的掃描/亂碼文件
python scripts/llamaparse/batch_llamaparse.py --ids 65 470    # 指定 doc ID
```

### retry_llamaparse_pages.py
重試 LlamaParse 失敗的特定頁面。

```bash
python scripts/llamaparse/retry_llamaparse_pages.py --doc-id 65
```

### scan_llamaparse_gaps.py
掃描「LlamaParse 回傳空白但 pymupdf4llm 有文字」的文件，找出靜默失敗案例。

```bash
python scripts/llamaparse/scan_llamaparse_gaps.py
```

### debug_llamaparse.py
Debug 單份文件的 LlamaParse 解析結果。

```bash
python scripts/llamaparse/debug_llamaparse.py --doc-id 65
```

---

## debug/ — Debug 與分析

### debug_chunks.py
預覽 chunking 結果，不寫入 Qdrant。

```bash
python scripts/debug/debug_chunks.py                          # 互動模式（從 DB 查快取）
python scripts/debug/debug_chunks.py --file llamacache/65.md  # 指定快取檔
```

### scan_quality.py
掃描 PostgreSQL 中所有文件，更新 quality_issue 欄位。

```bash
python scripts/debug/scan_quality.py
python scripts/debug/scan_quality.py --dry-run                # 只印結果，不寫 DB
python scripts/debug/scan_quality.py --limit 10               # 只掃前 N 筆
```

### compare_rrf.py
比較 equal-weight RRF 與 weighted RRF（dense 2 : sparse 1）的 hybrid search 結果。

```bash
python scripts/debug/compare_rrf.py --doc-ids 1 2 --queries "研究方法" "研究限制"
```
