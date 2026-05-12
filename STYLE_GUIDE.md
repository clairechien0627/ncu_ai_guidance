# 設計語言守則

本專案的視覺設計以「毛玻璃 + 柔和陰影 + 大量留白」為核心。修改任何 UI 前必須先對照這份守則。

---

## 一、顏色

### 文字
| 用途 | 值 |
|---|---|
| 主標題 / 頁面標題 | `#163257` |
| 面板標題 / 強調文字 | `#10233f` |
| 主要內文 | `#334155` |
| 次要說明 | `#64748b` |
| 輔助 / meta | `#94a3b8` |
| 禁用 / 極淡 | `#c6d0dd` |

### 背景
| 用途 | 值 |
|---|---|
| 整頁背景 | 漸層：`radial-gradient(green glow) + radial-gradient(orange glow) + linear #fbfdff→#edf4ff` |
| 主要卡片 / 面板 | `rgba(255,255,255,0.84)` + `backdrop-filter: blur(18px)` |
| 次要面板（admin） | `#fff` |
| 表格 header | `#f8fafc` |
| hover / 選中 | `#edf5ff` 或 `rgba(255,255,255,0.95)` |

### 邊框
| 用途 | 值 |
|---|---|
| 主要邊框 | `rgba(207, 219, 232, 0.92)` 或 `rgba(207, 219, 232, 0.9)` |
| 次要分隔線 | `rgba(236, 241, 247, 0.95)` 或 `#e7edf5` |
| Admin 面板邊框 | `#dbe4ef` |
| Admin 表格分隔 | `#edf2f7` |

### 語意色彩
| 意義 | 值 |
|---|---|
| 成功 / 完成 | `#16a34a`（深）、`#22c55e`（亮） |
| 警告 / 進行中 | `#d97706` |
| 錯誤 | `#dc2626` |
| 取消 | `#94a3b8` |
| 主要藍 | `#2563eb` |
| 紫（AI / 特殊功能） | `#7c3aed` |
| 天空藍（工具） | `#0ea5e9` |
| 琥珀（評估） | `#d97706`（同警告） |

---

## 二、圓角

**守則：大容器大圓角，小元素小圓角。千萬不要把大容器用 8px，也不要把小按鈕用 28px。**

| 元素類型 | 值 |
|---|---|
| 頁面主容器（sidebar, chat area） | `24px`–`28px` |
| 對話氣泡、訊息卡 | `18px`–`20px` |
| Dialog / Modal | `12px` |
| 標準面板 / Admin 卡片 | `8px` |
| 按鈕（圓形 pill） | `999px` |
| 按鈕（一般） | `8px` |
| 輸入框（一般） | `8px`–`10px` |
| 搜尋輸入框 | `999px` |
| 小 badge / 狀態標籤 | `999px` |
| 篩選控制項 | `7px` |

---

## 三、字型

**守則：絕對不要讓管理工具的表格用超過 12px 的字，資料密集時用 11px。**

| 用途 | 大小 | 粗細 |
|---|---|---|
| 頁面主標題 (h1) | `22px` | `800` |
| 面板標題 | `13px`–`14px` | `800` |
| 表格欄頭 | `11px` | `800` |
| 表格內文 | `11px`–`12px` | `400` |
| 標準 UI 文字 / 按鈕 | `13px` | `700` |
| 說明文字 / meta | `11px`–`12px` | `400` |
| 最小 label / badge | `10px`–`11px` | `700`–`800` |

---

## 四、間距

**守則：寧可有留白也不要把 padding 壓縮到 4px 以下來塞更多內容。留白是設計的一部分。**

| 情境 | 值 |
|---|---|
| 頁面 padding | `18px 20px`（admin）|
| 面板內 padding | `12px 14px`（標準）、`10px 12px`（緊湊）|
| 表格 cell padding（寬版） | `9px 10px` |
| 表格 cell padding（窄版 / 管理工具） | `6px 8px` |
| 元件間 gap（大） | `14px` |
| 元件間 gap（中） | `10px`–`12px` |
| 元件間 gap（小） | `8px` |
| 行內元素 gap | `4px`–`6px` |

---

## 五、陰影

**守則：陰影是給使用者感知深度的，不是裝飾。主要互動面板一定要有陰影。**

| 用途 | 值 |
|---|---|
| 頁面主面板（sidebar, chat） | `0 18px 36px rgba(148,163,184,0.16)` |
| 卡片 hover 狀態 | `0 12px 24px rgba(148,163,184,0.12)` |
| 按鈕 hover | `0 8px 18px rgba(148,163,184,0.14)` |
| Tab active | `0 1px 3px rgba(15,23,42,0.08)` |
| Admin 面板（扁平工具）| 無陰影或 `0 1px 3px rgba(0,0,0,0.04)` |

---

## 六、毛玻璃效果

**只用在主要互動容器（sidebar、chat area、PDF panel）。Admin 工具頁面不用毛玻璃，保持清爽。**

```css
/* 標準毛玻璃面板 */
background: rgba(255, 255, 255, 0.84);
border: 1px solid rgba(207, 219, 232, 0.9);
border-radius: 24px;
backdrop-filter: blur(18px);
box-shadow: 0 14px 32px rgba(148, 163, 184, 0.18);
```

---

## 七、Transition

```css
/* 幾乎所有互動元素都用這個 */
transition: transform 0.18s ease, background 0.18s ease, color 0.18s ease,
            border-color 0.18s ease, box-shadow 0.18s ease;

/* hover lift（小幅度） */
transform: translateY(-1px);
```

---

## 八、Admin 工具頁面規範

Admin / Monitor 頁面不用毛玻璃。但要遵守以下規則：

1. **背景**：用同一套漸層背景，不用純灰 `#f6f8fb`
2. **面板**：白色背景 `#fff`，邊框 `1px solid #dbe4ef`，圓角 `8px`
3. **標題**：顏色用 `#10233f`，大小 `13px`，粗細 `800`
4. **表格**：欄頭背景 `#f8fafc`，字 `11px 800`，Cell `11px` + `6px 8px` padding
5. **按鈕**：高度 `34px`，圓角 `8px`，字 `12px 700`
6. **不要**：壓縮 padding 來塞更多欄位，寧可讓面板有個 max-height 加捲軸

---

## 九、常見錯誤

| 錯誤 | 正確做法 |
|---|---|
| 用 `overflow: hidden` 在 panel 容器 | 只在內容區塊用，不在整個 panel |
| 把 `flex: 1` 加在 panel 上讓它「填滿」 | 用 `max-height` + `overflow-y: auto` 在內容區 |
| 表格字用 12px + padding 9px 放進 280px 寬的欄 | 11px + 6px padding，表格再加 `table-layout: fixed` |
| 兩個不相關的行動面板並排縮在 2-col grid | 行動面板（批次評分、路由測試）永遠全寬 |
| `height: 100%` 加在 flex 子元件 | 用 `flex: 1; min-height: 0` |
| 移除留白來讓更多內容進畫面 | 加捲軸，保留留白 |
