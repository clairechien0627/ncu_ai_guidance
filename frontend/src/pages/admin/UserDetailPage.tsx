import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getUserDetail } from '../../api'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function fmtTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export default function UserDetailPage() {
  const { userId } = useParams<{ userId: string }>()
  const navigate = useNavigate()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['user-detail', userId],
    queryFn: () => getUserDetail(userId!),
    enabled: !!userId,
  })

  if (isLoading) return <div style={{ padding: 32, color: 'var(--adm-text-3)' }}>載入中…</div>
  if (isError || !data) return <div style={{ padding: 32, color: 'var(--adm-red)' }}>User not found</div>

  const sessions: any[] = data.sessions ?? []

  return (
    <div>
      <PageHeader
        title={userId!}
        crumbs={[{ label: 'Users', to: '/admin/users' }, { label: userId! }]}
        subtitle={`${data.session_count} sessions · ${data.trace_count} traces`}
      />

      {/* Sessions table */}
      <div className="adm-section-label" style={{ marginBottom: 8 }}>Sessions</div>
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead>
            <tr>
              <th>Thread ID</th>
              <th className="adm-col-time">Created</th>
              <th style={{ width: 70 }}>Traces</th>
              <th style={{ width: 80 }}>Tokens</th>
              <th className="adm-col-score">Avg Quality</th>
            </tr>
          </thead>
          <tbody>
            {sessions.length === 0 ? (
              <tr><td colSpan={5} className="adm-table-empty">No sessions</td></tr>
            ) : sessions.map((s: any) => (
              <tr key={s.thread_id} className="adm-row--clickable" onClick={() => navigate(`/admin/sessions/${encodeURIComponent(s.thread_id)}`)}>
                <td className="adm-cell-mono" title={s.thread_id}>{s.thread_id.length > 28 ? s.thread_id.slice(0, 28) + '…' : s.thread_id}</td>
                <td className="adm-cell-mono">{fmtDate(s.created_at)}</td>
                <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{s.trace_count}</td>
                <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{fmtTokens(s.total_tokens)}</td>
                <td><ScoreBar value={s.avg_quality_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
