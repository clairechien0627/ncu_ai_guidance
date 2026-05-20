import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Upload, Trash2, RefreshCw, ChevronRight, ExternalLink,
  Download, Pencil,
} from 'lucide-react'
import {
  batchImport, batchExtractAll, exportDocument,
  extractSummary, extractSummaryStep,
  getDocumentTraces, getJobs, getParserCaches,
  parseDocument, refreshAbstractText,
  reindexDocument, renameDocument, updateAbstractText, uploadDocument,
  deleteChunk, updateChunk,
  batchReparse, batchExtractSelected,
  type ParserCacheInfo, type SummaryItem, type TraceItem, type JobItem,
} from '../../api'
import { useDocumentList } from '../../hooks/useDocumentList'
import { TablePagination } from '../../components/admin/TablePagination'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { useInlineEdit } from '../../hooks/useInlineEdit'
import { useJobStore, selectJobs } from '../../stores/jobStore'
import { useJobSSE } from '../../hooks/useJobSSE'
import { DrawerPanel } from '../../components/admin/DrawerPanel'
import { StatusBadge } from '../../components/admin/StatusBadge'
import { TraceDetailContent } from '../../components/admin/TraceDetailContent'
import { getDisplayTitle, getProjectNumber, getStatusMeta, relativeTime } from '../../utils/summaryUtils'
import { getYear } from '../../utils/filenameParse'

const MarkdownRenderer = lazy(() => import('../../components/MarkdownRenderer'))

const API_BASE = import.meta.env.VITE_API_URL ?? ''

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

const CACHE_PRIORITY = ['llamaparse', 'azure_di', 'pymupdf4llm'] as const

const PARSER_LABEL: Record<string, string> = {
  pymupdf4llm: 'PyMuPDF', azure_di: 'Azure DI', llamaparse: 'LlamaParse', auto: '自動',
}

// ── Inline Batch Action Bar ───────────────────────────────────────────────────

const PARSE_KEYS = ['pymupdf4llm', 'azure_di', 'llamaparse'] as const

function BatchBar({
  selected, allFiltered, onClear, onDone,
}: {
  selected: Set<number>
  allFiltered: SummaryItem[]
  onClear: () => void
  onDone: (msg: string) => void
}) {
  const [skipExisting, setSkipExisting] = useState(true)
  const [embedParser,  setEmbedParser]  = useState('auto')
  const [busy,         setBusy]         = useState<string | null>(null)

  const target = selected.size > 0
    ? allFiltered.filter(i => selected.has(i.id))
    : allFiltered

  const run = async (label: string, fn: () => Promise<{ queued: number }>) => {
    setBusy(label)
    try {
      const r = await fn()
      onDone(r.queued > 0 ? `已排入 ${r.queued} 筆 ${label} 工作` : `沒有可加入佇列的文件（${label}）`)
    } catch (e: any) {
      onDone(`${label} 失敗：${e?.response?.data?.detail ?? e?.message}`)
    } finally { setBusy(null) }
  }

  const handleParse = (parser: typeof PARSE_KEYS[number]) => {
    const ids = skipExisting
      ? target.filter(i => !i.caches[parser]).map(i => i.id)
      : target.map(i => i.id)
    if (ids.length === 0) { onDone(`所有文件都已有 ${PARSER_LABEL[parser]} 快取`); return }
    run(`解析(${PARSER_LABEL[parser]})`, () => batchReparse(parser, ids))
  }

  const handleEmbed = () => {
    const ids = target.map(i => i.id)
    run(`嵌入(${PARSER_LABEL[embedParser] ?? embedParser})`, () => batchReparse(embedParser, ids))
  }

  const handleExtract = () => {
    const ids = target.filter(i => i.status === 'ready').map(i => i.id)
    if (ids.length === 0) { onDone('沒有已嵌入可摘要的文件'); return }
    run('摘要', () => batchExtractSelected(ids, true))
  }

  return (
    <div style={{ background: 'var(--adm-blue-bg)', border: '1px solid #BFDBFE', borderRadius: 'var(--adm-radius)', padding: '10px 14px', marginBottom: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Row 1: selection info + skip toggle + clear */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--adm-blue-text)' }}>
          {selected.size > 0 ? `已選 ${selected.size} 筆` : `全部 ${allFiltered.length} 筆`}
        </span>
        <label style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--adm-text-2)', cursor: 'pointer' }}>
          <input type="checkbox" checked={skipExisting} onChange={e => setSkipExisting(e.target.checked)} className="adm-checkbox" />
          跳過已有快取
        </label>
        <span className="adm-filter-spacer" />
        <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={onClear}>✕ 取消選取</button>
      </div>
      {/* Row 2: operations */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', flexShrink: 0 }}>解析</span>
        {PARSE_KEYS.map(p => {
          const missing = target.filter(i => !i.caches[p]).length
          const label = skipExisting ? (missing === 0 ? '已全有' : `${missing} 缺`) : `${target.length} 筆`
          return (
            <button key={p} className="adm-btn adm-btn-secondary adm-btn-sm"
              disabled={!!busy || (skipExisting && missing === 0)}
              onClick={() => handleParse(p)}>
              {busy === `解析(${PARSER_LABEL[p]})` ? '…' : `${PARSER_LABEL[p]} (${label})`}
            </button>
          )
        })}
        <span style={{ width: 1, height: 16, background: 'var(--adm-border-2)', margin: '0 4px' }} />
        <span style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', flexShrink: 0 }}>嵌入</span>
        <select className="adm-filter-select" style={{ height: 28, fontSize: 11, padding: '0 8px' }}
          value={embedParser} onChange={e => setEmbedParser(e.target.value)}>
          <option value="auto">自動</option>
          <option value="pymupdf4llm">PyMuPDF</option>
          <option value="azure_di">Azure DI</option>
          <option value="llamaparse">Llama</option>
        </select>
        <button className="adm-btn adm-btn-secondary adm-btn-sm" disabled={!!busy} onClick={handleEmbed}>
          {busy?.startsWith('嵌入') ? '…' : '嵌入'}
        </button>
        <span style={{ width: 1, height: 16, background: 'var(--adm-border-2)', margin: '0 4px' }} />
        <span style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', flexShrink: 0 }}>摘要</span>
        <button className="adm-btn adm-btn-secondary adm-btn-sm" disabled={!!busy} onClick={handleExtract}>
          {busy === '摘要' ? '…' : `摘要 (${target.filter(i => i.status === 'ready').length} 筆)`}
        </button>
      </div>
    </div>
  )
}

function pipelineDone(item: SummaryItem): boolean {
  return (
    item.summary !== null &&
    !!item.raw_research_answer &&
    !!(item.summary?.intro && item.summary?.questions?.length)
  )
}

function PipelineSteps({ item }: { item: SummaryItem }) {
  const steps = [item.summary !== null, !!item.raw_research_answer, !!(item.summary?.intro && item.summary?.questions?.length)]
  return (
    <div style={{ display: 'flex', gap: 3 }} title={`Step1:${steps[0]?'✓':'✗'} Step2:${steps[1]?'✓':'✗'} Step3:${steps[2]?'✓':'✗'}`}>
      {steps.map((done, i) => (
        <div key={i} style={{ width: 22, height: 5, borderRadius: 3, background: done ? 'var(--adm-green)' : 'var(--adm-border-2)' }} />
      ))}
    </div>
  )
}

// ── Tab bar ───────────────────────────────────────────────────────────────────

type Tab = 'info' | 'pipeline' | 'content' | 'chunks' | 'traces'
const TABS: { key: Tab; label: string }[] = [
  { key: 'info',     label: 'Info' },
  { key: 'pipeline', label: 'Pipeline' },
  { key: 'content',  label: 'Content' },
  { key: 'chunks',   label: 'Chunks' },
  { key: 'traces',   label: 'Traces' },
]

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <div style={{ display: 'flex', borderBottom: '1px solid var(--adm-border)', marginBottom: 16, overflowX: 'auto', scrollbarWidth: 'none' }}>
      {TABS.map(t => (
        <button key={t.key} onClick={() => onChange(t.key)} style={{
          flex: 1, padding: '6px 8px', border: 'none', background: 'none', cursor: 'pointer',
          fontSize: 12, fontWeight: active === t.key ? 600 : 400,
          color: active === t.key ? 'var(--adm-text)' : 'var(--adm-text-3)',
          borderBottom: `2px solid ${active === t.key ? 'var(--adm-text)' : 'transparent'}`,
          transition: 'color 120ms, border-color 120ms',
          whiteSpace: 'nowrap',
        }}>{t.label}</button>
      ))}
    </div>
  )
}

// ── Inline Chunk Viewer ───────────────────────────────────────────────────────

interface Chunk {
  id: string; section: string; page: number | string; page_end: number | string
  chunk_index: number | null; parser: string; chars: number; preview: string; content: string
}

const SECTION_COLORS: Record<string, string> = {
  introduction: '#3b82f6', methods: '#8b5cf6', results: '#10b981',
  discussion: '#f59e0b', conclusion: '#ef4444', references: '#64748b',
  background: '#06b6d4', other: '#64748b', unknown: '#d1d5db',
}
function sectionColor(s: string) { return SECTION_COLORS[s] ?? '#64748b' }

function InlineChunkViewer({ docId, parserFilter }: { docId: number; parserFilter: string }) {
  const [chunks,   setChunks]   = useState<Chunk[]>([])
  const [total,    setTotal]    = useState(0)
  const [loading,  setLoading]  = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [section,  setSection]  = useState('')
  const [editing,  setEditing]  = useState<string | null>(null)
  const [draft,    setDraft]    = useState('')
  const [saving,   setSaving]   = useState(false)
  const [deleting, setDeleting] = useState<Set<string>>(new Set())

  useEffect(() => {
    setLoading(true); setExpanded(null); setEditing(null); setSection('')
    axios.get(`${API_BASE}/api/documents/${docId}/chunks`)
      .then(r => { setChunks(r.data.chunks); setTotal(r.data.total) })
      .finally(() => setLoading(false))
  }, [docId])

  const visible = useMemo(() => {
    let list = chunks
    if (parserFilter && parserFilter !== 'all') list = list.filter(c => c.parser === parserFilter)
    if (section) list = list.filter(c => c.section === section)
    return list.sort((a, b) => (a.chunk_index ?? 9999) - (b.chunk_index ?? 9999))
  }, [chunks, parserFilter, section])

  const sections = useMemo(() => [...new Set(chunks.filter(c => !parserFilter || parserFilter === 'all' || c.parser === parserFilter).map(c => c.section))], [chunks, parserFilter])
  const maxChars = Math.max(...visible.map(c => c.chars), 1)

  const handleDelete = async (chunkId: string) => {
    if (!window.confirm('確定刪除這個 chunk？')) return
    setDeleting(prev => new Set(prev).add(chunkId))
    try {
      await deleteChunk(docId, chunkId)
      setChunks(prev => prev.filter(c => c.id !== chunkId))
      setTotal(p => p - 1)
      if (expanded === chunkId) setExpanded(null)
    } catch { alert('刪除失敗') }
    finally { setDeleting(prev => { const n = new Set(prev); n.delete(chunkId); return n }) }
  }

  const handleSave = async (chunkId: string) => {
    setSaving(true)
    try {
      await updateChunk(docId, chunkId, draft)
      setChunks(prev => prev.map(c => c.id === chunkId ? { ...c, content: draft, preview: draft.slice(0, 300), chars: draft.length } : c))
      setEditing(null)
    } catch { alert('儲存失敗') }
    finally { setSaving(false) }
  }

  if (loading) return <div style={{ padding: 24, color: 'var(--adm-text-3)' }}><div className="adm-spinner" style={{ margin: '0 auto 8px' }} />載入中…</div>

  return (
    <div>
      {/* section filter */}
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 10 }}>
        <button onClick={() => setSection('')} className={`adm-btn adm-btn-sm ${!section ? 'adm-btn-primary' : 'adm-btn-secondary'}`}>
          全部 ({visible.length})
        </button>
        {sections.map(s => (
          <button key={s} onClick={() => setSection(section === s ? '' : s)}
            className={`adm-btn adm-btn-sm ${section === s ? 'adm-btn-primary' : 'adm-btn-ghost'}`}
            style={{ borderLeft: `3px solid ${sectionColor(s)}` }}>
            {s}
          </button>
        ))}
      </div>

      {visible.length === 0 ? (
        <div style={{ padding: '20px 0', color: 'var(--adm-text-3)', fontSize: 12 }}>
          {chunks.length === 0 ? '尚未嵌入任何 chunk' : '此 parser 無 chunk'}
        </div>
      ) : visible.map((chunk, i) => {
        const isOpen = expanded === chunk.id
        const pLabel = chunk.page !== chunk.page_end ? `p.${chunk.page}-${chunk.page_end}` : `p.${chunk.page}`
        const barW = Math.round((chunk.chars / maxChars) * 100)
        return (
          <div key={chunk.id} onClick={() => setExpanded(isOpen ? null : chunk.id)}
            style={{ background: 'var(--adm-surface-2)', borderRadius: 6, padding: '10px 12px', marginBottom: 6, cursor: 'pointer', border: '1px solid var(--adm-border)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <span style={{ fontSize: 10, color: 'var(--adm-text-3)', minWidth: 24 }}>#{i + 1}</span>
                <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: sectionColor(chunk.section), color: '#fff' }}>{chunk.section}</span>
                <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{pLabel}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{chunk.chars}字</span>
                <button className="adm-btn-icon" title="編輯" onClick={e => { e.stopPropagation(); setEditing(chunk.id); setDraft(chunk.content); setExpanded(chunk.id) }}>
                  <Pencil size={11} />
                </button>
                <button className="adm-btn-icon" title="刪除" disabled={deleting.has(chunk.id)}
                  style={{ color: 'var(--adm-text-3)' }}
                  onClick={e => { e.stopPropagation(); handleDelete(chunk.id) }}>
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
            <div style={{ height: 3, background: 'var(--adm-border)', borderRadius: 2, marginBottom: 7 }}>
              <div style={{ height: '100%', width: `${barW}%`, background: sectionColor(chunk.section), borderRadius: 2 }} />
            </div>
            {editing === chunk.id ? (
              <div onClick={e => e.stopPropagation()}>
                <textarea className="adm-form-textarea" style={{ minHeight: 140, fontSize: 11, fontFamily: 'var(--adm-font-mono)' }}
                  value={draft} onChange={e => setDraft(e.target.value)} />
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  <button className="adm-btn adm-btn-primary adm-btn-sm" onClick={() => handleSave(chunk.id)} disabled={saving}>{saving ? '儲存中…' : '儲存'}</button>
                  <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={e => { e.stopPropagation(); setEditing(null) }}>取消</button>
                </div>
              </div>
            ) : isOpen ? (
              <div style={{ fontSize: 11, color: 'var(--adm-text)', lineHeight: 1.65 }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{chunk.content}</ReactMarkdown>
              </div>
            ) : (
              <div style={{ fontSize: 11, color: 'var(--adm-text-2)', lineHeight: 1.6 }}>
                {chunk.preview}{chunk.content.length > 300 ? '…' : ''}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Document detail drawer ─────────────────────────────────────────────────────

interface DetailProps {
  item: SummaryItem
  onClose: () => void
  onFlash: (msg: string) => void
  jobs: JobItem[]
}

function DocDetailDrawer({ item, onClose, onFlash, jobs }: DetailProps) {
  const [tab,      setTab]      = useState<Tab>('info')
  const titleEdit  = useInlineEdit()
  const abstractEdit = useInlineEdit()

  const [traceDetailId,    setTraceDetailId]    = useState<string | null>(null)
  const [docTraces,        setDocTraces]        = useState<TraceItem[]>([])
  const [tracesLoading,    setTracesLoading]    = useState(false)
  const [expandedTraceId,  setExpandedTraceId]  = useState<string | null>(null)
  const [parserCaches,     setParserCaches]     = useState<ParserCacheInfo[]>([])
  const [parserCachesLoaded, setParserCachesLoaded] = useState(false)
  const [parserChoice,     setParserChoice]     = useState<string | null>(null)
  const [parsingTabs,      setParsingTabs]      = useState<Set<string>>(new Set())
  const [extracting,       setExtracting]       = useState(false)
  const [extractingSteps,  setExtractingSteps]  = useState<Set<string>>(new Set())
  const [reindexing,       setReindexing]       = useState(false)
  const [confirmDel,       setConfirmDel]       = useState(false)
  const [localItem,        setLocalItem]        = useState(item)
  const [chunkParser,      setChunkParser]      = useState('all')

  useEffect(() => { setLocalItem(item) }, [item])

  useEffect(() => {
    abstractEdit.setDraft(item.abstract_text ?? '')
    setParserCachesLoaded(false)
    getParserCaches(item.id).then(d => { setParserCaches(d); setParserCachesLoaded(true) }).catch(() => setParserCachesLoaded(true))
    setTracesLoading(true)
    getDocumentTraces(item.id).then(setDocTraces).catch(() => setDocTraces([])).finally(() => setTracesLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.id])

  const effectiveParser = useMemo<string | null>(() => {
    if (parserChoice != null) return parserChoice
    if (localItem.parser_used && localItem.parser_used !== 'auto') return localItem.parser_used
    for (const p of CACHE_PRIORITY) { if (parserCaches.find(c => c.parser === p && c.available)) return p }
    return null
  }, [parserChoice, localItem, parserCaches])

  const isParseRunning = (parser: string) =>
    jobs.some(j => j.doc_id === localItem.id && j.status !== 'done' && j.job_type === `parse_${parser}`)

  // Actions
  const act = {
    parseWith: async (parser: string) => {
      const key = `${localItem.id}_${parser}`
      setParsingTabs(p => new Set(p).add(key))
      try { const r = await parseDocument(localItem.id, parser); onFlash(r.duplicate ? `${parser} 已在佇列中` : `已加入 ${parser} 解析佇列`) }
      catch (e: any) { onFlash(`解析失敗：${e?.response?.data?.detail ?? e?.message}`) }
      finally { setParsingTabs(p => { const n = new Set(p); n.delete(key); return n }) }
    },
    reindex: async () => {
      setReindexing(true)
      try { await reindexDocument(localItem.id, effectiveParser ?? 'auto'); onFlash('已加入重新嵌入佇列') }
      catch (e: any) { onFlash(`嵌入失敗：${e?.response?.data?.detail ?? e?.message}`) }
      finally { setReindexing(false) }
    },
    extract: async () => {
      setExtracting(true)
      try { const r = await extractSummary(localItem.id); onFlash(r.queued ? '已加入摘要佇列' : '已在佇列中') }
      catch (e: any) { onFlash(`摘要失敗：${e?.response?.data?.detail ?? e?.message}`) }
      finally { setExtracting(false) }
    },
    extractStep: async (step: 'step1' | 'step2' | 'step3') => {
      setExtractingSteps(p => new Set(p).add(step))
      try { const r = await extractSummaryStep(localItem.id, step); onFlash(r.queued ? `已加入 ${step.toUpperCase()} 佇列` : '已在佇列中') }
      catch (e: any) { onFlash(`${step} 失敗：${e?.response?.data?.detail ?? e?.message}`) }
      finally { setExtractingSteps(p => { const n = new Set(p); n.delete(step); return n }) }
    },
    refreshAbstract: async () => {
      try { const r = await refreshAbstractText(localItem.id); abstractEdit.setDraft(r.text ?? ''); setLocalItem(p => ({ ...p, abstract_text: r.text })); onFlash(r.skipped ? '找不到可更新的摘要' : '已重新整理 PDF 摘要') }
      catch (e: any) { onFlash(`更新失敗：${e?.response?.data?.detail ?? e?.message}`) }
    },
    saveAbstract: async () => {
      abstractEdit.setSaving(true)
      try { await updateAbstractText(localItem.id, abstractEdit.draft); setLocalItem(p => ({ ...p, abstract_text: abstractEdit.draft })); abstractEdit.cancelEdit(); onFlash('摘要已儲存') }
      catch (e: any) { onFlash(`儲存失敗：${e?.response?.data?.detail ?? e?.message}`) }
      finally { abstractEdit.setSaving(false) }
    },
    saveTitle: async () => {
      const t = titleEdit.draft.trim(); if (!t) return
      titleEdit.setSaving(true)
      try { const { filename } = await renameDocument(localItem.id, t); setLocalItem(p => ({ ...p, filename })); titleEdit.cancelEdit() }
      catch (e: any) { onFlash(`重新命名失敗：${e?.response?.data?.detail ?? e?.message}`) }
      finally { titleEdit.setSaving(false) }
    },
    delete: async () => {
      try { await fetch(`${API_BASE}/api/documents/${localItem.id}`, { method: 'DELETE' }); onClose(); onFlash('文件已刪除') }
      catch (e: any) { onFlash(`刪除失敗：${e?.message}`) }
    },
    refreshTraces: () => {
      setTracesLoading(true)
      getDocumentTraces(localItem.id).then(setDocTraces).catch(() => setDocTraces([])).finally(() => setTracesLoading(false))
    },
  }

  if (traceDetailId) return (
    <div>
      <button className="adm-btn adm-btn-ghost adm-btn-sm" style={{ marginBottom: 12 }} onClick={() => setTraceDetailId(null)}>← 返回</button>
      <TraceDetailContent traceId={traceDetailId} compact />
    </div>
  )

  return (
    <div>
      {/* ── Persistent status bar (above tabs) ── */}
      {(() => {
        const isRunning = localItem.batch_status === 'processing' || localItem.status === 'processing'
        const isError   = localItem.status === 'error' || localItem.batch_status === 'error'
        const allDone   = pipelineDone(localItem)
        const pipeStatus = isRunning ? 'running' : isError ? 'failed' : allDone ? 'completed' : 'pending'
        const pipeLabel  = isRunning ? '處理中' : isError ? '摘要失敗' : allDone ? '就緒' : '未完成'
        return (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
            <StatusBadge status={pipeStatus} label={pipeLabel} />
            <PipelineSteps item={localItem} />
            {localItem.needs_reindex && <span className="adm-badge adm-badge--error" style={{ fontSize: 10 }}>需重嵌入</span>}
            {localItem.quality_issue && <span className="adm-badge adm-badge--error" style={{ fontSize: 10 }}>{localItem.quality_issue}</span>}
          </div>
        )
      })()}

      <TabBar active={tab} onChange={setTab} />

      {/* ── INFO ── */}
      {tab === 'info' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Rename */}
          {titleEdit.editing ? (
            <div style={{ display: 'flex', gap: 6 }}>
              <input className="adm-form-input" autoFocus style={{ flex: 1, fontSize: 13 }}
                value={titleEdit.draft}
                onChange={e => titleEdit.setDraft(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') act.saveTitle(); if (e.key === 'Escape') titleEdit.cancelEdit() }} />
              <button className="adm-btn adm-btn-primary adm-btn-sm" onClick={act.saveTitle} disabled={titleEdit.saving}>{titleEdit.saving ? '…' : '儲存'}</button>
              <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={() => titleEdit.cancelEdit()}>取消</button>
            </div>
          ) : (
            <button className="adm-btn adm-btn-ghost adm-btn-sm" style={{ alignSelf: 'flex-start' }}
              onClick={() => { titleEdit.setDraft(getDisplayTitle(localItem.filename)); titleEdit.setEditing(true) }}>
              <Pencil size={11} /> 重新命名
            </button>
          )}

          {/* Meta grid — same style as trace overview */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 6 }}>
            {[
              ['系所', localItem.department],
              ['計畫編號', getProjectNumber(localItem.filename) || '—'],
              ['Parser', PARSER_LABEL[localItem.parser_used ?? ''] ?? '—'],
              ['建立', fmtDate(localItem.created_at)],
            ].map(([label, value]) => (
              <div key={String(label)} style={{ background: 'var(--adm-surface)', border: '1px solid var(--adm-border)', borderRadius: 'var(--adm-radius)', padding: '6px 10px' }}>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--adm-text-3)', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 12, color: 'var(--adm-text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div>
              </div>
            ))}
          </div>

          {/* PDF Abstract */}
          <div className="adm-card">
            <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10 }}>
              <span className="adm-card-title" style={{ margin: 0, flex: 1 }}>PDF 摘要</span>
              <button className="adm-btn-icon" onClick={act.refreshAbstract} title="重新整理"><RefreshCw size={12} /></button>
              {!abstractEdit.editing && <button className="adm-btn-icon" onClick={() => abstractEdit.setEditing(true)} title="編輯"><Pencil size={12} /></button>}
            </div>
            {abstractEdit.editing ? (
              <>
                <textarea className="adm-form-textarea" style={{ minHeight: 80 }} value={abstractEdit.draft} onChange={e => abstractEdit.setDraft(e.target.value)} />
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  <button className="adm-btn adm-btn-primary adm-btn-sm" onClick={act.saveAbstract} disabled={abstractEdit.saving}>{abstractEdit.saving ? '儲存中…' : '儲存'}</button>
                  <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={() => { abstractEdit.cancelEdit(); abstractEdit.setDraft(localItem.abstract_text ?? '') }}>取消</button>
                </div>
              </>
            ) : (
              <p style={{ fontSize: 12, color: localItem.abstract_text ? 'var(--adm-text-2)' : 'var(--adm-text-3)', lineHeight: 1.7, margin: 0, fontStyle: localItem.abstract_text ? 'normal' : 'italic' }}>
                {localItem.abstract_text || '尚未整理 PDF 摘要'}
              </p>
            )}
          </div>

          {/* Delete */}
          <div style={{ paddingTop: 4, display: 'flex', gap: 8 }}>
            {confirmDel ? (
              <>
                <button className="adm-btn adm-btn-danger adm-btn-sm" onClick={act.delete}>確認刪除</button>
                <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={() => setConfirmDel(false)}>取消</button>
              </>
            ) : (
              <button className="adm-btn adm-btn-ghost adm-btn-sm" style={{ color: 'var(--adm-red)' }} onClick={() => setConfirmDel(true)}>
                <Trash2 size={12} /> 刪除文件
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── PIPELINE ── */}
      {tab === 'pipeline' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Parser cache list */}
          <div className="adm-card">
            <div className="adm-card-title">解析快取</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {(['pymupdf4llm', 'azure_di', 'llamaparse'] as const).map(parser => {
                const cache = parserCaches.find(c => c.parser === parser)
                const busy  = parsingTabs.has(`${localItem.id}_${parser}`) || isParseRunning(parser)
                const available = parserCachesLoaded && cache?.available
                const sizeKb = cache?.available && cache.size > 0 ? `${(cache.size / 1024).toFixed(0)} KB` : null
                return (
                  <div key={parser} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px', borderRadius: 'var(--adm-radius)', background: 'var(--adm-surface-2)', border: '1px solid var(--adm-border)' }}>
                    <span style={{ fontSize: 12, color: 'var(--adm-text)', flex: 1, minWidth: 0 }}>{PARSER_LABEL[parser]}</span>
                    {!parserCachesLoaded
                      ? <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>…</span>
                      : available
                        ? <span className="adm-badge adm-badge--completed" style={{ fontSize: 10 }}>就緒</span>
                        : <span className="adm-badge adm-badge--pending" style={{ fontSize: 10 }}>無快取</span>}
                    {sizeKb && <span style={{ fontSize: 11, color: 'var(--adm-text-3)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>{sizeKb}</span>}
                    <button className="adm-btn adm-btn-ghost adm-btn-sm" style={{ flexShrink: 0 }} disabled={busy} onClick={() => act.parseWith(parser)}>
                      {busy ? '…' : available ? '重跑' : '解析'}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Embed */}
          <div className="adm-card">
            <div className="adm-card-title">嵌入</div>
            {localItem.status === 'ready' && localItem.parser_used && (
              <div style={{ fontSize: 12, color: 'var(--adm-green)', marginBottom: 8 }}>
                目前使用：{PARSER_LABEL[localItem.parser_used] ?? localItem.parser_used}
                {localItem.needs_reindex && <span className="adm-badge adm-badge--error" style={{ fontSize: 10, marginLeft: 6 }}>需重新嵌入</span>}
              </div>
            )}
            <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
              <select className="adm-form-select" style={{ width: 160 }}
                value={effectiveParser ?? ''}
                onChange={e => setParserChoice(e.target.value || null)}
                disabled={reindexing || localItem.status === 'processing'}>
                {!effectiveParser && <option value="">（無快取）</option>}
                <option value="auto">自動選擇</option>
                <option value="pymupdf4llm">PyMuPDF4LLM</option>
                <option value="azure_di">Azure DI</option>
                <option value="llamaparse">LlamaParse</option>
              </select>
              <button className="adm-btn adm-btn-secondary adm-btn-sm" onClick={act.reindex} disabled={reindexing || localItem.status === 'processing'}>
                {reindexing ? '排入中…' : '嵌入'}
              </button>
              <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={() => setTab('chunks')}>查看 Chunks →</button>
            </div>
          </div>

          {/* Extract */}
          <div className="adm-card">
            <div className="adm-card-title">摘要</div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              <button className="adm-btn adm-btn-secondary adm-btn-sm"
                disabled={extracting || localItem.batch_status === 'processing' || localItem.status !== 'ready'}
                onClick={act.extract}>
                {extracting ? '排入中…' : '完整摘要 (1+2+3)'}
              </button>
              {(['step1', 'step2', 'step3'] as const).map(step => (
                <button key={step} className="adm-btn adm-btn-ghost adm-btn-sm"
                  disabled={extractingSteps.has(step) || localItem.batch_status === 'processing' ||
                    (step !== 'step1' && !localItem.raw_research_answer) || localItem.status !== 'ready'}
                  onClick={() => act.extractStep(step)}>
                  {extractingSteps.has(step) ? '…' : step.toUpperCase()}
                </button>
              ))}
              <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={() => exportDocument(localItem.id, localItem.filename).catch(() => onFlash('匯出失敗'))}>
                <Download size={11} /> 匯出
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── CONTENT ── */}
      {tab === 'content' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {/* Raw research */}
          <div className="adm-card">
            <div className="adm-card-title">原始研究摘要</div>
            {localItem.raw_research_answer ? (
              <>
                <div style={{ fontSize: 12, color: 'var(--adm-text-2)', lineHeight: 1.7 }}>
                  <Suspense fallback={<p style={{ margin: 0, fontSize: 12 }}>{localItem.raw_research_answer}</p>}>
                    <MarkdownRenderer content={localItem.raw_research_answer} />
                  </Suspense>
                </div>
                {localItem.raw_research_sources?.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
                    {localItem.raw_research_sources.slice(0, 6).map(src => (
                      <span key={src} className="adm-badge adm-badge--info" style={{ fontSize: 10 }}>{src}</span>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p style={{ fontSize: 12, color: 'var(--adm-text-3)', fontStyle: 'italic', margin: 0 }}>尚未產生</p>
            )}
          </div>

          {/* AI summary */}
          <div className="adm-card">
            <div className="adm-card-title">AI 摘要</div>
            {localItem.summary ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {(['motivation', 'method', 'results'] as const).map((key, i) => (
                  <div key={key}>
                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--adm-text-3)', marginBottom: 4 }}>
                      {['研究動機', '研究方法', '研究成果'][i]}
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--adm-text-2)', lineHeight: 1.7 }}>
                      <Suspense fallback={<p style={{ margin: 0 }}>{localItem.summary![key]}</p>}>
                        <MarkdownRenderer content={localItem.summary![key]} />
                      </Suspense>
                    </div>
                  </div>
                ))}
                {(localItem.summary.tags?.length ?? 0) > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 4 }}>
                    {localItem.summary!.tags.map(t => <span key={t} className="adm-badge adm-badge--info" style={{ fontSize: 10 }}>{t}</span>)}
                  </div>
                )}
              </div>
            ) : (
              <p style={{ fontSize: 12, color: 'var(--adm-text-3)', fontStyle: 'italic', margin: 0 }}>尚未產生</p>
            )}
          </div>

          {/* 一分鐘看研究 */}
          <div className="adm-card">
            <div className="adm-card-title">一分鐘看研究</div>
            {localItem.summary?.intro || (localItem.summary?.questions ?? []).length > 0 ? (
              <>
                {localItem.summary!.intro && (
                  <div style={{ fontSize: 12, color: 'var(--adm-text-2)', lineHeight: 1.7, marginBottom: 10 }}>
                    <Suspense fallback={<p style={{ margin: 0 }}>{localItem.summary!.intro}</p>}>
                      <MarkdownRenderer content={localItem.summary!.intro} />
                    </Suspense>
                  </div>
                )}
                {(localItem.summary!.questions ?? []).length > 0 && (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--adm-text-3)', marginBottom: 2 }}>興趣量表（1–5）</div>
                    {(localItem.summary!.questions ?? []).map((q, i) => (
                      <div key={i} style={{ padding: '8px 10px', borderRadius: 6, background: 'var(--adm-surface-2)', border: '1px solid var(--adm-border)', fontSize: 12, color: 'var(--adm-text-2)', lineHeight: 1.6 }}>{q}</div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p style={{ fontSize: 12, color: 'var(--adm-text-3)', fontStyle: 'italic', margin: 0 }}>尚未產生</p>
            )}
          </div>
        </div>
      )}

      {/* ── CHUNKS ── */}
      {tab === 'chunks' && (
        <div>
          {/* Parser switcher */}
          <div className="adm-filters" style={{ marginBottom: 12 }}>
            {(['all', 'pymupdf4llm', 'azure_di', 'llamaparse'] as const).map(p => (
              <button key={p} className={`adm-btn adm-btn-sm ${chunkParser === p ? 'adm-btn-primary' : 'adm-btn-secondary'}`}
                onClick={() => setChunkParser(p)}>
                {p === 'all' ? 'All' : PARSER_LABEL[p]}
                {p !== 'all' && parserCaches.find(c => c.parser === p)?.available && (
                  <span style={{ marginLeft: 4, fontSize: 10, opacity: 0.7 }}>
                    {((parserCaches.find(c => c.parser === p)?.size ?? 0) / 1024).toFixed(0)}KB
                  </span>
                )}
              </button>
            ))}
          </div>
          <InlineChunkViewer docId={localItem.id} parserFilter={chunkParser} />
        </div>
      )}

      {/* ── TRACES ── */}
      {tab === 'traces' && (
        <div className="adm-card">
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
            <span className="adm-card-title" style={{ margin: 0, flex: 1 }}>追蹤紀錄</span>
            <button className="adm-btn-icon" onClick={act.refreshTraces} title="重新整理"><RefreshCw size={12} /></button>
          </div>
          {tracesLoading ? (
            <div style={{ padding: 24, color: 'var(--adm-text-3)' }}><div className="adm-spinner" style={{ margin: '0 auto 8px' }} />載入中…</div>
          ) : docTraces.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--adm-text-3)', fontStyle: 'italic' }}>尚無追蹤紀錄</div>
          ) : docTraces.map(trace => {
            const isOpen = expandedTraceId === trace.id
            const isErr  = trace.level === 'ERROR'
            return (
              <div key={trace.id}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '7px 0', cursor: 'pointer', borderBottom: '1px solid var(--adm-border)' }}
                  onClick={() => setExpandedTraceId(isOpen ? null : trace.id)}>
                  <span style={{ width: 7, height: 7, borderRadius: '50%', background: isErr ? 'var(--adm-red)' : 'var(--adm-green)', flexShrink: 0 }} />
                  <span style={{ fontSize: 12, color: 'var(--adm-text-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{trace.name}</span>
                  <span style={{ fontSize: 11, color: 'var(--adm-text-3)', whiteSpace: 'nowrap' }}>{relativeTime(trace.start_time ?? undefined)}</span>
                  {trace.latency != null && <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{trace.latency.toFixed(1)}s</span>}
                  <button className="adm-btn-icon" title="在 Trace 監控開啟" onClick={e => { e.stopPropagation(); setTraceDetailId(trace.id) }}><ExternalLink size={11} /></button>
                  <ChevronRight size={12} style={{ color: 'var(--adm-text-3)', transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 150ms', flexShrink: 0 }} />
                </div>
                {isOpen && trace.display && (
                  <div style={{ padding: '8px 0 8px 13px', display: 'flex', flexDirection: 'column', gap: 5 }}>
                    {(trace.display.messages ?? []).map((msg: any, mi: number) => {
                      if (msg.role === 'ai_tool_call') return (msg.tool_calls ?? []).map((tc: any) => (
                        <div key={tc.call_id} style={{ fontSize: 11 }}>
                          <span style={{ color: 'var(--adm-violet)', fontWeight: 600 }}>AI › {tc.tool}</span>
                          <pre style={{ margin: '2px 0 0', fontSize: 10, background: 'var(--adm-surface-2)', padding: '4px 6px', borderRadius: 4, overflow: 'auto', maxHeight: 80 }}>{JSON.stringify(tc.args, null, 2)}</pre>
                        </div>
                      ))
                      if (!msg.content || msg.role === 'tool_result') return null
                      const roleColor: Record<string, string> = { system: 'var(--adm-text-3)', human: 'var(--adm-blue)', ai: 'var(--adm-violet)' }
                      const roleLabel: Record<string, string> = { system: '系統', human: '問題', ai: 'AI' }
                      return (
                        <div key={mi} style={{ fontSize: 11 }}>
                          <span style={{ color: roleColor[msg.role] ?? 'var(--adm-text-3)', fontWeight: 600, marginRight: 6 }}>{roleLabel[msg.role] ?? msg.role}</span>
                          <span style={{ color: 'var(--adm-text-2)', lineHeight: 1.5 }}>{String(msg.content).slice(0, 300)}{String(msg.content).length > 300 ? '…' : ''}</span>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 40,  resizable: false },  // 0 checkbox
  { width: 260, resizable: true  },  // 1 標題
  { width: 130, resizable: true  },  // 2 系所
  { width: 50,  resizable: false },  // 3 年份
  { width: 100, resizable: false },  // 4 狀態
  { width: 80,  resizable: false },  // 5 Pipeline
  { width: 80,  resizable: false },  // 6 Parser
  { width: 120, resizable: false },  // 7 建立時間
]

export default function DocumentsPage() {
  const { items, setLoading, loading, search, setSearch, deptFilter, setDeptFilter, loadItems } = useDocumentList()
  const jobs    = useJobStore(selectJobs)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-docs-col-widths')
  const sort = useTableSort<SummaryItem>({
    created_at: d => d.created_at,
  })
  const setJobs = useJobStore(s => s.setJobs)
  const fileRef = useRef<HTMLInputElement>(null)
  const selectedIdRef = useRef<number | null>(null)
  const pollRef = useRef<number | null>(null)

  const [selectedId,     setSelectedId]     = useState<number | null>(null)
  const [statusFilter,   setStatusFilter]   = useState('all')
  const [uploading,      setUploading]      = useState(false)
  const [batchSelected,  setBatchSelected]  = useState<Set<number>>(new Set())
  const [flash,          setFlash]          = useState<string | null>(null)

  const toggleBatch = (id: number, e: React.MouseEvent) => {
    e.stopPropagation()
    setBatchSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }
  const toggleAllBatch = () => {
    setBatchSelected(prev => prev.size === filtered.length ? new Set() : new Set(filtered.map(i => i.id)))
  }

  const onFlash = (msg: string) => { setFlash(msg); setTimeout(() => setFlash(null), 3500) }

  const loadJobs = useCallback(async () => {
    const data = await getJobs(); setJobs(data)
  }, [setJobs])

  const refreshAll = useCallback(async () => {
    setLoading(true)
    try { await Promise.all([loadItems(), loadJobs()]) }
    finally { setLoading(false) }
  }, [loadItems, loadJobs, setLoading])

  useEffect(() => { refreshAll().catch(() => setLoading(false)) }, [refreshAll])

  useEffect(() => {
    const shouldPoll = items.some(i => i.status === 'processing' || i.batch_status === 'processing') || jobs.some(j => j.status !== 'done')
    if (shouldPoll && !pollRef.current) pollRef.current = window.setInterval(() => refreshAll().catch(() => {}), 4000)
    if (!shouldPoll && pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null } }
  }, [items, jobs, refreshAll])

  useJobSSE({ onItemsDone: () => loadItems().catch(() => {}), onParseDone: () => {}, onExtractDone: () => {}, selectedIdRef })

  const departments = useMemo(
    () => ['全部', ...Array.from(new Set(items.map(i => i.department))).sort((a, b) => a.localeCompare(b, 'zh-TW'))],
    [items]
  )

  const filtered = useMemo(() => items.filter(item => {
    if (statusFilter === 'ready'      && item.status !== 'ready')      return false
    if (statusFilter === 'processing' && item.status !== 'processing') return false
    if (statusFilter === 'error'      && item.status !== 'error')      return false
    if (statusFilter === 'no-summary' && item.summary !== null)        return false
    if (deptFilter !== '全部' && item.department !== deptFilter)        return false
    const q = search.trim().toLowerCase()
    if (!q) return true
    return [getDisplayTitle(item.filename), item.filename, item.department, String(item.id),
      item.summary?.motivation ?? '', item.abstract_text ?? ''].some(v => v.toLowerCase().includes(q))
  }), [items, statusFilter, search, deptFilter])

  const selected = useMemo(() => items.find(i => i.id === selectedId) ?? null, [items, selectedId])
  const summaryCount = items.filter(i => i.summary).length

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []).filter(f => f.type === 'application/pdf')
    if (!files.length) return
    setUploading(true)
    for (const f of files) { try { await uploadDocument(f) } catch {} }
    setUploading(false)
    loadItems().catch(() => {})
    e.target.value = ''
  }

  return (
    <div>
      {/* Toolbar */}
      <div className="adm-filters" style={{ marginBottom: 12 }}>
        <input className="adm-filter-input" placeholder="搜尋標題、摘要、系所…" value={search} onChange={e => setSearch(e.target.value)} />
        <select className="adm-filter-select" value={deptFilter} onChange={e => setDeptFilter(e.target.value)}>
          {departments.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        {(['all', 'ready', 'processing', 'error', 'no-summary'] as const).map(s => (
          <button key={s} className={`adm-btn adm-btn-sm ${statusFilter === s ? 'adm-btn-primary' : 'adm-btn-secondary'}`}
            onClick={() => setStatusFilter(s)}>
            {s === 'all' ? 'All' : s === 'no-summary' ? 'No Summary' : s.charAt(0).toUpperCase() + s.slice(1)}
          </button>
        ))}
        <span className="adm-filter-spacer" />
        {flash && <span style={{ fontSize: 12, color: 'var(--adm-green)', fontWeight: 500 }}>{flash}</span>}
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{filtered.length} 筆</span>
        <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={async () => { const r = await batchImport(); onFlash(r.queued > 0 ? `已加入 ${r.queued} 筆匯入工作` : '沒有新檔案') }}>批量匯入</button>
        <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={async () => { const r = await batchExtractAll(); onFlash(`已排入 ${r.queued} 筆摘要工作`) }}>批量摘要</button>
        <button className="adm-btn adm-btn-primary adm-btn-sm" disabled={uploading} onClick={() => fileRef.current?.click()}>
          <Upload size={12} /> {uploading ? '上傳中…' : '上傳 PDF'}
        </button>
        <button className="adm-btn-icon" onClick={() => refreshAll()} disabled={loading} title="重新整理">
          <RefreshCw size={14} style={loading ? { animation: 'adm-spin 600ms linear infinite' } : undefined} />
        </button>
        <input ref={fileRef} type="file" accept=".pdf" multiple style={{ display: 'none' }} onChange={handleUpload} />
      </div>

      {/* Batch action bar — appears when any row is checked */}
      {batchSelected.size > 0 && (
        <BatchBar
          selected={batchSelected}
          allFiltered={filtered}
          onClear={() => setBatchSelected(new Set())}
          onDone={msg => { onFlash(msg); refreshAll().catch(() => {}) }}
        />
      )}

      {/* Table */}
      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th className="adm-col-checkbox">
                <input type="checkbox" className="adm-checkbox"
                  checked={filtered.length > 0 && batchSelected.size === filtered.length}
                  onChange={toggleAllBatch} />
              </th>
              <th>標題<span {...div(1)} /></th>
              <th>系所<span {...div(2)} /></th>
              <th>年份<span {...div(3)} /></th>
              <th>狀態<span {...div(4)} /></th>
              <th>Pipeline<span {...div(5)} /></th>
              <th>Parser<span {...div(6)} /></th>
              <th {...sort.th('created_at')}>建立時間{sort.ind('created_at')}</th>
            </tr>
          </thead>
          <tbody>
            {loading && items.length === 0 ? (
              <tr><td colSpan={8} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : filtered.length === 0 ? (
              <tr><td colSpan={8} className="adm-table-empty">沒有符合的文件</td></tr>
            ) : sort.apply(filtered).slice((page-1)*pageSize, page*pageSize).map(item => {
              const isBatchSel = batchSelected.has(item.id)
              return (
                <tr key={item.id}
                  className={`adm-row--clickable${item.status === 'error' ? ' adm-row--danger' : ''}${isBatchSel ? ' adm-row--selected' : ''}`}
                  onClick={() => { setSelectedId(item.id); selectedIdRef.current = item.id }}
                >
                  <td className="adm-col-checkbox">
                    <input type="checkbox" className="adm-checkbox" checked={isBatchSel}
                      onChange={e => { e.stopPropagation(); setBatchSelected(prev => { const n = new Set(prev); n.has(item.id) ? n.delete(item.id) : n.add(item.id); return n }) }}
                      onClick={e => e.stopPropagation()} />
                  </td>
                  <td style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 260 }} title={getDisplayTitle(item.filename)}>
                    {getDisplayTitle(item.filename)}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.department}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{getYear(item.filename) || '—'}</td>
                  <td>
                    {(() => {
                      const isRunning = item.batch_status === 'processing' || item.status === 'processing'
                      const isError   = item.status === 'error' || item.batch_status === 'error'
                      const done      = pipelineDone(item)
                      return (
                        <StatusBadge
                          status={isRunning ? 'running' : isError ? 'failed' : done ? 'completed' : 'pending'}
                          label={isRunning ? '處理中' : isError ? '錯誤' : done ? '就緒' : '未完成'}
                        />
                      )
                    })()}
                  </td>
                  <td><PipelineSteps item={item} /></td>
                  <td>
                    {item.parser_used && item.parser_used !== 'auto'
                      ? <span className="adm-badge adm-badge--info" style={{ fontSize: 10 }}>{PARSER_LABEL[item.parser_used] ?? item.parser_used}</span>
                      : <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>—</span>}
                  </td>
                  <td className="adm-cell-mono">{fmtDate(item.created_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <TablePagination
        page={page} pageCount={Math.max(1, Math.ceil(filtered.length / pageSize))}
        pageSize={pageSize} total={filtered.length}
        onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
      />

      {/* Detail drawer */}
      <DrawerPanel
        open={!!selected}
        title={selected ? getDisplayTitle(selected.filename) : ''}
        onClose={() => { setSelectedId(null); selectedIdRef.current = null }}
        width={540}
      >
        {selected && (
          <DocDetailDrawer
            key={selected.id}
            item={selected}
            onClose={() => { setSelectedId(null); selectedIdRef.current = null }}
            onFlash={onFlash}
            jobs={jobs}
          />
        )}
      </DrawerPanel>

    </div>
  )
}
