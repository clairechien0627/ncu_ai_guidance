import { Suspense, lazy } from 'react'
import type { ParserCacheInfo, SummaryItem } from '../api'
import type { UseParserCompareReturn } from '../hooks/useParserCompare'
import { getDisplayTitle } from '../utils/summaryUtils'

const PdfPageViewer = lazy(() => import('./viewer/PdfPageViewer'))

const API_BASE = import.meta.env.VITE_API_URL ?? ''
const PARSER_KEYS = ['pymupdf4llm', 'azure_di', 'llamaparse'] as const
const LABELS: Record<string, string> = { pymupdf4llm: 'PyMuPDF4LLM', azure_di: 'Azure DI', llamaparse: 'LlamaParse' }

interface ParserCompareModalProps extends UseParserCompareReturn {
  selected: SummaryItem
  parserCaches: ParserCacheInfo[]
  parsingTabs: Set<string>
  isParseQueuedOrRunning: (docId: number, parser: string) => boolean
  onParseWith: (item: SummaryItem, parser: string) => void
}

export function ParserCompareModal({
  open, closeCompare, selected, parserCaches, parsingTabs,
  isParseQueuedOrRunning, onParseWith,
  activePanels, togglePanel, panelContents,
  page, handlePage, pageInputValue, pageInputEditing,
  setPageInputValue, setPageInputEditing,
}: ParserCompareModalProps) {
  if (!open) return null

  const maxPages = Math.max(0, ...Object.values(panelContents).map(c => c.pages.length))
  const panelCount = activePanels.size

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.22)', backdropFilter: 'blur(6px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 240, padding: 16 }}
      onClick={closeCompare}
    >
      <div
        style={{ width: 'min(1600px, 98vw)', height: 'min(900px, 94vh)', background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(207,219,232,0.95)', borderRadius: 28, boxShadow: '0 28px 60px rgba(148,163,184,0.24)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid rgba(226,232,240,0.92)', flexShrink: 0 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 800, color: '#163257' }}>解析對比</div>
            <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{getDisplayTitle(selected.filename)}</div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, cursor: 'pointer', userSelect: 'none', color: '#334155' }}>
              <input
                type="checkbox"
                checked={activePanels.has('pdf')}
                onChange={() => togglePanel(selected.id, 'pdf')}
                style={{ width: 14, height: 14, cursor: 'pointer' }}
              />
              PDF
            </label>
            {PARSER_KEYS.map(p => {
              const info = parserCaches.find(c => c.parser === p)
              const busy = parsingTabs.has(`${selected.id}_${p}`) || isParseQueuedOrRunning(selected.id, p)
              return (
                <label key={p} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, cursor: info?.available ? 'pointer' : 'default', userSelect: 'none', opacity: info?.available ? 1 : 0.38, color: '#334155' }}>
                  <input
                    type="checkbox"
                    checked={activePanels.has(p)}
                    disabled={!info?.available}
                    onChange={() => togglePanel(selected.id, p)}
                    style={{ width: 14, height: 14, cursor: info?.available ? 'pointer' : 'default' }}
                  />
                  {LABELS[p]}
                  {info?.available && <span style={{ color: '#94a3b8', fontSize: 10, fontWeight: 400 }}>({(info.size / 1024).toFixed(0)}KB)</span>}
                  <button
                    onClick={e => { e.preventDefault(); !busy && onParseWith(selected, p) }}
                    disabled={busy}
                    title={info?.available ? '重新解析' : '建立解析'}
                    style={{ padding: '2px 7px', fontSize: 10, fontWeight: 700, border: '1px solid #d1d5db', borderRadius: 999, background: '#f9fafb', cursor: busy ? 'not-allowed' : 'pointer', color: '#64748b' }}
                  >
                    {busy ? '…' : info?.available ? '↺' : '解析'}
                  </button>
                </label>
              )
            })}
            <button className="nbtn nbtn-sm" style={{ marginLeft: 8 }} onClick={closeCompare}>關閉</button>
          </div>
        </div>

        {/* Page navigation */}
        {maxPages > 1 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 20px', borderBottom: '1px solid rgba(226,232,240,0.92)', flexShrink: 0, background: 'rgba(248,250,252,0.9)' }}>
            <button
              disabled={page === 0}
              onClick={() => handlePage(page - 1)}
              style={{ padding: '3px 10px', fontSize: 15, border: '1px solid #d1d5db', borderRadius: 999, background: '#fff', cursor: page === 0 ? 'not-allowed' : 'pointer', color: '#374151', opacity: page === 0 ? 0.4 : 1 }}
            >‹</button>
            <input
              type="range"
              min={0} max={maxPages - 1} value={page}
              onChange={e => handlePage(Number(e.target.value))}
              style={{ flex: 1, cursor: 'pointer', accentColor: '#2563eb' }}
            />
            <button
              disabled={page >= maxPages - 1}
              onClick={() => handlePage(page + 1)}
              style={{ padding: '3px 10px', fontSize: 15, border: '1px solid #d1d5db', borderRadius: 999, background: '#fff', cursor: page >= maxPages - 1 ? 'not-allowed' : 'pointer', color: '#374151', opacity: page >= maxPages - 1 ? 0.4 : 1 }}
            >›</button>
            <span style={{ fontSize: 12, color: '#64748b' }}>第</span>
            {pageInputEditing ? (
              <input
                type="text" inputMode="numeric" autoFocus
                value={pageInputValue}
                onChange={e => setPageInputValue(e.target.value.replace(/\D/g, ''))}
                onBlur={() => {
                  const n = Math.max(1, Math.min(maxPages, parseInt(pageInputValue) || 1))
                  handlePage(n - 1)
                  setPageInputEditing(false)
                }}
                onKeyDown={e => {
                  if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                  if (e.key === 'Escape') { setPageInputEditing(false); setPageInputValue(String(page + 1)) }
                }}
                style={{ width: 48, fontSize: 13, fontWeight: 700, textAlign: 'center', padding: '3px 6px', border: '1px solid #93c5fd', borderRadius: 8, outline: 'none', color: '#1e3a8a' }}
              />
            ) : (
              <span
                onClick={() => { setPageInputEditing(true); setPageInputValue(String(page + 1)) }}
                title="點擊輸入頁碼"
                style={{ fontSize: 13, fontWeight: 700, color: '#1e3a8a', minWidth: 32, textAlign: 'center', cursor: 'text', borderBottom: '2px dashed #93c5fd', paddingBottom: 1 }}
              >
                {page + 1}
              </span>
            )}
            <span style={{ fontSize: 12, color: '#64748b' }}>/ {maxPages} 頁</span>
          </div>
        )}

        {/* Panels */}
        <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
          {panelCount === 0 && (
            <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 13 }}>
              請在上方勾選要顯示的面板
            </div>
          )}
          {activePanels.has('pdf') && (
            <div style={{ flex: `0 0 ${Math.floor(100 / panelCount)}%`, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, borderRight: '1px solid rgba(226,232,240,0.92)' }}>
              <div style={{ padding: '8px 14px', fontSize: 10.5, fontWeight: 800, color: '#374151', background: 'rgba(248,250,252,0.92)', borderBottom: '1px solid rgba(226,232,240,0.92)', textTransform: 'uppercase', letterSpacing: '0.06em', flexShrink: 0 }}>
                PDF 原檔
              </div>
              <Suspense fallback={<div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 13 }}>載入中...</div>}>
                <PdfPageViewer
                  url={`${API_BASE}/api/documents/${selected.id}/file`}
                  page={page + 1}
                  onPageCount={() => { if (maxPages === 0) handlePage(0) }}
                />
              </Suspense>
            </div>
          )}
          {PARSER_KEYS.filter(p => activePanels.has(p)).map((p, idx) => {
            const content = panelContents[p]
            return (
              <div key={p} style={{ flex: `0 0 ${Math.floor(100 / panelCount)}%`, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, borderLeft: idx > 0 || activePanels.has('pdf') ? '1px solid rgba(226,232,240,0.92)' : 'none' }}>
                <div style={{ padding: '8px 14px', fontSize: 10.5, fontWeight: 800, color: '#374151', background: 'rgba(248,250,252,0.92)', borderBottom: '1px solid rgba(226,232,240,0.92)', textTransform: 'uppercase', letterSpacing: '0.06em', flexShrink: 0 }}>
                  {LABELS[p]}
                </div>
                <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px', minHeight: 0, background: '#fff' }}>
                  {!content || content.loading ? (
                    <span style={{ color: '#94a3b8', fontSize: 13 }}>載入中...</span>
                  ) : (
                    <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.75, color: '#1e293b', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace' }}>
                      {content.pages[page] ?? '（本頁無內容）'}
                    </pre>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
