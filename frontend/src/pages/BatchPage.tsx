import { useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, X } from 'lucide-react'
import type { SummaryItem } from '../api'
import { batchExtractSelected, batchReparse } from '../api'

interface Props {
  items: SummaryItem[]
  onClose: () => void
  onJobsChanged: () => void
}

function getDisplayTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

const PARSER_LABEL: Record<string, string> = {
  pymupdf4llm: 'PyMuPDF',
  azure_di: 'Azure DI',
  llamaparse: 'Llama',
  auto: '自動',
}

const CACHE_KEYS = ['pymupdf4llm', 'azure_di', 'llamaparse'] as const
type CacheKey = (typeof CACHE_KEYS)[number]

const CACHE_COLORS: Record<CacheKey, { on: string; onText: string }> = {
  pymupdf4llm: { on: '#059669', onText: '#ecfdf5' },
  azure_di:    { on: '#2563eb', onText: '#eff6ff' },
  llamaparse:  { on: '#c2410c', onText: '#fff7ed' },
}

export default function BatchPage({ items, onClose, onJobsChanged }: Props) {
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const [skipExisting, setSkipExisting] = useState(true)
  const [embedParser, setEmbedParser] = useState('auto')
  const [msg, setMsg] = useState<string | null>(null)
  const [msgIsError, setMsgIsError] = useState(false)

  const allSelected = selectedIds.size === items.length && items.length > 0
  const someSelected = selectedIds.size > 0

  // Target: selected rows, or all if nothing selected
  const targetItems = useMemo(
    () => (someSelected ? items.filter(i => selectedIds.has(i.id)) : items),
    [items, selectedIds, someSelected],
  )

  const flash = (text: string, isError = false) => {
    setMsg(text)
    setMsgIsError(isError)
    window.setTimeout(() => setMsg(null), 5000)
  }

  const toggleAll = () =>
    setSelectedIds(allSelected ? new Set() : new Set(items.map(i => i.id)))

  const toggle = (id: number) =>
    setSelectedIds(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  // IDs that need parsing for a given parser (respects skipExisting toggle)
  const parseIds = (parser: CacheKey) => {
    const base = targetItems
    return skipExisting
      ? base.filter(i => !i.caches[parser]).map(i => i.id)
      : base.map(i => i.id)
  }

  // Count label: "N 缺" or "已全有"
  const parseLabel = (parser: CacheKey) => {
    const missing = targetItems.filter(i => !i.caches[parser]).length
    if (!skipExisting) return `${targetItems.length} 筆`
    if (missing === 0) return '已全有'
    return `${missing} 缺`
  }

  const handleReparse = async (parser: CacheKey) => {
    const ids = parseIds(parser)
    if (ids.length === 0) {
      flash(`所有目標文件都已有 ${PARSER_LABEL[parser]} 快取，無需重新解析。`)
      return
    }
    try {
      const result = await batchReparse(parser, ids)
      flash(
        result.queued > 0
          ? `已加入 ${result.queued} 筆 ${PARSER_LABEL[parser]} 解析工作。`
          : '目前沒有可加入佇列的文件。',
      )
      if (result.queued > 0) onJobsChanged()
    } catch (error: any) {
      flash(`解析失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`, true)
    }
  }

  const handleEmbed = async () => {
    const ids = targetItems.map(i => i.id)
    try {
      const result = await batchReparse(embedParser, ids)
      flash(
        result.queued > 0
          ? `已加入 ${result.queued} 筆嵌入工作（${PARSER_LABEL[embedParser] ?? embedParser}）。`
          : '目前沒有可加入佇列的文件。',
      )
      if (result.queued > 0) onJobsChanged()
    } catch (error: any) {
      flash(`嵌入失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`, true)
    }
  }

  const handleExtract = async () => {
    const ids = targetItems
      .filter(i => i.status === 'ready')
      .map(i => i.id)
    if (ids.length === 0) {
      flash('沒有已嵌入且可進行摘要的文件。')
      return
    }
    try {
      const result = await batchExtractSelected(ids, true)
      flash(
        result.queued > 0
          ? `已加入 ${result.queued} 筆摘要工作。`
          : '目前沒有可加入佇列的文件（可能已全部完成）。',
      )
      if (result.queued > 0) onJobsChanged()
    } catch (error: any) {
      flash(`摘要失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`, true)
    }
  }

  return (
    <div
      className="batch-overlay"
      style={{ position: 'fixed', inset: 0, zIndex: 300, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20, background: 'rgba(15,23,42,0.22)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
    >
      <div
        className="batch-dialog"
        style={{ position: 'relative', width: 'min(1200px, 96vw)', height: 'min(880px, 93vh)', display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRadius: 28, border: '1px solid rgba(207,219,232,0.92)', background: 'rgba(255,255,255,0.97)', boxShadow: '0 28px 60px rgba(148,163,184,0.24)' }}
        onClick={e => e.stopPropagation()}
      >

        {/* ── Header ── */}
        <div className="batch-header" style={{ padding: '20px 24px 16px', borderBottom: '1px solid rgba(226,232,240,0.92)', background: 'linear-gradient(180deg,rgba(255,255,255,0.98),rgba(246,250,255,0.94))', flexShrink: 0 }}>

          {/* Title row */}
          <div className="batch-title-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 18 }}>
            <div>
              <div style={{ fontSize: 20, fontWeight: 800, color: '#163257', marginBottom: 4 }}>批次操作</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 13, color: '#64748b' }}>
                  選取文件後，依序執行解析 → 嵌入 → AI 摘要三個步驟。
                </span>
                <span style={{ fontSize: 12, color: '#475569', background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 999, padding: '2px 10px', fontWeight: 700, whiteSpace: 'nowrap' }}>
                  已嵌入 {items.filter(i => i.status === 'ready' && i.parser_used && i.parser_used !== 'auto').length}
                  <span style={{ fontWeight: 400, color: '#93c5fd' }}> / {items.length}</span>
                </span>
              </div>
            </div>
            <button
              onClick={onClose}
              title="關閉"
              aria-label="關閉批次操作"
              style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 38, height: 38, padding: 0, borderRadius: 999, border: '1px solid #d7deea', background: '#fff', color: '#475569', fontSize: 13, fontWeight: 700, cursor: 'pointer', flexShrink: 0 }}
            >
              <X size={16} />
            </button>
          </div>

          {/* Action rows */}
          <div className="batch-action-rows" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>

            {/* Select + skip toggle */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8, height: 38, padding: '0 14px', borderRadius: 999, border: '1px solid rgba(207,219,232,0.95)', background: '#fff', color: '#163257', fontSize: 13, fontWeight: 700, cursor: 'pointer', userSelect: 'none' }}>
                <input type="checkbox" checked={allSelected} onChange={toggleAll} style={{ width: 15, height: 15, cursor: 'pointer' }} />
                {someSelected ? `已選 ${selectedIds.size} / ${items.length} 筆` : `全選（${items.length} 筆）`}
              </label>

              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 7, height: 38, padding: '0 14px', borderRadius: 999, border: `1px solid ${skipExisting ? '#a7f3d0' : '#d7deea'}`, background: skipExisting ? '#ecfdf5' : '#f9fafb', color: skipExisting ? '#065f46' : '#64748b', fontSize: 12, fontWeight: 700, cursor: 'pointer', userSelect: 'none', transition: 'all 0.18s' }}>
                <input type="checkbox" checked={skipExisting} onChange={e => setSkipExisting(e.target.checked)} style={{ width: 14, height: 14, cursor: 'pointer' }} />
                跳過已有快取
              </label>

              {someSelected && (
                <span style={{ fontSize: 11, color: '#94a3b8', marginLeft: 4 }}>
                  （目標 {targetItems.length} 筆）
                </span>
              )}
            </div>

            {/* Step 1: Parse */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: '0.07em', textTransform: 'uppercase', minWidth: 54 }}>解析</span>
              {CACHE_KEYS.map(parser => {
                const ids = parseIds(parser)
                const label = parseLabel(parser)
                const allDone = ids.length === 0 && skipExisting
                return (
                  <button
                    key={parser}
                    onClick={() => handleReparse(parser)}
                    disabled={allDone}
                    style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      height: 36, padding: '0 14px', borderRadius: 999,
                      border: `1px solid ${allDone ? '#e5e7eb' : '#d1d5db'}`,
                      background: allDone ? '#f9fafb' : '#fff',
                      color: allDone ? '#9ca3af' : '#374151',
                      fontSize: 12, fontWeight: 700, cursor: allDone ? 'default' : 'pointer',
                      boxShadow: allDone ? 'none' : '0 4px 10px rgba(148,163,184,0.08)',
                    }}
                  >
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: CACHE_COLORS[parser].on, flexShrink: 0 }} />
                    {PARSER_LABEL[parser]}
                    <span style={{ fontWeight: 400, color: allDone ? '#9ca3af' : '#6b7280', fontSize: 11, marginLeft: 1 }}>({label})</span>
                  </button>
                )
              })}
            </div>

            {/* Step 2: Embed */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: '0.07em', textTransform: 'uppercase', minWidth: 54 }}>嵌入</span>
              <select
                value={embedParser}
                onChange={e => setEmbedParser(e.target.value)}
                style={{ height: 36, padding: '0 12px', borderRadius: 999, border: '1px solid #d1d5db', background: '#fff', color: '#374151', fontSize: 12, cursor: 'pointer' }}
              >
                <option value="auto">自動選擇</option>
                <option value="pymupdf4llm">PyMuPDF4LLM</option>
                <option value="azure_di">Azure DI</option>
                <option value="llamaparse">LlamaParse</option>
              </select>
              <button
                onClick={handleEmbed}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 36, padding: '0 16px', borderRadius: 999, border: '1px solid #bfdbfe', background: '#eff6ff', color: '#1d4ed8', fontSize: 12, fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 10px rgba(59,130,246,0.1)' }}
              >
                嵌入 {targetItems.length} 筆
              </button>
            </div>

            {/* Step 3: Extract */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: '#94a3b8', letterSpacing: '0.07em', textTransform: 'uppercase', minWidth: 54 }}>摘要</span>
              <button
                onClick={handleExtract}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 36, padding: '0 16px', borderRadius: 999, border: '1px solid #ddd6fe', background: '#f5f3ff', color: '#6d28d9', fontSize: 12, fontWeight: 700, cursor: 'pointer', boxShadow: '0 4px 10px rgba(109,40,217,0.08)' }}
              >
                AI 摘要 {targetItems.filter(i => i.status === 'ready').length} 筆
              </button>
              <span style={{ fontSize: 11, color: '#94a3b8' }}>（僅已嵌入的文件）</span>
            </div>
          </div>
        </div>

        {/* ── Toast ── */}
        {msg && (
          <div style={{
            position: 'absolute', bottom: 24, left: '50%', transform: 'translateX(-50%)',
            background: msgIsError ? '#fef2f2' : '#f0f9ff',
            border: `1px solid ${msgIsError ? '#fecaca' : '#bae6fd'}`,
            color: msgIsError ? '#b91c1c' : '#0369a1',
            borderRadius: 999, padding: '10px 20px', fontSize: 13, fontWeight: 700,
            boxShadow: '0 8px 24px rgba(0,0,0,0.12)', zIndex: 10, whiteSpace: 'nowrap',
            pointerEvents: 'none',
          }}>
            {msg}
          </div>
        )}

        {/* ── Document list ── */}
        <div className="batch-list" style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', background: 'linear-gradient(180deg, rgba(250,252,255,0.97), rgba(244,248,253,0.94))' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {items.map(item => {
              const checked = selectedIds.has(item.id)
              const parserUsed = item.parser_used && item.parser_used !== 'auto'
                ? item.parser_used
                : null

              return (
                <button
                  key={item.id}
                  type="button"
                  className="batch-row"
                  onClick={() => toggle(item.id)}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '36px minmax(0,1fr) 280px',
                    gap: 14,
                    alignItems: 'center',
                    width: '100%',
                    padding: '14px 18px',
                    borderRadius: 20,
                    border: checked ? '1px solid rgba(169,199,255,0.98)' : '1px solid rgba(226,232,240,0.92)',
                    background: checked
                      ? 'linear-gradient(180deg,rgba(255,255,255,0.98),rgba(238,244,255,0.95))'
                      : 'rgba(255,255,255,0.88)',
                    boxShadow: checked ? '0 12px 28px rgba(117,144,184,0.12)' : '0 4px 14px rgba(148,163,184,0.06)',
                    cursor: 'pointer',
                    textAlign: 'left',
                    transition: 'all 0.18s ease',
                  }}
                >
                  {/* Checkbox */}
                  <div style={{ display: 'grid', placeItems: 'center' }}>
                    <div style={{
                      width: 22, height: 22, borderRadius: 999, flexShrink: 0,
                      border: checked ? 'none' : '1.5px solid #cbd5e1',
                      background: checked ? '#2563eb' : '#fff',
                      display: 'grid', placeItems: 'center',
                    }}>
                      {checked && <CheckCircle2 size={14} color="#fff" />}
                    </div>
                  </div>

                  {/* Title + dept */}
                  <div style={{ minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 5, flexWrap: 'wrap' }}>
                      <span style={{ fontSize: 14.5, fontWeight: 800, color: '#163257', lineHeight: 1.45 }}>
                        {getDisplayTitle(item.filename)}
                      </span>
                      <span style={{ fontSize: 10, color: '#b0b9c5' }}>#{item.id}</span>
                      {item.quality_issue && (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 999, background: '#fff5df', color: '#a05a00' }}>
                          <AlertTriangle size={10} style={{ marginRight: 3, verticalAlign: 'middle' }} />
                          {item.quality_issue === 'garbled' ? '亂碼' : item.quality_issue === 'scanned' ? '掃描版' : '圖表為主'}
                        </span>
                      )}
                    </div>
                    <div style={{ fontSize: 12, color: '#64748b' }}>{item.department}</div>
                  </div>

                  {/* Pipeline status */}
                  <div className="batch-row-status" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>

                    {/* Cache dots */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 10, color: '#94a3b8', minWidth: 28 }}>快取</span>
                      {CACHE_KEYS.map(k => {
                        const has = item.caches[k]
                        const { on, onText } = CACHE_COLORS[k]
                        return (
                          <span
                            key={k}
                            title={`${PARSER_LABEL[k]}: ${has ? '已建立' : '尚未建立'}`}
                            style={{
                              display: 'inline-flex', alignItems: 'center',
                              padding: '2px 7px', borderRadius: 999, fontSize: 10, fontWeight: 700,
                              background: has ? on + '22' : '#f1f5f9',
                              color: has ? on : '#94a3b8',
                              border: `1px solid ${has ? on + '44' : '#e2e8f0'}`,
                            }}
                          >
                            {has ? '●' : '○'} {PARSER_LABEL[k]}
                          </span>
                        )
                      })}
                    </div>

                    {/* Embed status */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 10, color: '#94a3b8', minWidth: 28 }}>嵌入</span>
                      {item.status === 'processing' ? (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#fff5df', color: '#a05a00', border: '1px solid #fde68a' }}><Loader2 size={10} style={{ marginRight: 3, verticalAlign: 'middle', animation: 'spin 1s linear infinite' }} />進行中</span>
                      ) : item.status === 'error' ? (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#fee2e2', color: '#b91c1c', border: '1px solid #fecaca' }}>✗ 失敗</span>
                      ) : item.status === 'ready' ? (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#ecfdf5', color: '#065f46', border: '1px solid #a7f3d0', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                          ✓ 完成
                          {parserUsed && (
                            <span style={{ fontWeight: 400, color: '#047857' }}>· {PARSER_LABEL[parserUsed] ?? parserUsed}</span>
                          )}
                        </span>
                      ) : (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#f1f5f9', color: '#94a3b8', border: '1px solid #e2e8f0' }}>○ 未嵌入</span>
                      )}
                    </div>

                    {/* Summary status */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ fontSize: 10, color: '#94a3b8', minWidth: 28 }}>摘要</span>
                      {item.batch_status === 'processing' ? (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#fff5df', color: '#a05a00', border: '1px solid #fde68a' }}><Loader2 size={10} style={{ marginRight: 3, verticalAlign: 'middle', animation: 'spin 1s linear infinite' }} />進行中</span>
                      ) : item.batch_status === 'error' ? (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#fee2e2', color: '#b91c1c', border: '1px solid #fecaca' }}>✗ 失敗</span>
                      ) : item.batch_status === 'summarized' ? (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#f0fdfa', color: '#134e4a', border: '1px solid #99f6e4' }}>✓ 完成</span>
                      ) : (
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#f1f5f9', color: '#94a3b8', border: '1px solid #e2e8f0' }}>○ 未開始</span>
                      )}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
