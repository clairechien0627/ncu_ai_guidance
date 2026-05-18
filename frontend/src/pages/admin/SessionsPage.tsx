import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getSessions, type SessionFilters, type SessionItem } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { useAdminStore } from '../../stores/adminStore'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { DrawerPanel } from '../../components/admin/DrawerPanel'
import { SessionDetailContent } from '../../components/admin/SessionDetailContent'

const ROUTE_INTENT_OPTIONS = ['all', 'research', 'retrieval', 'chat', 'question', 'evaluation']

function fmtDuration(s: number | null) {
  if (s === null) return '—'
  if (s < 60) return `${s.toFixed(1)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return m > 0 ? `${h}h ${m}m` : `${h}h`
}
function fmtTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}
function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 180, resizable: true, minWidth:150, maxWidth:210  },  // 0 Thread ID
  { width: 100, resizable: false },  // 1 Task Type
  { width: 110, resizable: false },  // 2 Created
  { width: 100,  resizable: false },  // 3 Duration
  { width: 80,  resizable: false },  // 4 Traces
  { width: 80,  resizable: false },  // 5 Tokens
  { width: 130, resizable: false },  // 6 Avg Quality
  { width: 160, resizable: true, minWidth:120, maxWidth:200  },  // 7 Users
]

export default function SessionsPage() {
  const { environment } = useAdminStore()
  const [filters, setFilters] = useState<SessionFilters>({})
  const [routeIntent, setRouteIntent] = useState('all')
  const [page, setPage] = useState(0)
  const [drawerThreadId, setDrawerThreadId] = useState<string | null>(null)
  const [limit, setLimit] = useState(50)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-sessions-col-widths')
  const sort = useTableSort<SessionItem>({
    created_at:        s => s.created_at,
    duration_seconds:  s => s.duration_seconds,
    trace_count:       s => s.trace_count,
    total_tokens:      s => s.total_tokens,
    avg_quality_score: s => s.avg_quality_score,
  })

  const activeFilters: SessionFilters = {
    ...filters,
    route_intent: routeIntent !== 'all' ? routeIntent : undefined,
    environment: environment ?? undefined,
    offset: page * limit,
  }

  const { data, isLoading } = useQuery({
    queryKey: ['sessions', activeFilters],
    queryFn: () => getSessions(limit, activeFilters),
  })
  const sessions = data?.sessions ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / limit)

  return (
    <div>
      {/* Filters */}
      <div className="adm-filters">
        <select className="adm-filter-select" value={routeIntent} onChange={e => { setRouteIntent(e.target.value); setPage(0) }}>
          {ROUTE_INTENT_OPTIONS.map(o => <option key={o} value={o}>{o === 'all' ? 'All task types' : o}</option>)}
        </select>
        <input className="adm-filter-input" placeholder="Date from (YYYY-MM-DD)"
          value={filters.date_from ?? ''}
          onChange={e => { setFilters(f => ({ ...f, date_from: e.target.value || undefined })); setPage(0) }} />
        <input className="adm-filter-input" placeholder="Date to (YYYY-MM-DD)"
          value={filters.date_to ?? ''}
          onChange={e => { setFilters(f => ({ ...f, date_to: e.target.value || undefined })); setPage(0) }} />
        <span className="adm-filter-spacer" />
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{total} sessions</span>
      </div>

      {/* Table */}
      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th>Thread ID<span {...div(0)} /></th>
              <th>Task Type<span {...div(1)} /></th>
              <th {...sort.th('created_at')}>Created{sort.ind('created_at')}<span {...div(2)} /></th>
              <th {...sort.th('duration_seconds')}>Duration{sort.ind('duration_seconds')}<span {...div(3)} /></th>
              <th {...sort.th('trace_count')}>Traces{sort.ind('trace_count')}<span {...div(4)} /></th>
              <th {...sort.th('total_tokens')}>Tokens{sort.ind('total_tokens')}<span {...div(5)} /></th>
              <th {...sort.th('avg_quality_score')}>Avg Quality{sort.ind('avg_quality_score')}<span {...div(6)} /></th>
              <th>Users</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={8} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : sessions.length === 0 ? (
              <tr><td colSpan={8} className="adm-table-empty">No sessions found</td></tr>
            ) : sort.apply(sessions).map((s: SessionItem) => (
              <tr key={s.thread_id} className="adm-row--clickable" onClick={() => setDrawerThreadId(s.thread_id)}>
                <td className="adm-cell-mono" title={s.thread_id}>{s.thread_id.length > 24 ? s.thread_id.slice(0, 24) + '…' : s.thread_id}</td>
                <td>{s.task_type ? <span className="adm-badge adm-badge--info">{s.task_type}</span> : '—'}</td>
                <td className="adm-cell-mono">{fmtDate(s.created_at)}</td>
                <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{fmtDuration(s.duration_seconds)}</td>
                <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{s.trace_count}</td>
                <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }} title={`in: ${s.input_tokens.toLocaleString()} / out: ${s.output_tokens.toLocaleString()}`}>{fmtTokens(s.total_tokens)}</td>
                <td><ScoreBar value={s.avg_quality_score} /></td>
                <td style={{ fontSize: 11, color: 'var(--adm-text-2)' }}>{s.user_ids?.length > 0 ? s.user_ids.join(', ') : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <TablePagination
        page={page + 1}
        pageCount={totalPages || 1}
        pageSize={limit}
        total={total}
        onPageChange={p => setPage(p - 1)}
        onPageSizeChange={n => { setLimit(n); setPage(0) }}
      />

      <DrawerPanel
        open={!!drawerThreadId}
        title={drawerThreadId ? `Session · ${drawerThreadId.slice(0, 20)}…` : ''}
        onClose={() => setDrawerThreadId(null)}
        width={600}
      >
        {drawerThreadId && <SessionDetailContent threadId={drawerThreadId} />}
      </DrawerPanel>
    </div>
  )
}
