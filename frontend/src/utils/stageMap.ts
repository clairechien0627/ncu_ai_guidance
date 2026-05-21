type StageEntry = { label: string; visible: boolean }

const EXACT: Record<string, StageEntry> = {
  // 路由決定
  "直接回答中":               { label: "讓我來回答",       visible: true  },
  "搜尋文件中":               { label: "找找相關文件",      visible: true  },
  "生成導讀問題中":           { label: "準備導讀問題",      visible: true  },
  "品質評估中":               { label: "評估品質中",        visible: true  },
  // Retrieval agent
  "搜尋相關段落中":           { label: "找找相關段落",      visible: true  },
  // Chat agent
  "組織回答中":               { label: "快好了，整理一下",  visible: true  },
  // Research agent
  "分析任務：建立檢索項目":   { label: "分析問題",          visible: true  },
  "任務規劃失敗：使用預設檢索項目": { label: "改用預設方式搜尋", visible: true },
  "產生研究整理":             { label: "整理研究結果",      visible: true  },
  // Extraction pipeline
  "研究文獻中":               { label: "深入研究論文中",    visible: true  },
  "整理結構中":               { label: "整理內容架構",      visible: true  },
  "生成導讀與問題":           { label: "生成導讀題目",      visible: true  },
  "品質檢查":                 { label: "品質檢查中",        visible: true  },
  "品質未達標，重新搜尋":     { label: "重新搜尋中",        visible: true  },
  // 靜默（後台 job 看得到，chat 不顯示）
  "結構化與導讀題目生成完成": { label: "", visible: false },
  "偵測文件語言":             { label: "", visible: false },
}

const PREFIX: Array<{ prefix: string } & StageEntry> = [
  { prefix: "完成搜尋：",       label: "✓ 找到資料",   visible: true  },
  { prefix: "找不到相關資料：", label: "略過部分內容",  visible: true  },
  { prefix: "規劃查詢策略：",   label: "規劃搜尋方向",  visible: true  },
  // 以下對使用者意義不大，DocChat 靜默
  { prefix: "排程搜尋：",       label: "", visible: false },
  { prefix: "搜尋 ",            label: "", visible: false },
  { prefix: "搜尋文件：",       label: "", visible: false },
  { prefix: "任務規劃完成：",   label: "", visible: false },
  { prefix: "載入研究快取：",   label: "", visible: false },
]

/**
 * Maps a raw backend stage string to a user-friendly display label.
 * Returns null if the stage should be silently skipped in chat UI.
 * Unknown stages fall through to show the raw string (safe fallback).
 */
export function mapStage(raw: string): string | null {
  const exact = EXACT[raw]
  if (exact !== undefined) return exact.visible ? exact.label : null

  for (const p of PREFIX) {
    if (raw.startsWith(p.prefix)) return p.visible ? p.label : null
  }

  return raw
}
