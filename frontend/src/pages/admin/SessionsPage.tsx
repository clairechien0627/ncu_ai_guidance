import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getSessions, type SessionFilters, type SessionItem } from '../../api'
import './SessionsPage.css'

const ROUTE_INTENT_OPTIONS = ['all', 'research', 'retrieval', 'chat', 'question', 'evaluation']

function formatDuration(seconds: number | null): string {
  if (seconds === null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${s}s`
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function QualityBadge({ score }: { score: number | null }) {
  if (score === null) return <span className="sessions-quality-empty">—</span>
  const color = score >= 4 ? '#16a34a' : score >= 3 ? '#d97706' : '#dc2626'
  return (
    <span className="sessions-quality-badge" style={{ color }}>
      {score.toFixed(1)}
    </span>
  )
}

function TaskTypeBadge({ type }: { type: string | null }) {
  if (!type) return <span className="sessions-type-badge sessions-type-badge--unknown">—</span>
  const cls = `sessions-type-badge sessions-type-badge--${type}`
  return <span className={cls}>{type}</span>
}

export default function SessionsPage() {
  const navigate = useNavigate()
  const [filters, setFilters] = useState<SessionFilters>({})
  const [routeIntent, setRouteIntent] = useState('all')
  const [page, setPage] = useState(0)
  const limit = 50

  const activeFilters: SessionFilters = {
    ...filters,
    route_intent: routeIntent !== 'all' ? routeIntent : undefined,
    offset: page * limit,
  }

  const { data, isLoading } = useQuery({
    queryKey: ['sessions', activeFilters],
    queryFn: () => getSessions(limit, activeFilters),
  })

  const sessions = data?.sessions ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / limit)

  const handleRowClick = (s: SessionItem) => {
    navigate(`/admin/sessions/${encodeURIComponent(s.thread_id)}`)
  }

  return (
    <div className="sessions-page">
      <div className="sessions-header">
        <div>
          <h1 className="sessions-title">Sessions</h1>
          <p className="sessions-subtitle">{total} sessions</p>
        </div>
      </div>

      {/* Filters */}
      <div className="sessions-filters">
        <select
          className="sessions-filter-select"
          value={routeIntent}
          onChange={(e) => { setRouteIntent(e.target.value); setPage(0) }}
        >
          {ROUTE_INTENT_OPTIONS.map((o) => (
            <option key={o} value={o}>{o === 'all' ? 'All task types' : o}</option>
          ))}
        </select>
        <input
          className="sessions-filter-input"
          placeholder="Date from (YYYY-MM-DD)"
          value={filters.date_from ?? ''}
          onChange={(e) => { setFilters(f => ({ ...f, date_from: e.target.value || undefined })); setPage(0) }}
        />
        <input
          className="sessions-filter-input"
          placeholder="Date to (YYYY-MM-DD)"
          value={filters.date_to ?? ''}
          onChange={(e) => { setFilters(f => ({ ...f, date_to: e.target.value || undefined })); setPage(0) }}
        />
      </div>

      {/* Table */}
      <div className="sessions-table-wrapper">
        <table className="sessions-table">
          <thead>
            <tr>
              <th>Thread ID</th>
              <th>Task Type</th>
              <th>Created At</th>
              <th>Duration</th>
              <th>Traces</th>
              <th>Tokens</th>
              <th>Avg Quality</th>
              <th>Users</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={8} className="sessions-loading">Loading…</td>
              </tr>
            )}
            {!isLoading && sessions.length === 0 && (
              <tr>
                <td colSpan={8} className="sessions-empty">No sessions found</td>
              </tr>
            )}
            {sessions.map((s) => (
              <tr key={s.thread_id} className="sessions-row" onClick={() => handleRowClick(s)}>
                <td className="sessions-td-id" title={s.thread_id}>
                  {s.thread_id.length > 24 ? s.thread_id.slice(0, 24) + '…' : s.thread_id}
                </td>
                <td><TaskTypeBadge type={s.task_type} /></td>
                <td className="sessions-td-date">{formatDate(s.created_at)}</td>
                <td className="sessions-td-num">{formatDuration(s.duration_seconds)}</td>
                <td className="sessions-td-num">{s.trace_count}</td>
                <td className="sessions-td-num" title={`in: ${s.input_tokens.toLocaleString()} / out: ${s.output_tokens.toLocaleString()}`}>
                  {formatTokens(s.total_tokens)}
                </td>
                <td className="sessions-td-num"><QualityBadge score={s.avg_quality_score} /></td>
                <td className="sessions-td-users">
                  {s.user_ids.length > 0 ? s.user_ids.join(', ') : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="sessions-pagination">
          <button
            className="sessions-page-btn"
            disabled={page === 0}
            onClick={() => setPage((p) => p - 1)}
          >
            ← Prev
          </button>
          <span className="sessions-page-info">
            {page + 1} / {totalPages}
          </span>
          <button
            className="sessions-page-btn"
            disabled={page >= totalPages - 1}
            onClick={() => setPage((p) => p + 1)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}
