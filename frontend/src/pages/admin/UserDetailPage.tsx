import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getUserDetail } from '../../api'
import './UserDetailPage.css'

function formatDate(iso: string | null | undefined) {
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

function IntentBadge({ type }: { type?: string | null }) {
  if (!type) return null
  return <span className={`ud-badge ud-badge--${type}`}>{type}</span>
}

export default function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>()
  const navigate = useNavigate()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['user-detail', userId],
    queryFn: () => getUserDetail(userId!),
    enabled: !!userId,
  })

  if (isLoading) return <div className="ud-loading">Loading user…</div>
  if (isError || !data) return <div className="ud-error">User not found</div>

  const sessions: Array<{
    thread_id: string
    task_type: string | null
    created_at: string | null
    trace_count: number
    total_tokens: number
    avg_quality_score: number | null
  }> = data.sessions ?? []

  return (
    <div className="ud-page">
      {/* Header */}
      <div className="ud-header">
        <div className="ud-header-left">
          <button className="ud-back-btn" onClick={() => navigate('/admin/users')}>
            ← Users
          </button>
          <div>
            <h1 className="ud-user-id">{userId}</h1>
            <p className="ud-subtitle">
              {data.session_count} sessions · {data.trace_count} traces
            </p>
          </div>
        </div>
        <div className="ud-stats">
          <div className="ud-stat">
            <span className="ud-stat-label">First Event</span>
            <span className="ud-stat-value">{formatDate(data.first_event)}</span>
          </div>
          <div className="ud-stat">
            <span className="ud-stat-label">Last Event</span>
            <span className="ud-stat-value">{formatDate(data.last_event)}</span>
          </div>
          <div className="ud-stat">
            <span className="ud-stat-label">Total Tokens</span>
            <span className="ud-stat-value">{formatTokens(data.total_tokens)}</span>
          </div>
          {data.avg_quality_score != null && (
            <div className="ud-stat">
              <span className="ud-stat-label">Avg Quality</span>
              <span className="ud-stat-value">{data.avg_quality_score.toFixed(1)}</span>
            </div>
          )}
        </div>
      </div>

      {/* Sessions table */}
      <div className="ud-section">
        <h2 className="ud-section-title">Sessions</h2>
        <div className="ud-table-wrapper">
          <table className="ud-table">
            <thead>
              <tr>
                <th>Thread ID</th>
                <th>Task Type</th>
                <th>Created At</th>
                <th>Traces</th>
                <th>Tokens</th>
                <th>Avg Quality</th>
              </tr>
            </thead>
            <tbody>
              {sessions.length === 0 && (
                <tr><td colSpan={6} className="ud-empty">No sessions</td></tr>
              )}
              {sessions.map((s) => (
                <tr
                  key={s.thread_id}
                  className="ud-row"
                  onClick={() => navigate(`/admin/sessions/${encodeURIComponent(s.thread_id)}`)}
                >
                  <td className="ud-td-id" title={s.thread_id}>
                    {s.thread_id.length > 28 ? s.thread_id.slice(0, 28) + '…' : s.thread_id}
                  </td>
                  <td><IntentBadge type={s.task_type} /></td>
                  <td className="ud-td-date">{formatDate(s.created_at)}</td>
                  <td className="ud-td-num">{s.trace_count}</td>
                  <td className="ud-td-num">{formatTokens(s.total_tokens)}</td>
                  <td className="ud-td-num">
                    {s.avg_quality_score != null
                      ? <span style={{ fontWeight: 600 }}>{s.avg_quality_score.toFixed(1)}</span>
                      : <span style={{ color: '#d1d5db' }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
