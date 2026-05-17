# 設計語言守則

核心美學：**毛玻璃 + 柔和陰影 + 大量留白**。修改任何 UI 前必須對照本守則。

---

## 一、背景系統

### 頁面底層背景（全域）

```css
background:
  radial-gradient(circle at 12% 16%, rgba(201, 240, 229, 0.88) 0%, rgba(201, 240, 229, 0) 26%),
  radial-gradient(circle at 86% 16%, rgba(255, 236, 200, 0.82) 0%, rgba(255, 236, 200, 0) 24%),
  linear-gradient(180deg, #fbfdff 0%, #edf4ff 100%);
```

左上角柔和薄荷綠光暈 + 右上角暖橘光暈，底層是極淡的藍白漸層。**不要用純白或純灰取代這個背景。**

### 毛玻璃面板（標準）

```css
/* 主要互動容器：sidebar、chat area */
background: rgba(255, 255, 255, 0.84);
border: 1px solid rgba(207, 219, 232, 0.92);
border-radius: 24px;
backdrop-filter: blur(18px);
box-shadow: 0 14px 32px rgba(148, 163, 184, 0.18);
```

### 毛玻璃面板（Admin / 次要）

```css
/* admin 面板、trace 卡片 */
background: rgba(255, 255, 255, 0.82);
border: 1px solid rgba(207, 219, 232, 0.9);
border-radius: 18px;
backdrop-filter: blur(16px);
box-shadow: 0 12px 26px rgba(148, 163, 184, 0.12);
```

---

## 二、顏色

### 文字色階

| 用途 | 值 |
|------|-----|
| 主標題 / 品牌名稱 | `#163257` |
| 面板標題 / 強調 | `#10233f` |
| 主要內文 | `#334155` |
| 次要說明 | `#64748b` |
| 輔助 / meta | `#94a3b8` |
| 禁用 / 極淡 | `#c6d0dd` |

### 語意色彩

| 意義 | 值 |
|------|-----|
| 主要藍 / CTA | `#2563eb` |
| 成功 / 完成 | `#16a34a`（深）、`#22c55e`（亮） |
| 警告 / 進行中 | `#d97706` |
| 錯誤 | `#dc2626` |
| 取消 / 中性 | `#94a3b8` |
| AI / 研究功能 | `#7c3aed`（紫） |
| 工具 / 系統 | `#0ea5e9`（天空藍） |
| 評估 | `#d97706`（琥珀，同警告） |

### 背景 / 表面

| 用途 | 值 |
|------|-----|
| 主要卡片 | `rgba(255,255,255,0.84)` |
| 次要卡片 | `rgba(255,255,255,0.82)` |
| 頂欄 / 淡透明層 | `rgba(255,255,255,0.55)` |
| 表格 header | `#f8fafc` |
| hover / 選中行 | `#edf5ff` |
| active 狀態 | `rgba(255,255,255,0.95)` |

### 邊框

| 用途 | 值 |
|------|-----|
| 主要邊框 | `rgba(207, 219, 232, 0.92)` |
| 次要分隔線 | `rgba(236, 241, 247, 0.95)` |
| Admin / trace | `rgba(207, 219, 232, 0.9)` |
| 表格分隔 | `#edf2f7` |

---

## 三、圓角

**大容器大圓角，小元素小圓角。**

| 元素類型 | 值 |
|----------|-----|
| 頁面主容器（sidebar、chat area） | `24px`–`28px` |
| Admin / trace 卡片 | `18px`–`24px` |
| 對話氣泡 / 訊息卡 | `18px`–`20px` |
| Dialog / Modal | `12px` |
| 標準面板 / 表格框 | `8px` |
| 主要按鈕（圓形膠囊） | `999px` |
| 一般按鈕 | `8px`–`12px` |
| 輸入框（一般） | `8px`–`10px` |
| 搜尋輸入框 | `999px` |
| Badge / 狀態標籤 | `999px` |
| 篩選控制項 | `7px` |

---

## 四、字型

**表格和管理工具使用 11px；對話介面使用 13px–15px。不要讓資料表格字超過 12px。**

| 用途 | 大小 | 粗細 |
|------|------|------|
| 頁面主標題 (h1) | `22px` | `800` |
| 面板標題 / section 標題 | `13px`–`14px` | `700`–`800` |
| 表格欄頭 | `11px` | `800` |
| 表格內文 | `11px`–`12px` | `400` |
| 標準 UI 文字 / 按鈕 | `13px` | `600`–`700` |
| 說明 / meta | `11px`–`12px` | `400` |
| Badge / label | `10px`–`11px` | `700`–`800` |
| 訊息內文（chat） | `14px`–`15px` | `400` |
| 行高（內文） | `1.6`–`1.7` | — |

---

## 五、間距

**寧可留白也不要壓縮 padding 到 4px 以下。留白是設計的一部分。**

| 情境 | 值 |
|------|-----|
| 頁面 padding（admin） | `18px 20px` |
| 面板內 padding（標準） | `12px 14px` |
| 面板內 padding（緊湊） | `10px 12px` |
| 表格 cell（寬版） | `9px 10px` |
| 表格 cell（管理工具） | `6px 8px` |
| 元件間 gap（大） | `14px` |
| 元件間 gap（中） | `10px`–`12px` |
| 元件間 gap（小） | `8px` |
| 行內元素 gap | `4px`–`6px` |

---

## 六、陰影

**陰影表達深度，不是裝飾。主要互動面板必須有陰影。**

| 用途 | 值 |
|------|-----|
| 頁面主面板（sidebar、chat） | `0 14px 32px rgba(148,163,184,0.18)` |
| Chat area | `0 18px 36px rgba(148,163,184,0.16)` |
| Admin / Trace 詳情面板 | `0 12px 26px rgba(148,163,184,0.12)` |
| 卡片 hover | `0 12px 24px rgba(148,163,184,0.12)` |
| 按鈕 hover | `0 8px 18px rgba(148,163,184,0.14)` |
| Tab active | `0 1px 3px rgba(15,23,42,0.08)` |

---

## 七、Transition

```css
/* 幾乎所有互動元素 */
transition: transform 0.18s ease, background 0.18s ease, color 0.18s ease,
            border-color 0.18s ease, box-shadow 0.18s ease;

/* hover 微浮 */
transform: translateY(-1px);
```

時間統一用 `0.18s`；複雜展開用 `0.2s`；禁止超過 `0.3s`。

---

## 八、互動狀態

| 狀態 | 規格 |
|------|------|
| **hover（可點擊元素）** | `background` 往亮 5–10%，或 `translateY(-1px)` + shadow 加深 |
| **active / pressed** | `translateY(0)` + shadow 縮小；背景略深 |
| **focus（鍵盤）** | `outline: 2px solid #2563eb; outline-offset: 2px`，不能移除 |
| **selected / active tab** | 背景 `#edf5ff`，文字 `#2563eb`，有底線或左側指示條 |
| **disabled** | `opacity: 0.5; cursor: not-allowed; pointer-events: none` |
| **loading（按鈕）** | 顯示 spinner，同時 disabled |

---

## 九、按鈕系統

### 尺寸

| 層級 | 高度 | padding | 字級 |
|------|------|---------|------|
| 頁首主操作 | `42px` | `12px 20px` | `14px 700` |
| 標準主要 | `40px` | `10px 18px` | `13px 700` |
| 工具列 / 次要 | `36px` | `8px 14px` | `13px 600` |
| 小型 / 行內 | `28px`–`32px` | `6px 10px` | `12px 600` |
| 圖示按鈕（icon-only） | `36px × 36px` | — | — |

### 色彩變體

```css
/* Primary（主要藍，最常用 CTA） */
background: linear-gradient(180deg, #3c76ed, #2563eb);
color: #fff;
/* hover */ background: linear-gradient(180deg, #264b8b, #19376b);

/* Secondary（白底） */
background: rgba(255,255,255,0.84);
border: 1px solid rgba(207,219,232,0.9);
color: #334155;
/* hover */ background: #f1f5ff;

/* Danger（刪除/取消） */
background: #dc2626;
color: #fff;
/* hover */ background: #b91c1c;

/* Ghost（超低調） */
background: transparent;
color: #64748b;
/* hover */ background: rgba(148,163,184,0.1); color: #334155;
```

### 圓角

- 膠囊型（推薦）：`border-radius: 999px`
- 方形主按鈕：`border-radius: 12px`–`14px`
- 工具列小按鈕：`border-radius: 8px`

---

## 十、表單元件

```css
/* 輸入框 */
input, textarea, select {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(207, 219, 232, 0.92);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  color: #334155;
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}

/* focus */
input:focus {
  border-color: rgba(37, 99, 235, 0.5);
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.08);
  outline: none;
}

/* 搜尋框 */
border-radius: 999px;
padding: 8px 14px 8px 36px; /* 留給 icon */
```

---

## 十一、Z-index 層級

| 層級 | 值 | 用途 |
|------|----|------|
| 基礎內容 | `0` | 一般卡片、表格 |
| 浮動元件 | `10` | dropdown、tooltip |
| sticky header | `20` | 頁面頂欄 |
| 側欄 overlay | `40` | mobile 側欄遮罩 |
| Modal scrim | `100` | 對話框遮罩層 |
| Modal / Dialog | `110` | 對話框本身 |
| Toast / Notification | `200` | 全域提示 |

---

## 十二、Scrollbar

```css
scrollbar-width: thin;
scrollbar-color: #c6d0dd transparent;

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb {
  background: #c6d0dd;
  border-radius: 999px;
}
::-webkit-scrollbar-track { background: transparent; }
```

---

## 十三、Admin 頁面規範

Admin 使用「較淡毛玻璃」版本，不完全扁平：

1. **背景**：用同一套漸層，不用純灰 `#f6f8fb`
2. **面板**：`rgba(255,255,255,0.82)` + `blur(16px)` + 邊框 `rgba(207,219,232,0.9)` + 圓角 `18px`
3. **標題**：`#10233f`，`13px`–`14px 800`
4. **表格**：欄頭 `#f8fafc`，字 `11px 800`，cell `6px 8px`
5. **按鈕**：工具列 `36px`，主要 `40px`，頁首 `42px`
6. **不要**：壓縮 padding 塞更多欄位，改加捲軸

---

## 十四、常見錯誤

| 錯誤 | 正確做法 |
|------|---------|
| `overflow: hidden` 加在 panel 導致內容裁切 | 只加在外層框體；滾動區要 `min-height: 0` + `overflow-y: auto` |
| `height: 100%` 加在 flex 子元件 | 用 `flex: 1; min-height: 0` |
| `flex: 1` 加在 panel 讓它填滿 | 用 `max-height` + `overflow-y: auto` |
| 表格字用 12px + padding 9px 塞進 280px 欄 | `11px` + `6px padding` + `table-layout: fixed` |
| 兩個行動面板並排縮在 2-col grid | 行動面板永遠全寬 |
| 移除留白來讓更多內容進畫面 | 加捲軸，保留留白 |
| focus ring 被移除 | 永遠保留 `outline`，只換樣式不能移除 |
| disabled 元素沒有視覺反饋 | `opacity: 0.5 + cursor: not-allowed` |
| 背景用純白 `#fff` 替換毛玻璃 | 保留 `rgba(255,255,255,0.82~0.84)` + `backdrop-filter` |
