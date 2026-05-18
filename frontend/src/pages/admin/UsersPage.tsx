import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getUsers, type UserItem } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { useAdminStore } from '../../stores/adminStore'
import { ScoreBar } from '../../components/admin/ScoreBar'

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function fmtTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 200, resizable: true  },  // 0 User ID
  { width: 120, resizable: false },  // 1 First Event
  { width: 120, resizable: false },  // 2 Last Event
  { width: 70,  resizable: false },  // 3 Sessions
  { width: 70,  resizable: false },  // 4 Traces
  { width: 80,  resizable: false },  // 5 Tokens
  { width: 130, resizable: false },  // 6 Avg Quality
]

export default function UsersPage() {
  const navigate = useNavigate()
  const { environment } = useAdminStore()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const [limit, setLimit] = useState(50)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-users-col-widths')
  const sort = useTableSort<UserItem>({
    first_event:       u => u.first_event,
    last_event:        u => u.last_event,
    session_count:     u => u.session_count,
    trace_count:       u => u.trace_count,
    total_tokens:      u => u.total_tokens,
    avg_quality_score: u => u.avg_quality_score,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['users', search, page, environment],
    queryFn: () => getUsers(limit, { search: search || undefined, environment: environment ?? undefined, offset: page * limit }),
  })
  const users = data?.users ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / limit)

  return (
    <div>
      <div className="adm-filters" style={{ marginBottom: 16 }}>
        <input className="adm-filter-input" placeholder="Search user ID…"
          value={search} onChange={e => { setSearch(e.target.value); setPage(0) }} />
        <span className="adm-filter-spacer" />
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{total} users</span>
      </div>

      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th>User ID<span {...div(0)} /></th>
              <th {...sort.th('first_event')}>First Event{sort.ind('first_event')}<span {...div(1)} /></th>
              <th {...sort.th('last_event')}>Last Event{sort.ind('last_event')}<span {...div(2)} /></th>
              <th {...sort.th('session_count')}>Sessions{sort.ind('session_count')}<span {...div(3)} /></th>
              <th {...sort.th('trace_count')}>Traces{sort.ind('trace_count')}<span {...div(4)} /></th>
              <th {...sort.th('total_tokens')}>Tokens{sort.ind('total_tokens')}<span {...div(5)} /></th>
              <th {...sort.th('avg_quality_score')}>Avg Quality{sort.ind('avg_quality_score')}</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={7} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : users.length === 0 ? (
              <tr><td colSpan={7} className="adm-table-empty">No users found. Users appear once <code>user_id</code> is set on requests.</td></tr>
            ) : sort.apply(users).map((u: UserItem) => (
              <tr key={u.user_id} className="adm-row--clickable" onClick={() => navigate(`/admin/users/${encodeURIComponent(u.user_id)}`)}>
                <td style={{ fontWeight: 500 }}>{u.user_id}</td>
                <td className="adm-cell-mono">{fmtDate(u.first_event)}</td>
                <td className="adm-cell-mono">{fmtDate(u.last_event)}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{u.session_count}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{u.trace_count}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12 }} title={`in: ${u.input_tokens.toLocaleString()} / out: ${u.output_tokens.toLocaleString()}`}>{fmtTokens(u.total_tokens)}</td>
                <td><ScoreBar value={u.avg_quality_score} /></td>
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
    </div>
  )
}
