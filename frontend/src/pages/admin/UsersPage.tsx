import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getUsers, type UserItem } from '../../api'
import './UsersPage.css'

function formatDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function formatTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function QualityBadge({ score }: { score: number | null }) {
  if (score == null) return <span className="u-empty">—</span>
  const color = score >= 4 ? '#16a34a' : score >= 3 ? '#d97706' : '#dc2626'
  return <span style={{ color, fontWeight: 600 }}>{score.toFixed(1)}</span>
}

export default function UsersPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const limit = 50

  const { data, isLoading } = useQuery({
    queryKey: ['users', search, page],
    queryFn: () => getUsers(limit, {
      search: search || undefined,
      offset: page * limit,
    }),
  })

  const users = data?.users ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / limit)

  return (
    <div className="u-page">
      <div className="u-header">
        <div>
          <h1 className="u-title">Users</h1>
          <p className="u-subtitle">{total} users</p>
        </div>
        <input
          className="u-search"
          placeholder="Search user ID…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0) }}
        />
      </div>

      {users.length === 0 && !isLoading && (
        <div className="u-empty-state">
          <p>No users found.</p>
          <p className="u-empty-hint">
            Users appear here once <code>user_id</code> is set on incoming requests.
          </p>
        </div>
      )}

      {(users.length > 0 || isLoading) && (
        <div className="u-table-wrapper">
          <table className="u-table">
            <thead>
              <tr>
                <th>User ID</th>
                <th>First Event</th>
                <th>Last Event</th>
                <th>Sessions</th>
                <th>Total Events</th>
                <th>Total Tokens</th>
                <th>Avg Quality</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={7} className="u-loading">Loading…</td></tr>
              )}
              {users.map((u: UserItem) => (
                <tr
                  key={u.user_id}
                  className="u-row"
                  onClick={() => navigate(`/admin/users/${encodeURIComponent(u.user_id)}`)}
                >
                  <td className="u-td-id">{u.user_id}</td>
                  <td className="u-td-date">{formatDate(u.first_event)}</td>
                  <td className="u-td-date">{formatDate(u.last_event)}</td>
                  <td className="u-td-num">{u.session_count}</td>
                  <td className="u-td-num">{u.trace_count}</td>
                  <td className="u-td-num" title={`in: ${u.input_tokens.toLocaleString()} / out: ${u.output_tokens.toLocaleString()}`}>
                    {formatTokens(u.total_tokens)}
                  </td>
                  <td className="u-td-num"><QualityBadge score={u.avg_quality_score} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <div className="u-pagination">
          <button className="u-page-btn" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</button>
          <span className="u-page-info">{page + 1} / {totalPages}</span>
          <button className="u-page-btn" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}
    </div>
  )
}
