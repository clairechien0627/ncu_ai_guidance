import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getScoreStats, getTraces, batchScoreTraces, type TraceItem } from '../../api'
import './ScoresPage.css'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

// ── Dimension config ──────────────────────────────────────────────────────────

const DIMS: { key: string; label: string; weight: string }[] = [
  { key: 'grounding',          label: '證據支撐',    weight: '25%' },
  { key: 'task_fit',           label: '任務符合',    weight: '18%' },
  { key: 'completeness',       label: '完整度',      weight: '17%' },
  { key: 'specificity',        label: '具體性',      weight: '12%' },
  { key: 'source_quality',     label: '來源品質',    weight: '10%' },
  { key: 'uncertainty_honesty', label: '不確定誠實', weight: '10%' },
  { key: 'format_fit',         label: '格式適切',    weight: '8%' },
]

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ScoresPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchMsg, setBatchMsg] = useState<string | null>(null)

  const { data: stats, isLoading: statsLoading } = useQuery({
    queryKey: ['score-stats'],
    queryFn: getScoreStats,
  })

  const { data: lowTraces = [], isLoading: lowLoading } = useQuery({
    queryKey: ['low-quality-traces'],
    queryFn: () => getTraces(100, { max_quality: 3 }),
  })

  const handleBatchScore = async () => {
    setBatchLoading(true)
    setBatchMsg(null)
    try {
      const res = await batchScoreTraces(50)
      setBatchMsg(res.message)
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['score-stats'] })
        qc.invalidateQueries({ queryKey: ['low-quality-traces'] })
      }, 3000)
    } catch {
      setBatchMsg('批次評分啟動失敗')
    } finally {
      setBatchLoading(false)
    }
  }

  if (statsLoading) return <div className="sc-loading">載入中…</div>

  const dist = stats?.distribution ?? []
  const maxCount = Math.max(...dist.map(d => d.count), 1)
  const dimAvgs = stats?.dimension_avgs ?? {}

  const bucketColor = (bucket: string) => {
    if (bucket === '0-1' || bucket === '1-2') return 'sc-dist-fill--low'
    if (bucket === '2-3') return 'sc-dist-fill--mid'
    return 'sc-dist-fill--high'
  }

  const dimCls = (v: number) => v >= 4 ? 'high' : v >= 2.5 ? 'mid' : 'low'

  return (
    <div className="sc-page">
      {/* KPI row */}
      <div className="sc-kpi-row">
        <div className="sc-kpi">
          <span className="sc-kpi-label">已評筆數</span>
          <div className="sc-kpi-value">{(stats?.scored ?? 0).toLocaleString()}</div>
          <span className="sc-kpi-sub">共 {stats?.total ?? 0} 筆</span>
        </div>
        <div className="sc-kpi">
          <span className="sc-kpi-label">未評筆數</span>
          <div className="sc-kpi-value" style={{ color: stats?.unscored ? '#d97706' : '#111827' }}>
            {(stats?.unscored ?? 0).toLocaleString()}
          </div>
          <span className="sc-kpi-sub">待 batch score</span>
        </div>
        <div className="sc-kpi">
          <span className="sc-kpi-label">平均分數</span>
          <div className="sc-kpi-value" style={{
            color: stats?.avg_score == null ? '#9ca3af'
              : stats.avg_score >= 4 ? '#16a34a'
              : stats.avg_score >= 3 ? '#d97706' : '#dc2626',
          }}>
            {stats?.avg_score != null ? `${stats.avg_score.toFixed(2)} / 5` : '—'}
          </div>
        </div>
        <div className="sc-kpi">
          <span className="sc-kpi-label">低品質佔比</span>
          <div className="sc-kpi-value" style={{ color: (stats?.low_quality_pct ?? 0) > 20 ? '#dc2626' : '#111827' }}>
            {stats?.low_quality_pct != null ? `${stats.low_quality_pct}%` : '—'}
          </div>
          <span className="sc-kpi-sub">分數 &lt; 3（{stats?.low_quality_count ?? 0} 筆）</span>
        </div>
      </div>

      {/* Distribution + Dimensions */}
      <div className="sc-two-col">
        {/* Score distribution */}
        <div className="sc-section">
          <div className="sc-section-header">
            <h2 className="sc-section-title">分數分布</h2>
          </div>
          <div className="sc-dist-chart">
            {dist.map(d => (
              <div key={d.bucket} className="sc-dist-row">
                <span className="sc-dist-label">{d.bucket}</span>
                <div className="sc-dist-track">
                  <div
                    className={`sc-dist-fill ${bucketColor(d.bucket)}`}
                    style={{ width: `${(d.count / maxCount) * 100}%` }}
                  />
                </div>
                <span className="sc-dist-count">{d.count}</span>
              </div>
            ))}
            {dist.length === 0 && <div className="sc-empty">尚無評分資料</div>}
          </div>
        </div>

        {/* Dimension averages */}
        <div className="sc-section">
          <div className="sc-section-header">
            <h2 className="sc-section-title">7 維度平均</h2>
          </div>
          <div className="sc-dims">
            {DIMS.map(({ key, label, weight }) => {
              const v = (dimAvgs as Record<string, number | null>)[key]
              return (
                <div key={key} className="sc-dim-row">
                  <span className="sc-dim-label">{label}</span>
                  <div className="sc-dim-track">
                    {v != null && (
                      <div
                        className={`sc-dim-fill ${dimCls(v)}`}
                        style={{ width: `${(v / 5) * 100}%` }}
                      />
                    )}
                  </div>
                  <span className="sc-dim-val">{v != null ? v.toFixed(2) : '—'}</span>
                  <span className="sc-dim-weight">{weight}</span>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Batch score */}
      <div className="sc-section" style={{ marginBottom: 20 }}>
        <div className="sc-section-header">
          <h2 className="sc-section-title">批次評分</h2>
        </div>
        <div className="sc-batch">
          <span className="sc-batch-info">
            目前有 <strong>{stats?.unscored ?? 0}</strong> 筆 trace 尚未評分
          </span>
          <button
            className="sc-batch-btn"
            onClick={handleBatchScore}
            disabled={batchLoading || (stats?.unscored ?? 0) === 0}
          >
            {batchLoading ? '啟動中…' : '▶ Run Batch Score'}
          </button>
          {batchMsg && <span className="sc-batch-msg">{batchMsg}</span>}
        </div>
      </div>

      {/* Low quality traces */}
      <div className="sc-section">
        <div className="sc-section-header">
          <h2 className="sc-section-title">低品質 Traces（分數 &lt; 3）</h2>
          <span style={{ fontSize: 12, color: '#9ca3af' }}>{lowTraces.length} 筆</span>
        </div>

        {lowLoading ? (
          <div className="sc-loading">載入中…</div>
        ) : lowTraces.length === 0 ? (
          <div className="sc-empty">目前沒有低品質 trace</div>
        ) : (
          <table className="sc-traces-table">
            <thead>
              <tr>
                <th>時間</th>
                <th>Intent</th>
                <th>Agent</th>
                <th>Score</th>
                <th>Verdict</th>
                <th>Latency</th>
              </tr>
            </thead>
            <tbody>
              {lowTraces.map((t: TraceItem) => {
                const score = t.quality_score!
                const cls = score < 2 ? 'sc-score-badge--low' : 'sc-score-badge--mid'
                const detail = t.quality_detail
                return (
                  <tr
                    key={t.id}
                    className="sc-traces-row"
                    onClick={() => navigate(`/admin/traces/${t.id}`)}
                  >
                    <td style={{ whiteSpace: 'nowrap', color: '#6b7280' }}>{fmtDate(t.start_time)}</td>
                    <td>
                      {t.original_intent
                        ? <span className="sc-intent-badge">{t.original_intent}</span>
                        : '—'}
                    </td>
                    <td style={{ color: '#6b7280', fontSize: 12 }}>{t.agent_name ?? '—'}</td>
                    <td>
                      <span className={`sc-score-badge ${cls}`}>{score.toFixed(1)}</span>
                    </td>
                    <td style={{ fontSize: 12, color: '#6b7280', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {detail?.verdict ?? (t.user_feedback ? t.user_feedback.slice(0, 60) : '—')}
                    </td>
                    <td style={{ color: '#6b7280', textAlign: 'right' }}>
                      {t.latency != null ? `${t.latency}s` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
