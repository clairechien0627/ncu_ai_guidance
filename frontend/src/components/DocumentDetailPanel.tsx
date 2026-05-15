import { Suspense, lazy, useMemo } from 'react'
import { ChevronRight, Download, ExternalLink, FileText, MessageCircle, Pencil, RefreshCw } from 'lucide-react'
import type { ParserCacheInfo, SummaryItem, TraceItem } from '../api'
import type { UseInlineEditReturn } from '../hooks/useInlineEdit'
import { getCollegeInfo, getDisplayTitle, getProjectNumber, getStatusMeta, relativeTime } from '../utils/summaryUtils'

const DocChat = lazy(() => import('./chat/DocChat'))
const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'))

const API_BASE = import.meta.env.VITE_API_URL ?? ''
const CACHE_PRIORITY = ['llamaparse', 'azure_di', 'pymupdf4llm'] as const

function StatusPill({ status }: { status: SummaryItem['batch_status'] }) {
  const meta = getStatusMeta(status)
  return <span className={meta.cls}>{meta.label}</span>
}

export interface DocumentDetailCallbacks {
  onReindex: (item: SummaryItem) => void
  onParseWith: (item: SummaryItem, parser: string) => void
  onOpenCompare: (item: SummaryItem) => void
  onViewChunks: (docId: number) => void
  onExtract: (item: SummaryItem) => void
  onExtractStep: (item: SummaryItem, step: 'step1' | 'step2' | 'step3') => void
  onRefreshAbstract: (item: SummaryItem) => void
  onSaveTitle: (item: SummaryItem) => void
  onSaveAbstract: (item: SummaryItem) => void
  onExport: (item: SummaryItem) => void
  onRefreshTraces: () => void
}

interface DocumentDetailPanelProps {
  selected: SummaryItem
  titleEdit: UseInlineEditReturn
  abstractEdit: UseInlineEditReturn
  parserCaches: ParserCacheInfo[]
  parserCachesLoaded: boolean
  parserChoice: Record<number, string>
  onParserChoiceChange: (docId: number, parser: string) => void
  parsingTabs: Set<string>
  isParseQueuedOrRunning: (docId: number, parser: string) => boolean
  extracting: Set<number>
  extractingSteps: Set<string>
  reindexing: Set<number>
  docTraces: TraceItem[]
  tracesLoading: boolean
  expandedTraceId: string | null
  onExpandTrace: (id: string | null) => void
  rightView: 'pdf' | 'chat'
  onSetRightView: (v: 'pdf' | 'chat') => void
  callbacks: DocumentDetailCallbacks
}

export function DocumentDetailPanel({
  selected, titleEdit, abstractEdit,
  parserCaches, parserCachesLoaded, parserChoice, onParserChoiceChange,
  parsingTabs, isParseQueuedOrRunning,
  extracting, extractingSteps, reindexing,
  docTraces, tracesLoading, expandedTraceId, onExpandTrace,
  rightView, onSetRightView,
  callbacks,
}: DocumentDetailPanelProps) {
  const effectiveParser = useMemo<string | null>(() => {
    if (parserChoice[selected.id] != null) return parserChoice[selected.id]
    if (selected.parser_used && selected.parser_used !== 'auto') return selected.parser_used
    for (const p of CACHE_PRIORITY) {
      if (parserCaches.find(c => c.parser === p && c.available)) return p
    }
    return null
  }, [selected, parserChoice, parserCaches])

  const college = getCollegeInfo(selected.department)

  return (
    <div className="detail-scroll">
      <div className="detail-summary-col">

        {/* ── Header: title, dept, action steps ── */}
        <div className="detail-sec">
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span className={college.cls}>{college.label}</span>
              <span style={{ fontSize: 11, color: '#94a3b8' }}>#{selected.id}</span>
            </div>
            {titleEdit.editing ? (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
                <input
                  autoFocus
                  className="ncu-search"
                  style={{ flex: 1, fontSize: 14, fontWeight: 600, height: 34 }}
                  value={titleEdit.draft}
                  onChange={e => titleEdit.setDraft(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') callbacks.onSaveTitle(selected)
                    if (e.key === 'Escape') titleEdit.cancelEdit()
                  }}
                />
                <button className="nbtn nbtn-blue nbtn-sm" onClick={() => callbacks.onSaveTitle(selected)} disabled={titleEdit.saving}>
                  {titleEdit.saving ? '…' : '儲存'}
                </button>
                <button className="nbtn nbtn-sm" onClick={() => titleEdit.cancelEdit()}>取消</button>
              </div>
            ) : (
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginBottom: 6 }}>
                <h2 className="detail-title-text" style={{ flex: 1, margin: 0 }}>{getDisplayTitle(selected.filename)}</h2>
                <button
                  className="nbtn-icon"
                  style={{ marginTop: 3, flexShrink: 0 }}
                  title="重新命名"
                  onClick={() => { titleEdit.setDraft(getDisplayTitle(selected.filename)); titleEdit.setEditing(true) }}
                >
                  <Pencil size={13} />
                </button>
              </div>
            )}
            <div className="detail-doc-meta">
              <span className="detail-dept-chip">{selected.department}</span>
              {getProjectNumber(selected.filename) && (
                <span style={{ fontSize: 10, color: '#94a3b8', fontFamily: 'monospace' }}>
                  {getProjectNumber(selected.filename)}
                </span>
              )}
              <StatusPill status={selected.batch_status} />
            </div>
          </div>

          <div className="action-area">
            <span className="action-area-lbl">文件流程</span>

            {/* Step 1: Parse */}
            <div className="action-step">
              <div className="action-step-head">
                <span className="action-step-no">1</span>
                <span className="action-step-title">先解析</span>
              </div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {(['pymupdf4llm', 'azure_di', 'llamaparse'] as const).map(parser => {
                  const cache = parserCaches.find(entry => entry.parser === parser)
                  const busy = parsingTabs.has(`${selected.id}_${parser}`) || isParseQueuedOrRunning(selected.id, parser)
                  const label = parser === 'pymupdf4llm' ? 'PyMuPDF' : parser === 'azure_di' ? 'Azure DI' : 'Llama'
                  const sizeKb = cache?.available && cache.size > 0 ? `${(cache.size / 1024).toFixed(0)} KB` : null
                  return (
                    <button
                      key={parser}
                      className={`pcache-pill${!busy && cache?.available ? ' ready' : ''}`}
                      onClick={() => callbacks.onParseWith(selected, parser)}
                      disabled={busy}
                      title={cache?.available ? `${label} 已可用（${sizeKb}）` : `建立 ${label} 解析`}
                    >
                      {busy ? '處理中...' : label}
                      {!busy && sizeKb && <span className="pcache-size">{sizeKb}</span>}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Step 2: Embed */}
            <div className="action-step">
              <div className="action-step-head">
                <span className="action-step-no">2</span>
                <span className="action-step-title">選嵌入來源</span>
                {selected.status === 'ready' && selected.parser_used && (
                  <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#ecfdf5', color: '#065f46', border: '1px solid #a7f3d0', marginLeft: 6 }}>
                    ✓ {{ pymupdf4llm: 'PyMuPDF', azure_di: 'Azure DI', llamaparse: 'Llama', auto: '自動' }[selected.parser_used] ?? selected.parser_used}
                  </span>
                )}
                {selected.needs_reindex && (
                  <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999, background: '#fff7ed', color: '#9a3412', border: '1px solid #fed7aa', marginLeft: 6 }}>
                    內容已手動修改，需重新嵌入
                  </span>
                )}
              </div>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <select
                  className="ncu-select"
                  style={{ maxWidth: 180, minWidth: 150, height: 34, fontSize: 12 }}
                  value={effectiveParser ?? ''}
                  onChange={e => onParserChoiceChange(selected.id, e.target.value)}
                  disabled={reindexing.has(selected.id) || selected.status === 'processing' || (parserCachesLoaded && effectiveParser === null)}
                >
                  {effectiveParser === null && <option value="">（無快取可選）</option>}
                  <option value="auto">自動選擇</option>
                  <option value="pymupdf4llm">PyMuPDF4LLM</option>
                  <option value="azure_di">Azure DI</option>
                  <option value="llamaparse">LlamaParse</option>
                </select>
                <button className="nbtn nbtn-blue nbtn-sm" onClick={() => callbacks.onReindex(selected)} disabled={reindexing.has(selected.id) || selected.status === 'processing'}>
                  {reindexing.has(selected.id) ? '排入中...' : '嵌入'}
                </button>
                <button className="nbtn nbtn-sm" onClick={() => callbacks.onOpenCompare(selected)}>解析對比</button>
                <button className="nbtn nbtn-sm" onClick={() => callbacks.onViewChunks(selected.id)}>嵌入結果</button>
              </div>
            </div>

            {/* Step 3: Extract & Export */}
            <div className="action-step">
              <div className="action-step-head">
                <span className="action-step-no">3</span>
                <span className="action-step-title">摘要與匯出</span>
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <button className="nbtn nbtn-purple nbtn-sm" onClick={() => callbacks.onExtract(selected)} disabled={extracting.has(selected.id) || selected.batch_status === 'processing' || (selected.status !== 'ready' && selected.status !== 'error')}>
                  {extracting.has(selected.id) ? '排入中...' : '摘要'}
                </button>
                {(['step1', 'step2', 'step3'] as const).map(step => (
                  <button
                    key={step}
                    className="nbtn nbtn-sm"
                    onClick={() => callbacks.onExtractStep(selected, step)}
                    disabled={
                      extractingSteps.has(`${selected.id}_${step}`) ||
                      (step !== 'step1' && !selected.raw_research_answer) ||
                      selected.batch_status === 'processing' ||
                      (selected.status !== 'ready' && selected.status !== 'error')
                    }
                  >
                    {extractingSteps.has(`${selected.id}_${step}`) ? '排入中...' : step.toUpperCase()}
                  </button>
                ))}
                <button className="nbtn nbtn-sm" onClick={() => callbacks.onExport(selected)}>
                  <Download size={11} /> 匯出
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* ── PDF Abstract ── */}
        <div className="detail-sec">
          <div className="detail-sec-hdr">
            <span className="detail-sec-bar" />
            <span className="detail-sec-label">PDF 摘要</span>
            <div className="detail-sec-actions">
              <button className="nbtn-icon" onClick={() => callbacks.onRefreshAbstract(selected)} title="重新整理 PDF 摘要">
                <RefreshCw size={12} />
              </button>
              {!abstractEdit.editing && (
                <button className="nbtn-icon" onClick={() => abstractEdit.setEditing(true)} title="編輯摘要">
                  <Pencil size={12} />
                </button>
              )}
            </div>
          </div>
          {abstractEdit.editing ? (
            <>
              <textarea className="ncu-textarea" value={abstractEdit.draft} onChange={e => abstractEdit.setDraft(e.target.value)} />
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button className="nbtn nbtn-blue nbtn-sm" onClick={() => callbacks.onSaveAbstract(selected)} disabled={abstractEdit.saving}>
                  {abstractEdit.saving ? '儲存中...' : '儲存'}
                </button>
                <button className="nbtn nbtn-sm" onClick={() => { abstractEdit.cancelEdit(); abstractEdit.setDraft(selected.abstract_text ?? '') }}>
                  取消
                </button>
              </div>
            </>
          ) : (
            <p className="detail-sec-text">{selected.abstract_text || '尚未整理 PDF 摘要。'}</p>
          )}
        </div>

        {/* ── Step 1 Raw Research ── */}
        {selected.raw_research_answer && (
          <div className="detail-sec">
            <div className="detail-sec-hdr">
              <span className="detail-sec-bar" />
              <span className="detail-sec-label">Step 1 原始研究摘要</span>
            </div>
            <div className="detail-markdown">
              <Suspense fallback={<p className="detail-sec-text">{selected.raw_research_answer}</p>}>
                <MarkdownRenderer content={selected.raw_research_answer} />
              </Suspense>
            </div>
            {selected.raw_research_sources?.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                {selected.raw_research_sources.slice(0, 6).map(source => <span key={source} className="detail-tag">{source}</span>)}
              </div>
            )}
          </div>
        )}

        {/* ── AI Summary ── */}
        <div className="detail-sec">
          <div className="detail-sec-hdr">
            <span className="detail-sec-bar" />
            <span className="detail-sec-label">AI 摘要內容</span>
          </div>
          {selected.summary ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {(['motivation', 'method', 'results'] as const).map((key, i) => (
                <section key={key}>
                  <div className="detail-sec-label" style={{ marginBottom: 5 }}>
                    {['研究動機', '研究方法', '研究成果'][i]}
                  </div>
                  <div className="detail-markdown">
                    <Suspense fallback={<p className="detail-sec-text">{selected.summary![key]}</p>}>
                      <MarkdownRenderer content={selected.summary![key]} />
                    </Suspense>
                  </div>
                </section>
              ))}
              {selected.summary.tags?.length > 0 && (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {selected.summary.tags.map(tag => <span key={tag} className="detail-tag">{tag}</span>)}
                </div>
              )}
            </div>
          ) : (
            <p className="detail-sec-text">尚未產生 AI 摘要。</p>
          )}
        </div>

        {/* ── 一分鐘看研究 ── */}
        {selected.summary && (selected.summary.intro || (selected.summary.questions ?? []).length > 0) && (
          <div className="detail-sec">
            <div className="detail-sec-hdr">
              <span className="detail-sec-bar" />
              <span className="detail-sec-label">一分鐘看研究</span>
            </div>
            {selected.summary.intro && (
              <div className="detail-markdown" style={{ marginBottom: 14 }}>
                <Suspense fallback={<p className="detail-sec-text">{selected.summary.intro}</p>}>
                  <MarkdownRenderer content={selected.summary.intro} />
                </Suspense>
              </div>
            )}
            {(selected.summary.questions ?? []).length > 0 && (
              <>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>興趣量表</span>
                  <span style={{ fontSize: 11, color: '#94a3b8' }}>1＝完全不感興趣　5＝非常感興趣</span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {(selected.summary.questions ?? []).map((q, i) => (
                    <div key={i} style={{ padding: '9px 12px', borderRadius: 8, background: '#f8fafc', border: '1px solid #e8edf3', fontSize: 13, color: '#334155', lineHeight: 1.65 }}>
                      {q}
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        )}

        {/* ── Trace list ── */}
        <div className="detail-sec">
          <div className="detail-sec-hdr">
            <span className="detail-sec-bar" />
            <span className="detail-sec-label">追蹤紀錄</span>
            <div className="detail-sec-actions">
              <button className="nbtn-icon" title="重新整理追蹤紀錄" onClick={callbacks.onRefreshTraces}>
                <RefreshCw size={12} />
              </button>
            </div>
          </div>
          {tracesLoading ? (
            <p className="detail-sec-text">載入中...</p>
          ) : docTraces.length === 0 ? (
            <p className="detail-sec-text">尚無追蹤紀錄。</p>
          ) : (
            <div className="trace-list">
              {docTraces.map(trace => {
                const isOpen = expandedTraceId === trace.id
                const latency = trace.latency == null ? null
                  : trace.latency < 60 ? `${trace.latency.toFixed(1)}s`
                  : `${Math.floor(trace.latency / 60)}m ${(trace.latency % 60).toFixed(0)}s`
                const timeAgo = trace.start_time ? relativeTime(trace.start_time) : null
                return (
                  <div key={trace.id} className={`trace-row${isOpen ? ' open' : ''}`}>
                    <button
                      type="button"
                      className="trace-row-head"
                      onClick={() => onExpandTrace(isOpen ? null : trace.id)}
                    >
                      <span className="trace-dot" data-status={trace.status} />
                      <span className="trace-name">{trace.name}</span>
                      <span className="trace-latency" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        {timeAgo && <span>{timeAgo}</span>}
                        {latency && <span style={{ color: '#94a3b8' }}>· {latency}</span>}
                      </span>
                      {trace.url && (
                        <a href={trace.url} target="_blank" rel="noopener noreferrer" onClick={e => e.stopPropagation()} className="trace-ext-link" title="在 LangSmith 開啟">
                          <ExternalLink size={11} />
                        </a>
                      )}
                      <ChevronRight size={13} className="trace-chevron" />
                    </button>
                    {isOpen && trace.display && (
                      <div className="trace-row-body">
                        {trace.display.messages.map((msg, i) => {
                          if (msg.role === 'system') return (
                            <div key={i} className="trace-msg">
                              <span className="trace-msg-type" style={{ color: '#475569' }}>系統</span>
                              <div className="trace-msg-content">{msg.content}</div>
                            </div>
                          )
                          if (msg.role === 'human') return (
                            <div key={i} className="trace-msg">
                              <span className="trace-msg-type" style={{ color: '#2563eb' }}>問題</span>
                              <div className="trace-msg-content">{msg.content}</div>
                            </div>
                          )
                          if (msg.role === 'ai' && msg.content) return (
                            <div key={i} className="trace-msg">
                              <span className="trace-msg-type" style={{ color: '#7c3aed' }}>AI</span>
                              <div className="trace-msg-content">{msg.content}</div>
                            </div>
                          )
                          if (msg.role === 'ai_tool_call') return msg.tool_calls.map(tc => (
                            <div key={tc.call_id} className="trace-msg">
                              <span className="trace-msg-type" style={{ color: '#7c3aed' }}>AI</span>
                              <div className="trace-tool-name">{tc.tool}</div>
                              <pre className="trace-code">{JSON.stringify(tc.args, null, 2)}</pre>
                            </div>
                          ))
                          if (msg.role === 'tool') {
                            let prettyRaw = msg.raw ?? ''
                            try { prettyRaw = JSON.stringify(JSON.parse(msg.raw ?? ''), null, 2) } catch {}
                            const isSearch = msg.tool === 'search_report'
                            let chunkCount = 0
                            if (isSearch) {
                              chunkCount = msg.chunks.length
                              if (chunkCount === 0 && msg.raw) {
                                try {
                                  const p = JSON.parse(msg.raw)
                                  chunkCount = Array.isArray(p?.results) ? p.results.length : 0
                                } catch {}
                              }
                            }
                            return (
                              <div key={i} className="trace-msg">
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                  <span className="trace-msg-type" style={{ color: '#0d9488' }}>TOOL</span>
                                  {isSearch && <span className="trace-chunk-badge">{chunkCount} chunks</span>}
                                </div>
                                {prettyRaw && (
                                  <pre className="trace-code" style={{ maxHeight: 220, overflowY: 'auto', overflowX: 'hidden', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{prettyRaw}</pre>
                                )}
                              </div>
                            )
                          }
                          return null
                        })}
                        {trace.display.answer && (
                          <div className="trace-msg">
                            <span className="trace-msg-type" style={{ color: '#4f46e5' }}>回答</span>
                            <div className="trace-msg-content">{trace.display.answer}</div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── Right column: PDF / Chat ── */}
      <div className="detail-pdf-col" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ display: 'flex', gap: 4, padding: '8px 10px 6px', background: '#fff', borderBottom: '1px solid #e8edf3', flexShrink: 0 }}>
          {(['pdf', 'chat'] as const).map(v => (
            <button
              key={v}
              onClick={() => onSetRightView(v)}
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '4px 12px', borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                background: rightView === v ? '#1a56db' : 'transparent',
                color: rightView === v ? '#fff' : '#64748b',
                transition: 'background 0.15s, color 0.15s',
              }}
            >
              {v === 'pdf' ? <FileText size={12} /> : <MessageCircle size={12} />}
              {v === 'pdf' ? 'PDF' : '聊天'}
            </button>
          ))}
        </div>
        <div style={{ flex: 1, overflow: 'hidden' }}>
          {rightView === 'pdf' ? (
            <iframe src={`${API_BASE}/api/documents/${selected.id}/file`} title={selected.filename} style={{ width: '100%', height: '100%', border: 'none' }} />
          ) : (
            <Suspense fallback={<div style={{ padding: 20, color: '#94a3b8', fontSize: 13 }}>載入中…</div>}>
              <DocChat docId={selected.id} filename={selected.filename} />
            </Suspense>
          )}
        </div>
      </div>
    </div>
  )
}
