import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getPromptList2, type PromptSummary } from '../../api'
import './PromptsPage.css'

function formatDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function QualityBadge({ score }: { score: number | null }) {
  if (score == null) return <span style={{ color: '#d1d5db' }}>—</span>
  const color = score >= 4 ? '#16a34a' : score >= 3 ? '#d97706' : '#dc2626'
  return <span style={{ color, fontWeight: 600 }}>{score.toFixed(1)}</span>
}

export default function PromptsPage() {
  const navigate = useNavigate()
  const { data: prompts = [], isLoading } = useQuery({
    queryKey: ['prompts-list'],
    queryFn: getPromptList2,
  })

  return (
    <div className="pp-page">
      <div className="pp-header">
        <h1 className="pp-title">Prompts</h1>
        <p className="pp-subtitle">{prompts.length} prompts</p>
      </div>

      <div className="pp-table-wrapper">
        <table className="pp-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Version</th>
              <th>Words</th>
              <th>Versions</th>
              <th>Last Synced</th>
              <th>Avg Quality</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6} className="pp-loading">Loading…</td></tr>
            )}
            {!isLoading && prompts.length === 0 && (
              <tr>
                <td colSpan={6} className="pp-empty">
                  No versions synced yet. Run <code>python scripts/prompts/sync_prompts.py</code> to populate.
                </td>
              </tr>
            )}
            {prompts.map((p: PromptSummary) => (
              <tr
                key={p.name}
                className="pp-row"
                onClick={() => navigate(`/admin/prompts/${encodeURIComponent(p.name)}`)}
              >
                <td className="pp-td-name">{p.name}</td>
                <td className="pp-td-hash">
                  {p.current_hash ? <code>#{p.current_hash}</code> : <span style={{ color: '#d1d5db' }}>—</span>}
                </td>
                <td className="pp-td-num">{p.word_count ?? '—'}</td>
                <td className="pp-td-num">
                  <span className={p.version_count > 0 ? 'pp-version-badge' : ''}>
                    {p.version_count > 0 ? p.version_count : '—'}
                  </span>
                </td>
                <td className="pp-td-date">{formatDate(p.synced_at)}</td>
                <td className="pp-td-num"><QualityBadge score={p.avg_quality_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
