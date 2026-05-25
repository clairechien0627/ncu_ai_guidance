/**
 * TracesPage — Langfuse-inspired layout with filter sidebar, toolbar, column visibility
 */
import { useMemo, useReducer, useRef, useState, useEffect, useLayoutEffect, useCallback } from 'react'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { useColumnVisibility } from '../../hooks/useColumnVisibility'
import { TablePagination } from '../../components/admin/TablePagination'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  OctagonX, TriangleAlert, CircleCheck, SquareTerminal,
  RefreshCw, X, PanelLeftOpen, PanelLeftClose,
  Search, ChevronDown, Columns, Star, GripVertical,
} from 'lucide-react'
import {
  getTraces, getTraceStats, getPromptList, createEvalRun,
  addTraceToDataset, getDatasets, deleteTraces, updateTraceBookmark,
  getTraceTags, getTraceNames, getTraceUserIds,
  type TraceItem, type TraceFilters, type TraceStats,
  type PromptInfo, type DatasetData,
} from '../../api'
import { useAdminStore } from '../../stores/adminStore'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { BulkActionBar } from '../../components/admin/BulkActionBar'
import { DrawerPanel } from '../../components/admin/DrawerPanel'
import { TraceDetailContent } from '../../components/admin/TraceDetailContent'

// ── Constants ─────────────────────────────────────────────────────────────────

const PAGE_SIZE = 40
const DATE_PRESETS = [
  { label: 'Today',       days: 0  },
  { label: 'Past 7d',     days: 7  },
  { label: 'Past 14d',    days: 14 },
  { label: 'Past 30d',    days: 30 },
]

const REFRESH_OPTIONS = [
  { label: 'Off',  ms: 0       },
  { label: '30s',  ms: 30_000  },
  { label: '1m',   ms: 60_000  },
  { label: '5m',   ms: 300_000 },
]

// ── Column definitions ─────────────────────────────────────────────────────────

interface ColEntry extends ColDef { key: string; label: string; required?: boolean }

const ALL_COLS: ColEntry[] = [
  { key: 'checkbox',    label: '',               required: true,  width: 40,  resizable: false },
  { key: 'time',        label: 'Timestamp',      required: true,  width: 100, resizable: false },
  { key: 'name',        label: 'Name',                            width: 120, resizable: true, minWidth: 65, maxWidth: 150  },
  { key: 'input',       label: 'Input',                           width: 180, resizable: true, minWidth: 120, maxWidth: 600  },
  { key: 'output',      label: 'Output',                          width: 180, resizable: true, minWidth: 120, maxWidth: 600  },
  { key: 'obs_statuses', label: 'Obs. Statuses',                  width: 100,  resizable: false },
  { key: 'latency',     label: 'Latency',                         width: 80,  resizable: false },
  { key: 'tokens',        label: 'Tokens',                        width: 90,  resizable: false },
  { key: 'input_tokens',  label: 'Input Tokens',                  width: 80,  resizable: false },
  { key: 'output_tokens', label: 'Output Tokens',                 width: 80,  resizable: false },
  { key: 'total_cost',    label: 'Cost',                          width: 80,  resizable: false },
  { key: 'input_cost',    label: 'Input Cost',                    width: 80,  resizable: false },
  { key: 'output_cost',   label: 'Output Cost',                   width: 80,  resizable: false },
  { key: 'environment', label: 'Env',                             width: 120,  resizable: false },
  { key: 'tags',        label: 'Tags',                            width: 140, resizable: true, minWidth: 80, maxWidth: 300 },
  { key: 'metadata',    label: 'Metadata',                        width: 90,  resizable: false },
  { key: 'score',       label: 'Score',                           width: 130, resizable: false },
  { key: 'session',     label: 'Thread',                          width: 120, resizable: true, minWidth: 80, maxWidth: 300  },
  { key: 'user',        label: 'User',                            width: 100, resizable: true, minWidth: 80, maxWidth: 300  },
  { key: 'obs_count',   label: 'Observations',                    width: 110,  resizable: false },
  { key: 'status',      label: 'Status',                          width: 110,  resizable: false },
  { key: 'trace_id',    label: 'Trace ID',                        width: 120, resizable: true, minWidth: 80, maxWidth: 300  },
  { key: 'actions',     label: '',               required: true,  width: 50,  resizable: false },
]

const COL_DEFAULTS: Record<string, boolean> = {
  // Langfuse defaults ON
  time: true, name: true, input: true, output: true,
  obs_statuses: true, latency: true, tokens: true, total_cost: true,
  environment: true, tags: true, metadata: true,
  // Langfuse defaults OFF
  score: true, session: false, user: false,
  obs_count: false, status: false, trace_id: false,
  input_tokens: false, output_tokens: false,
  input_cost: false, output_cost: false,
}

// Keys of user-orderable columns (excludes fixed checkbox / time / actions)
const NON_REQUIRED_KEYS = ALL_COLS
  .filter(c => !c.required && c.key !== 'checkbox' && c.key !== 'actions')
  .map(c => c.key)

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTime(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function inputPreview(t: TraceItem): string {
  if (typeof t.input === 'string' && t.input) return t.input.slice(0, 100)
  return ''
}
function outputPreview(t: TraceItem): string {
  if (typeof t.output === 'string' && t.output) return t.output.slice(0, 100)
  return ''
}
function dateAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

// ── Filter state ──────────────────────────────────────────────────────────────

type FilterState = {
  statuses: string[]
  tags: string[]
  names: string[]
  user_ids: string[]
  prompt: string
  latency: string
  min_quality: string
  max_quality: string
  min_tokens: string
  max_tokens: string
  bookmarked: boolean
  search: string
  date_from: string
  date_to: string
  datePreset: number | null
}

const FILTER_DEFAULT: FilterState = {
  statuses: [], tags: [], names: [], user_ids: [],
  prompt: 'all', latency: '', min_quality: '', max_quality: '',
  min_tokens: '', max_tokens: '', bookmarked: false,
  search: '', date_from: '', date_to: '', datePreset: 14,
}

function filterReducer(s: FilterState, a: Partial<FilterState> | 'reset'): FilterState {
  if (a === 'reset') return FILTER_DEFAULT
  return { ...s, ...a }
}

// ── Sub-components ────────────────────────────────────────────────────────────

function FilterSection({ title, children, defaultOpen = true }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="adm-filter-section">
      <button className={`adm-filter-section-header${open ? ' open' : ''}`} onClick={() => setOpen(o => !o)}>
        {title}
        <ChevronDown size={13} />
      </button>
      {open && <div className="adm-filter-section-body">{children}</div>}
    </div>
  )
}

function CheckItem({ label, count, checked, onChange }: { label: string; count?: number; checked: boolean; onChange: () => void }) {
  return (
    <label className="adm-filter-check-item">
      <input type="checkbox" checked={checked} onChange={onChange} />
      <span style={{ flex: 1 }}>{label}</span>
      {count != null && <span className="adm-filter-check-count">{count}</span>}
    </label>
  )
}

function PromptDropdown({ value, options, onChange }: { value: string; options: string[]; onChange: (v: string) => void }) {
  const [open, setOpen] = useState(false)
  const label = value === 'all' ? 'All prompts' : value
  return (
    <div style={{ position: 'relative' }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOpen(false) }}>
      <button className="adm-tb-btn" style={{ width: '100%', justifyContent: 'space-between' }} onClick={() => setOpen(o => !o)}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
        <ChevronDown size={13} style={{ opacity: 0.5, flexShrink: 0 }} />
      </button>
      {open && (
        <div className="adm-dropdown" style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 200, marginTop: 4, maxHeight: 220, overflowY: 'auto' }}>
          {['all', ...options].map(n => (
            <button key={n} className={`adm-dropdown-item${value === n ? ' adm-dropdown-item--active' : ''}`}
              onClick={() => { onChange(n); setOpen(false) }}>
              {n === 'all' ? 'All prompts' : n}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function AddToDatasetModal({ traceId, onClose, onDone }: { traceId: string; onClose: () => void; onDone: () => void }) {
  const [dsId, setDsId] = useState('')
  const [loading, setLoading] = useState(false)
  const { data } = useQuery({ queryKey: ['datasets-list'], queryFn: getDatasets })
  const datasets: DatasetData[] = data?.datasets ?? []
  const handleAdd = async () => {
    if (!dsId) return
    setLoading(true)
    try { await addTraceToDataset(dsId, traceId); onDone() } catch { setLoading(false) }
  }
  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" style={{ width: 360 }} onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">加入 Dataset</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <div className="adm-form-group">
            <label className="adm-form-label">選擇 Dataset</label>
            <select className="adm-form-select" value={dsId} onChange={e => setDsId(e.target.value)}>
              <option value="">— 選擇 —</option>
              {datasets.map(d => <option key={d.dataset_id} value={d.dataset_id}>{d.name} ({d.item_count ?? '?'})</option>)}
            </select>
          </div>
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-primary" disabled={!dsId || loading} onClick={handleAdd}>{loading ? '…' : '確認'}</button>
        </div>
      </div>
    </div>
  )
}

function DeleteConfirmModal({ count, onClose, onConfirm, loading }: { count: number; onClose: () => void; onConfirm: () => void; loading: boolean }) {
  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" style={{ width: 360 }} onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">確認刪除</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <p style={{ fontSize: 13, color: 'var(--adm-text-2)', margin: 0 }}>
            確定要刪除 <strong>{count}</strong> 筆 trace 及其相關 observation、score？此操作無法復原。
          </p>
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-danger" disabled={loading} onClick={onConfirm}>{loading ? '刪除中…' : '確認刪除'}</button>
        </div>
      </div>
    </div>
  )
}

function StatsBar({ stats }: { stats: TraceStats | null | undefined }) {
  if (!stats) return null
  const items = [
    { label: 'Total',    value: stats.total_runs.toLocaleString() },
    { label: 'Avg Score', value: stats.avg_quality != null ? `${stats.avg_quality.toFixed(2)}/5` : '—' },
    { label: 'Error Rate', value: stats.error_rate != null ? `${stats.error_rate.toFixed(1)}%` : '—' },
    { label: 'Avg Latency', value: stats.avg_latency != null ? `${stats.avg_latency.toFixed(1)}s` : '—' },
  ]
  return (
    <div style={{ display: 'flex', gap: 1, borderBottom: '1px solid var(--adm-border)', background: 'var(--adm-border)', flexShrink: 0 }}>
      {items.map(({ label, value }) => (
        <div key={label} style={{ flex: 1, background: 'var(--adm-surface)', padding: '8px 14px' }}>
          <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--adm-text-3)', marginBottom: 2 }}>{label}</div>
          <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--adm-text)', fontVariantNumeric: 'tabular-nums' }}>{value}</div>
        </div>
      ))}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TracesPage() {
  const navigate  = useNavigate()
  const qc        = useQueryClient()
  const { environment } = useAdminStore()

  // ── State ────────────────────────────────────────────────────────────────────

  const [filters,     dispatch]    = useReducer(filterReducer, FILTER_DEFAULT)
  const [selected,    setSelected] = useState<Set<string>>(new Set())
  const [page,        setPage]     = useState(1)
  const [filterOpen,  setFilterOpen] = useState(() => {
    try { return localStorage.getItem('adm-traces-filter-open') !== 'false' } catch { return true }
  })
  const [refreshMs,   setRefreshMs] = useState(60_000)
  const [showRefreshMenu, setShowRefreshMenu] = useState(false)
  const [showDateMenu,    setShowDateMenu]    = useState(false)
  const [colsOpen,        setColsOpen]        = useState(false)
  const [drawerTraceId,   setDrawerTraceId]   = useState<string | null>(null)
  const [addDatasetId,    setAddDatasetId]    = useState<string | null>(null)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleting,        setDeleting]        = useState(false)
  const [toast,           setToast]           = useState<string | null>(null)

  const toggleFilterOpen = () => setFilterOpen(o => {
    const next = !o
    try { localStorage.setItem('adm-traces-filter-open', String(next)) } catch {}
    return next
  })

  // ── Column order ─────────────────────────────────────────────────────────────

  const [colOrder, setColOrder] = useState<string[]>(() => {
    try {
      const saved = localStorage.getItem('adm-traces-col-order')
      if (saved) {
        const parsed = JSON.parse(saved) as string[]
        const valid = parsed.filter(k => NON_REQUIRED_KEYS.includes(k))
        const added = NON_REQUIRED_KEYS.filter(k => !valid.includes(k))
        return [...valid, ...added]
      }
    } catch {}
    return NON_REQUIRED_KEYS
  })

  const saveColOrder = (order: string[]) => {
    setColOrder(order)
    try { localStorage.setItem('adm-traces-col-order', JSON.stringify(order)) } catch {}
  }

  const [dragColKey,     setDragColKey]     = useState<string | null>(null)
  const [dragOverColKey, setDragOverColKey] = useState<string | null>(null)

  const handleGripMouseDown = (e: React.MouseEvent, key: string) => {
    e.preventDefault()
    document.documentElement.classList.add('adm-col-dragging')
    setDragColKey(key)

    const prevent = (ev: Event) => ev.preventDefault()
    window.addEventListener('selectstart', prevent)
    window.addEventListener('dragstart',   prevent)

    const handleMouseUp = () => {
      document.documentElement.classList.remove('adm-col-dragging')
      window.removeEventListener('selectstart', prevent)
      window.removeEventListener('dragstart',   prevent)
      window.removeEventListener('mouseup', handleMouseUp)
      setDragColKey(null)
      setDragOverColKey(prev => {
        if (prev && prev !== key) {
          setColOrder(order => {
            const from = order.indexOf(key)
            const to   = order.indexOf(prev)
            if (from !== -1 && to !== -1) {
              const next = [...order]
              next.splice(from, 1)
              next.splice(to, 0, key)
              try { localStorage.setItem('adm-traces-col-order', JSON.stringify(next)) } catch {}
              return next
            }
            return order
          })
        }
        return null
      })
    }
    window.addEventListener('mouseup', handleMouseUp)
  }

  // ── Column visibility ────────────────────────────────────────────────────────

  const { visible, toggle: toggleCol, reset: resetCols } = useColumnVisibility('adm-traces-col-visibility', COL_DEFAULTS)
  const visibleCols = useMemo(() => {
    const fixedStart = ALL_COLS.filter(c => c.required && c.key !== 'actions') // checkbox + time
    const fixedEnd   = ALL_COLS.filter(c => c.key === 'actions')
    const optional   = colOrder
      .map(key => ALL_COLS.find(c => c.key === key)!)
      .filter(c => c && (visible[c.key] ?? true))
    return [...fixedStart, ...optional, ...fixedEnd]
  }, [visible, colOrder])
  const { widths, div } = useLinkedColumnResize(visibleCols as ColDef[], 'adm-traces-col-widths')

  // ── Distribute extra container space to resizable columns only ───────────
  const tableWrapRef = useRef<HTMLDivElement>(null)
  const [wrapWidth, setWrapWidth] = useState(0)

  useLayoutEffect(() => {
    const el = tableWrapRef.current
    if (!el) return
    setWrapWidth(el.clientWidth)
    const ro = new ResizeObserver(([entry]) => setWrapWidth(entry.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const displayWidths = useMemo(() => {
    if (wrapWidth <= 0) return widths
    const total = widths.reduce((a, b) => a + b, 0)
    const extra = wrapWidth - total
    if (extra <= 0) return widths  // table is wider than container, scroll normally
    const resizableIdxs = visibleCols
      .map((c, i) => (c as ColDef & { resizable: boolean }).resizable ? i : -1)
      .filter(i => i !== -1)
    if (resizableIdxs.length === 0) return widths
    const result = [...widths]
    let remaining = extra
    let available = resizableIdxs.filter(i => {
      const col = visibleCols[i] as ColEntry
      return result[i] < (col.maxWidth ?? 600)
    })
    while (remaining > 0.5 && available.length > 0) {
      const addEach = remaining / available.length
      let overflow = 0
      const stillAvail: number[] = []
      for (const i of available) {
        const col = visibleCols[i] as ColEntry
        const maxW = col.maxWidth ?? 600
        const proposed = result[i] + addEach
        if (proposed >= maxW) {
          overflow += proposed - maxW
          result[i] = maxW
        } else {
          result[i] = proposed
          stillAvail.push(i)
        }
      }
      if (stillAvail.length === available.length) break
      remaining = overflow
      available = stillAvail
    }
    return result
  }, [widths, wrapWidth, visibleCols])

  // ── Queries ──────────────────────────────────────────────────────────────────

  const { data: promptList = [] }  = useQuery<PromptInfo[]>({ queryKey: ['prompt-list'], queryFn: getPromptList })
  const { data: tagList = [] }     = useQuery<string[]>({ queryKey: ['trace-tags'], queryFn: getTraceTags })
  const { data: nameList = [] }    = useQuery<{ name: string; count: number }[]>({ queryKey: ['trace-names'], queryFn: getTraceNames })
  const { data: userIdList = [] }  = useQuery<{ user_id: string; count: number }[]>({ queryKey: ['trace-user-ids'], queryFn: getTraceUserIds })

  const traceFilters: TraceFilters = useMemo(() => ({
    status:      filters.statuses.length > 0 ? filters.statuses.join(',') : undefined,
    tags:        filters.tags.length > 0 ? filters.tags.join(',') : undefined,
    names:       filters.names.length > 0 ? filters.names.join(',') : undefined,
    user_ids:    filters.user_ids.length > 0 ? filters.user_ids.join(',') : undefined,
    prompt_name: filters.prompt !== 'all' ? filters.prompt : undefined,
    min_latency: filters.latency.trim() || undefined,
    min_quality: filters.min_quality ? parseFloat(filters.min_quality) : undefined,
    max_quality: filters.max_quality ? parseFloat(filters.max_quality) : undefined,
    min_tokens:  filters.min_tokens ? parseInt(filters.min_tokens) : undefined,
    max_tokens:  filters.max_tokens ? parseInt(filters.max_tokens) : undefined,
    bookmarked:  filters.bookmarked ? true : undefined,
    date_from:   filters.date_from || undefined,
    date_to:     filters.date_to || undefined,
    environment: environment ?? undefined,
  }), [filters, environment])

  const { data: baseTraces = [], isFetching, refetch } = useQuery({
    queryKey: ['traces', traceFilters, page],
    queryFn:  () => getTraces(PAGE_SIZE, { ...traceFilters, offset: (page - 1) * PAGE_SIZE }),
  })

  const hasMore = baseTraces.length === PAGE_SIZE

  // Client-side search
  const traces = useMemo(() => {
    const q = filters.search.trim().toLowerCase()
    if (!q) return baseTraces
    return baseTraces.filter(t =>
      t.id.toLowerCase().includes(q) ||
      inputPreview(t).toLowerCase().includes(q)
    )
  }, [baseTraces, filters.search])

  const sort = useTableSort<TraceItem>({ start_time: t => t.start_time, quality_score: t => t.quality_score, latency: t => t.latency })

  // ── Auto-refresh ─────────────────────────────────────────────────────────────

  useEffect(() => {
    if (refreshMs === 0) return
    const id = setInterval(() => refetch(), refreshMs)
    return () => clearInterval(id)
  }, [refreshMs, refetch])

  // ── Bulk actions ─────────────────────────────────────────────────────────────

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(null), 3000) }

  const handleBulkEval = async () => {
    const ids = Array.from(selected)
    if (!ids.length) return
    try {
      await createEvalRun({ limit: ids.length })
      showToast(`已啟動批次評分（${ids.length} 筆）`)
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['eval-runs'] })
    } catch { showToast('啟動評分失敗') }
  }

  const handleBulkDelete = async () => {
    setDeleting(true)
    try {
      const ids = Array.from(selected)
      await deleteTraces(ids)
      showToast(`已刪除 ${ids.length} 筆`)
      setSelected(new Set())
      setShowDeleteConfirm(false)
      refetch()
    } catch { showToast('刪除失敗') }
    finally { setDeleting(false) }
  }

  const allSelected = traces.length > 0 && traces.every(t => selected.has(t.id))
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(traces.map(t => t.id)))
  const toggleSelect = (id: string) => setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleBookmark = async (trace: TraceItem) => {
    try {
      await updateTraceBookmark(trace.id, !trace.bookmarked)
      refetch()
    } catch {
      showToast('更新收藏失敗')
    }
  }

  const promptNames = useMemo(() => Array.from(new Set(promptList.map((p: PromptInfo) => p.name))).sort(), [promptList])

  // ── Date preset ──────────────────────────────────────────────────────────────

  const applyPreset = (days: number) => {
    const from = days === 0 ? dateAgo(0) : dateAgo(days)
    dispatch({ date_from: from, date_to: '', datePreset: days })
    setPage(1)
  }

  const dateLabel = useMemo(() => {
    if (filters.datePreset !== null) {
      const p = DATE_PRESETS.find(p => p.days === filters.datePreset)
      return p ? p.label : 'Custom'
    }
    return 'Custom range'
  }, [filters.datePreset])

  // ── Cell renderer ─────────────────────────────────────────────────────────────

  function renderCell(c: ColEntry, trace: TraceItem, isErr: boolean, inP: string, outP: string) {
    const isSel = selected.has(trace.id)
    const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
    const mono11: React.CSSProperties = { fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--adm-text-2)' }

    switch (c.key) {
      case 'checkbox':
        return (
          <td key="checkbox" className="adm-col-checkbox" onClick={e => e.stopPropagation()}>
            <input type="checkbox" className="adm-checkbox" checked={isSel}
              onChange={() => toggleSelect(trace.id)} onClick={e => e.stopPropagation()} />
          </td>
        )
      case 'time':
        return <td key="time" className="adm-cell-mono" style={{ fontSize: 11 }}>{fmtTime(trace.start_time)}</td>
      case 'name':
        return <td key="name" style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis' }}>{trace.name || '—'}</td>
      case 'input':
        return <td key="input" style={{ fontSize: 11, color: 'var(--adm-text-2)', overflow: 'hidden', textOverflow: 'ellipsis' }} title={inP}>{inP || '—'}</td>
      case 'output':
        return (
          <td key="output"
            style={{ fontSize: 11, color: isErr ? 'var(--adm-red)' : 'var(--adm-text-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}
            title={isErr ? (trace.error ?? undefined) : outP}>
            {isErr ? (trace.error?.slice(0, 80) ?? 'Error') : (outP || '—')}
          </td>
        )
      case 'obs_statuses':
        return (
          <td key="obs_statuses">
            {trace.obs_status_counts
              ? <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  {Object.entries(trace.obs_status_counts).map(([s, n]) => (
                    <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 2, fontSize: 10, fontWeight: 600, color: s === 'ERROR' ? 'var(--adm-red)' : 'var(--adm-amber)' }}>
                      {s === 'ERROR' ? <OctagonX size={11} /> : s === 'WARNING' ? <TriangleAlert size={11} /> : <SquareTerminal size={11} />}
                      {n}
                    </span>
                  ))}
                </div>
              : <span style={{ fontSize: 10, color: 'var(--adm-text-3)' }}>—</span>}
          </td>
        )
      case 'latency':
        return <td key="latency" style={mono11}>{trace.latency != null ? `${trace.latency.toFixed(1)}s` : '—'}</td>
      case 'tokens': {
        const inp = trace.prompt_tokens ?? 0
        const out = trace.completion_tokens ?? 0
        const total = inp + out
        return (
          <td key="tokens" style={{ fontVariantNumeric: 'tabular-nums' }}>
            {total > 0
              ? <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                  <span style={{ fontSize: 11, color: 'var(--adm-text-2)', fontWeight: 500 }}>{fmt(total)}</span>
                  <span style={{ fontSize: 10, color: 'var(--adm-text-3)' }}>↑{fmt(inp)} ↓{fmt(out)}</span>
                </div>
              : <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>—</span>}
          </td>
        )
      }
      case 'input_tokens':
        return <td key="input_tokens" style={mono11}>{trace.prompt_tokens != null && trace.prompt_tokens > 0 ? fmt(trace.prompt_tokens) : '—'}</td>
      case 'output_tokens':
        return <td key="output_tokens" style={mono11}>{trace.completion_tokens != null && trace.completion_tokens > 0 ? fmt(trace.completion_tokens) : '—'}</td>
      case 'total_cost':
        return <td key="total_cost" style={mono11}>{trace.total_cost != null ? `$${trace.total_cost.toFixed(4)}` : '—'}</td>
      case 'input_cost':
        return <td key="input_cost" style={mono11}>{trace.input_cost != null ? `$${trace.input_cost.toFixed(4)}` : '—'}</td>
      case 'output_cost':
        return <td key="output_cost" style={mono11}>{trace.output_cost != null ? `$${trace.output_cost.toFixed(4)}` : '—'}</td>
      case 'environment':
        return (
          <td key="environment">
            {trace.environment
              ? <span className="adm-badge adm-badge--neutral" style={{ fontSize: 10 }}>{trace.environment}</span>
              : <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>—</span>}
          </td>
        )
      case 'tags':
        return (
          <td key="tags" style={{ overflow: 'hidden' }}>
            {trace.tags?.length
              ? <div style={{ display: 'flex', gap: 3, flexWrap: 'nowrap', overflow: 'hidden' }}>
                  {trace.tags.slice(0, 3).map(t => (
                    <span key={t} className="adm-badge adm-badge--info" style={{ fontSize: 10, whiteSpace: 'nowrap' }}>{t}</span>
                  ))}
                </div>
              : <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>—</span>}
          </td>
        )
      case 'metadata':
        return (
          <td key="metadata" style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>
            {trace.metadata ? `{${Object.keys(trace.metadata).length} keys}` : '—'}
          </td>
        )
      case 'score':
        return <td key="score"><ScoreBar value={trace.quality_score} /></td>
      case 'session':
        return <td key="session" style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--adm-text-2)' }}>{trace.thread_id ? trace.thread_id.slice(-8) : '—'}</td>
      case 'user':
        return <td key="user" style={{ fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--adm-text-2)' }}>{trace.user_id || '—'}</td>
      case 'obs_count':
        return <td key="obs_count" style={mono11}>{trace.observation_count ?? '—'}</td>
      case 'status': {
        const lvlColor = trace.status === 'ERROR' ? 'var(--adm-red)' : trace.status === 'WARNING' ? 'var(--adm-amber)' : trace.status === 'DEBUG' ? 'var(--adm-text-3)' : 'var(--adm-green)'
        const lvlLabel = trace.status === 'ERROR' ? 'Error' : trace.status === 'WARNING' ? 'Warning' : trace.status === 'DEBUG' ? 'Debug' : 'Default'
        return (
          <td key="status">
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: lvlColor, fontSize: 11, fontWeight: 500 }}>
              {trace.status === 'ERROR' ? <OctagonX size={12} /> : trace.status === 'WARNING' ? <TriangleAlert size={12} /> : trace.status === 'DEBUG' ? <SquareTerminal size={12} /> : <CircleCheck size={12} />}
              {lvlLabel}
            </span>
          </td>
        )
      }
      case 'trace_id':
        return <td key="trace_id" className="adm-cell-mono" style={{ fontSize: 10, color: 'var(--adm-text-3)', overflow: 'hidden', textOverflow: 'ellipsis' }} title={trace.id}>{trace.id.slice(-12)}</td>
      case 'actions':
        return (
          <td key="actions" onClick={e => e.stopPropagation()}>
            <div className="adm-action-row">
              <button className="adm-btn-icon" title={trace.bookmarked ? '取消收藏' : '收藏'} onClick={() => toggleBookmark(trace)}>
                <Star size={12} fill={trace.bookmarked ? 'currentColor' : 'none'} />
              </button>
              <button className="adm-btn-icon" title="加入 Dataset" onClick={() => setAddDatasetId(trace.id)}>⊕</button>
            </div>
          </td>
        )
      default:
        return <td key={c.key} />
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────────

  return (
    <div className="adm-page--sidebar-layout">

      {/* ── Filter Sidebar ── */}
      <aside className={`adm-filter-sidebar${filterOpen ? '' : ' adm-filter-sidebar--hidden'}`}>
        <div className="adm-filter-sidebar-header">
          <span>Filters</span>
          {/* clear all */}
          {(filters.statuses.length > 0 || filters.tags.length > 0 || filters.names.length > 0 ||
            filters.user_ids.length > 0 || filters.prompt !== 'all' || filters.latency ||
            filters.min_quality || filters.max_quality || filters.min_tokens || filters.max_tokens ||
            filters.bookmarked) && (
            <button className="adm-btn adm-btn-ghost adm-btn-sm" style={{ fontSize: 11, padding: '1px 6px' }}
              onClick={() => { dispatch('reset'); setPage(1) }}>Clear</button>
          )}
        </div>
        <div className="adm-filter-sidebar-body">

          {/* Trace Name — multi-select */}
          {nameList.length > 0 && (
            <FilterSection title="Trace Name" defaultOpen={false}>
              {nameList.slice(0, 20).map(({ name, count }) => (
                <CheckItem key={name} label={name} count={count}
                  checked={filters.names.includes(name)}
                  onChange={() => {
                    const next = filters.names.includes(name)
                      ? filters.names.filter(x => x !== name)
                      : [...filters.names, name]
                    dispatch({ names: next }); setPage(1)
                  }}
                />
              ))}
            </FilterSection>
          )}

          {/* User ID — multi-select */}
          {userIdList.length > 0 && (
            <FilterSection title="User ID" defaultOpen={false}>
              {userIdList.slice(0, 15).map(({ user_id, count }) => (
                <CheckItem key={user_id} label={user_id} count={count}
                  checked={filters.user_ids.includes(user_id)}
                  onChange={() => {
                    const next = filters.user_ids.includes(user_id)
                      ? filters.user_ids.filter(x => x !== user_id)
                      : [...filters.user_ids, user_id]
                    dispatch({ user_ids: next }); setPage(1)
                  }}
                />
              ))}
            </FilterSection>
          )}

          {/* Status - multi-select */}
          <FilterSection title="Status">
            {(['DEFAULT', 'WARNING', 'ERROR', 'DEBUG'] as const).map(status => (
              <CheckItem key={status} label={status}
                checked={filters.statuses.includes(status)}
                onChange={() => {
                  const next = filters.statuses.includes(status)
                    ? filters.statuses.filter(x => x !== status)
                    : [...filters.statuses, status]
                  dispatch({ statuses: next }); setPage(1)
                }}
              />
            ))}
          </FilterSection>

          <FilterSection title="Bookmark" defaultOpen={false}>
            <CheckItem
              label="Bookmarked"
              checked={filters.bookmarked}
              onChange={() => { dispatch({ bookmarked: !filters.bookmarked }); setPage(1) }}
            />
          </FilterSection>

          {/* Prompt */}
          <FilterSection title="Prompt" defaultOpen={false}>
            <PromptDropdown
              value={filters.prompt}
              options={promptNames}
              onChange={v => { dispatch({ prompt: v }); setPage(1) }}
            />
          </FilterSection>

          {/* Tags — multi-select */}
          {tagList.length > 0 && (
            <FilterSection title="Tags" defaultOpen={false}>
              {tagList.map(t => (
                <CheckItem key={t} label={t}
                  checked={filters.tags.includes(t)}
                  onChange={() => {
                    const next = filters.tags.includes(t)
                      ? filters.tags.filter(x => x !== t)
                      : [...filters.tags, t]
                    dispatch({ tags: next }); setPage(1)
                  }}
                />
              ))}
            </FilterSection>
          )}

          {/* Score Range */}
          <FilterSection title="Score" defaultOpen={false}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input className="adm-tb-input" placeholder="Min (0)" style={{ flex: 1 }}
                value={filters.min_quality}
                onChange={e => { dispatch({ min_quality: e.target.value }); setPage(1) }} />
              <input className="adm-tb-input" placeholder="Max (5)" style={{ flex: 1 }}
                value={filters.max_quality}
                onChange={e => { dispatch({ max_quality: e.target.value }); setPage(1) }} />
            </div>
          </FilterSection>

          {/* Token Range */}
          <FilterSection title="Total Tokens" defaultOpen={false}>
            <div style={{ display: 'flex', gap: 6 }}>
              <input className="adm-tb-input" placeholder="Min" style={{ flex: 1 }}
                value={filters.min_tokens}
                onChange={e => { dispatch({ min_tokens: e.target.value }); setPage(1) }} />
              <input className="adm-tb-input" placeholder="Max" style={{ flex: 1 }}
                value={filters.max_tokens}
                onChange={e => { dispatch({ max_tokens: e.target.value }); setPage(1) }} />
            </div>
          </FilterSection>

          {/* Latency */}
          <FilterSection title="Latency" defaultOpen={false}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>≥</span>
              <input className="adm-tb-input" style={{ flex: 1 }} placeholder="seconds"
                value={filters.latency} onChange={e => { dispatch({ latency: e.target.value }); setPage(1) }} />
              <span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>s</span>
            </div>
          </FilterSection>

        </div>
      </aside>

      {/* ── Main ── */}
      <div className="adm-page--sidebar-content">

        {/* Toolbar */}
        <div className="adm-traces-toolbar">
          {/* Filter toggle */}
          <button className="adm-tb-btn" onClick={toggleFilterOpen}>
            {filterOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}
            Filters
          </button>

          {/* Search */}
          <div className="adm-search-group">
            <span className="adm-search-icon"><Search size={14} /></span>
            <input placeholder="Search…" value={filters.search}
              onChange={e => dispatch({ search: e.target.value })} />
          </div>

          {/* Date range */}
          <div style={{ position: 'relative' }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setShowDateMenu(false) }}>
            <button className="adm-tb-btn" onClick={() => setShowDateMenu(m => !m)}>
              <span className="adm-toolbar-date-badge">
                {filters.datePreset === 0 ? 'Today' : filters.datePreset != null ? `${filters.datePreset}d` : '?'}
              </span>
              {dateLabel}
              <ChevronDown size={14} style={{ opacity: 0.5 }} />
            </button>
            {showDateMenu && (
              <div className="adm-dropdown" style={{ position: 'absolute', top: '100%', left: 0, zIndex: 100, marginTop: 4, minWidth: 130 }}>
                {DATE_PRESETS.map(p => (
                  <button key={p.days} className="adm-dropdown-item"
                    onClick={() => { applyPreset(p.days); setShowDateMenu(false) }}>
                    {p.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Refresh split button */}
          <div className="adm-tb-split">
            <button className="adm-tb-icon" title="Refresh now" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw size={14} className={isFetching ? 'adm-spinner' : ''} style={isFetching ? { animation: 'adm-spin 600ms linear infinite' } : undefined} />
            </button>
            <div style={{ position: 'relative' }} onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setShowRefreshMenu(false) }}>
              <button className="adm-tb-btn" onClick={() => setShowRefreshMenu(m => !m)}>
                <ChevronDown size={13} style={{ opacity: 0.5 }} />
                {refreshMs === 0 ? 'Off' : REFRESH_OPTIONS.find(o => o.ms === refreshMs)?.label ?? '?'}
              </button>
              {showRefreshMenu && (
                <div className="adm-dropdown" style={{ position: 'absolute', top: '100%', right: 0, zIndex: 100, marginTop: 4, minWidth: 80 }}>
                  {REFRESH_OPTIONS.map(o => (
                    <button key={o.ms} className="adm-dropdown-item"
                      onClick={() => { setRefreshMs(o.ms); setShowRefreshMenu(false) }}>
                      {o.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          <span className="adm-toolbar-spacer" />

          {/* Column visibility */}
          <button className="adm-tb-btn" onClick={() => setColsOpen(o => !o)}>
            <Columns size={14} />
            Columns
            <span className="adm-tb-count">
              {visibleCols.filter(c => !c.required && c.key !== 'checkbox' && c.key !== 'actions').length}/{ALL_COLS.filter(c => !c.required && c.key !== 'checkbox' && c.key !== 'actions').length}
            </span>
          </button>
        </div>

        {/* Bulk action bar */}
        {selected.size > 0 && (
          <BulkActionBar
            count={selected.size}
            actions={[
              { label: '▶ Run Eval', onClick: handleBulkEval, variant: 'primary' },
              { label: '🗑 Delete', onClick: () => setShowDeleteConfirm(true), variant: 'danger' },
            ]}
            onClear={() => setSelected(new Set())}
          />
        )}

        {/* Table */}
        <div className="adm-table-wrap" ref={tableWrapRef}>
          <table className="adm-table" style={{ tableLayout: 'fixed', width: Math.max(displayWidths.reduce((a, b) => a + b, 0), wrapWidth || 0), minWidth: 400 }}>
            <colgroup>{displayWidths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
            <thead>
              <tr>
                {visibleCols.map((c, i) => {
                  const isLast = i === visibleCols.length - 1
                  if (c.key === 'checkbox') return (
                    <th key="checkbox" className="adm-col-checkbox">
                      <input type="checkbox" className="adm-checkbox" checked={allSelected} onChange={toggleAll} />
                    </th>
                  )
                  const D = !isLast && <span {...div(i)} />
                  if (c.key === 'time')        return <th key="time" {...sort.th('start_time')}>Timestamp{sort.ind('start_time')}{D}</th>
                  if (c.key === 'name')        return <th key="name">Name{D}</th>
                  if (c.key === 'input')       return <th key="input">Input{D}</th>
                  if (c.key === 'output')      return <th key="output">Output{D}</th>
                  if (c.key === 'obs_statuses')  return <th key="obs_statuses">Obs. Statuses{D}</th>
                  if (c.key === 'latency')     return <th key="latency" {...sort.th('latency')}>Latency{sort.ind('latency')}{D}</th>
                  if (c.key === 'tokens')        return <th key="tokens">Tokens{D}</th>
                  if (c.key === 'input_tokens')  return <th key="input_tokens">In Tokens{D}</th>
                  if (c.key === 'output_tokens') return <th key="output_tokens">Out Tokens{D}</th>
                  if (c.key === 'total_cost')    return <th key="total_cost">Cost{D}</th>
                  if (c.key === 'input_cost')    return <th key="input_cost">In Cost{D}</th>
                  if (c.key === 'output_cost')   return <th key="output_cost">Out Cost{D}</th>
                  if (c.key === 'environment') return <th key="environment">Env{D}</th>
                  if (c.key === 'tags')        return <th key="tags">Tags{D}</th>
                  if (c.key === 'metadata')    return <th key="metadata">Metadata{D}</th>
                  if (c.key === 'score')       return <th key="score" {...sort.th('quality_score')}>Score{sort.ind('quality_score')}{D}</th>
                  if (c.key === 'session')     return <th key="session">Thread{D}</th>
                  if (c.key === 'user')        return <th key="user">User{D}</th>
                  if (c.key === 'obs_count')   return <th key="obs_count">Observations{D}</th>
                  if (c.key === 'trace_id')    return <th key="trace_id">Trace ID{D}</th>
                  if (c.key === 'status')      return <th key="status">Status{D}</th>
                  if (c.key === 'actions')     return <th key="actions" />
                  return <th key={c.key}>{c.label}</th>
                })}
              </tr>
            </thead>
            <tbody>
              {isFetching && baseTraces.length === 0 ? (
                <tr><td colSpan={visibleCols.length} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
              ) : traces.length === 0 ? (
                <tr><td colSpan={visibleCols.length} className="adm-table-empty">沒有符合條件的 trace</td></tr>
              ) : sort.apply(traces).map(trace => {
                const isErr = trace.status === 'ERROR'
                const inP   = inputPreview(trace)
                const outP  = outputPreview(trace)
                return (
                  <tr key={trace.id}
                    className={`adm-row--clickable${selected.has(trace.id) ? ' adm-row--selected' : ''}${isErr ? ' adm-row--danger' : ''}`}
                    onClick={() => setDrawerTraceId(trace.id)}
                  >
                    {visibleCols.map(c => renderCell(c, trace, isErr, inP, outP))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        <TablePagination
          page={page}
          pageCount={page + (hasMore ? 1 : 0)}
          pageSize={PAGE_SIZE}
          onPageChange={p => { setPage(p); setSelected(new Set()) }}
          showFirstLast={false}
        />

      </div>

      {/* ── Column Visibility Sidebar (right bubble) ── */}
      <aside className={`adm-col-vis-sidebar${colsOpen ? '' : ' adm-col-vis-sidebar--hidden'}`}>
        <div className="adm-filter-sidebar-header">
          <span>Columns</span>
          <button className="adm-btn-icon" onClick={() => setColsOpen(false)}><X size={13} /></button>
        </div>
        <div className="adm-filter-sidebar-body">
          <div style={{ padding: '6px 10px 8px', borderBottom: '1px solid var(--adm-border)' }}>
            <button className="adm-tb-btn" style={{ width: '100%' }}
              onClick={() => { resetCols(); saveColOrder(NON_REQUIRED_KEYS) }}>
              Restore Defaults
            </button>
          </div>
          <div className="adm-filter-section-body">
            {colOrder.map(key => {
              const c = ALL_COLS.find(col => col.key === key)
              if (!c) return null
              const isDragging  = dragColKey === c.key
              const isDropTarget = dragOverColKey === c.key && !isDragging
              return (
                <label key={c.key} className="adm-filter-check-item"
                  onMouseEnter={() => dragColKey && setDragOverColKey(c.key)}
                  onDragStart={e => e.preventDefault()}
                  style={{
                    opacity: isDragging ? 0.4 : 1,
                    borderTop: isDropTarget ? '2px solid var(--adm-blue)' : undefined,
                    userSelect: 'none',
                  }}
                >
                  <GripVertical size={12}
                    onMouseDown={e => handleGripMouseDown(e, c.key)}
                    style={{ color: 'var(--adm-text-3)', flexShrink: 0, cursor: 'grab' }} />
                  <input type="checkbox" checked={visible[c.key] ?? true} onChange={() => toggleCol(c.key)} />
                  <span style={{ flex: 1 }}>{c.label}</span>
                </label>
              )
            })}
          </div>
        </div>
      </aside>

      {/* ── Overlays ── */}
      <DrawerPanel open={!!drawerTraceId} title={drawerTraceId ? `Trace · ${drawerTraceId.slice(-12)}…` : ''} onClose={() => setDrawerTraceId(null)} width={620}>
        {drawerTraceId && <TraceDetailContent traceId={drawerTraceId} compact />}
      </DrawerPanel>

      {addDatasetId && (
        <AddToDatasetModal traceId={addDatasetId} onClose={() => setAddDatasetId(null)}
          onDone={() => { setAddDatasetId(null); showToast('已加入 Dataset') }} />
      )}

      {showDeleteConfirm && (
        <DeleteConfirmModal count={selected.size} loading={deleting}
          onClose={() => setShowDeleteConfirm(false)} onConfirm={handleBulkDelete} />
      )}

      {toast && <div className="adm-toast-wrap"><div className="adm-toast">{toast}</div></div>}
    </div>
  )
}
