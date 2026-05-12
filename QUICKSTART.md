# QUICKSTART

本文件給「第一次接手專案」的人，目標是：

1. 能在本機把服務跑起來（frontend + backend + PostgreSQL + Qdrant）
2. 能選擇「重建資料」或「直接使用已向量化資料庫」

---

## 1. 前置需求

- Windows + PowerShell
- Docker Desktop（需可執行 `docker compose`）
- Python 3.11
- Node.js 18+（前端）

---

## 2. 取得程式碼與環境檔

```powershell
cd D:\try
copy .\backend\.env.example .\backend\.env
```

編輯 `D:\try\backend\.env`，至少填好：

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_CHAT_DEPLOYMENT`
- `AZURE_MINI_DEPLOYMENT`
- `AZURE_EMBEDDING_DEPLOYMENT`

通常本機可直接沿用：

- `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/reportdb`
- `QDRANT_URL=http://localhost:6333`

---

## 3. 啟動基礎服務（PostgreSQL / Qdrant）

```powershell
cd D:\try
docker compose up -d
```

> `docker-compose.yml` 會啟動：
> - PostgreSQL: `localhost:5432`
> - Qdrant: `localhost:6333`
> - Phoenix（可觀測）: `localhost:6006`

---

## 4. Backend 啟動

```powershell
cd D:\try\backend
python -m venv .venv
.\.venv\Scripts\pip install -r .\requirements.txt
.\.venv\Scripts\python.exe .\run_dev_server.py
```

Backend 啟動後：

- API: `http://127.0.0.1:8200`
- Swagger: `http://127.0.0.1:8200/docs`

---

## 5. Frontend 啟動

```powershell
cd D:\try\frontend
npm install
npm run dev
```

Frontend：`http://localhost:5173`

---

## 6. 兩種資料模式（重點）

## A) 全新建置（沒有現成向量資料）

流程：

1. 上傳 PDF
2. 由 backend 自動解析、chunk、embedding
3. 寫入 Qdrant（collection: `reports`）

適合：第一次部署、或不需要舊資料。

---

## B) 使用「已向量化」資料（你目前需求）

你要一起帶兩份資料：

1. **Qdrant 資料**（向量）
2. **PostgreSQL 資料**（文件/對話/trace 等 metadata）

只帶 Qdrant 不帶 PostgreSQL，通常會缺文件對應資訊。

---

## 7. 建議的交付格式（給同事）

建立一個 `data-package`：

```text
data-package/
  qdrant/
    reports-YYYYMMDD.snapshot
  postgres/
    reportdb.dump
  manifest.txt
```

`manifest.txt` 建議記：

- 匯出時間
- Qdrant 版本
- PostgreSQL 版本
- collection 名稱（例：`reports`）
- 專案 commit hash

---

## 8. 還原「已向量化資料」建議流程

1. 先啟動 Docker（PostgreSQL/Qdrant）
2. 還原 PostgreSQL dump 到 `reportdb`
3. 還原 Qdrant snapshot 到 `reports` collection
4. 啟動 backend + frontend

> 注意：Qdrant 與 PostgreSQL 的資料時間點要一致（同一批匯出），避免 ID 對不上。

---

## 9. 自動化資料打包與還原（推薦）

為了方便交接，我們提供了自動化腳本：

### 打包資料 (Export)
如果你在本機已有資料想交給別人：
```powershell
cd D:\try\backend
.\.venv\Scripts\python.exe .\scripts\export_data.py
```
這會在 `D:\try\data-package` 產生 Snapshot 與 Dump。請將此資料夾打包（不要推上 Git）交給對方。

### 還原資料 (Import)
接手的人拿到 `data-package` 後：
1. 確保 Docker 已啟動
2. 執行：
```powershell
cd D:\try\backend
.\.venv\Scripts\python.exe .\scripts\import_data.py
```

---

## 10. 快速檢查

- `http://localhost:6333/collections` 可看到 `reports`
- `http://127.0.0.1:8200/docs` 可開啟
- 前端頁面能正常讀到文件列表與查詢結果

---

## 10. 常見問題

- `ECONNREFUSED /api/...`：frontend 有啟動，但 backend 沒啟動
- 搜尋沒結果：Qdrant 無資料、或 `QDRANT_URL` 指到錯的主機
- 回答失敗：Azure OpenAI 設定未填完整或 deployment 名稱錯誤

