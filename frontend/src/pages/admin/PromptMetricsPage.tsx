import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  getPromptDetail,
  getTraceTimeline,
  getTracesByPromptVersion,
  getTraces,
  compareTraceVersions,
  type TimelinePoint,
  type TraceGroupStats,
  type TraceItem,
} from '../../api'
import './PromptMetricsPage.css'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

// ── Timeline chart ────────────────────────────────────────────────────────────

function TimelineChart({ points, days }: { points: TimelinePoint[]; days: number }) {
  if (points.length === 0) return <div className="pm-empty" style={{ height: 80 }}>No trend data</div>

  const maxQ   = Math.max(...points.map(p => p.avg_quality ?? 0), 0.1)
  const maxLat = Math.max(...points.map(p => p.avg_latency ?? 0), 0.1)
  const h = 72

  return (
    <svg className="pm-timeline-svg" viewBox={`0 0 ${days * 20} ${h}`} preserveAspectRatio="none">
      <polyline
        fill="none" stroke="#3b82f6" strokeWidth="2"
        points={points.map((p, i) => `${i * 20 + 10},${h - ((p.avg_quality ?? 0) / maxQ) * (h - 4)}`).join(' ')}
      />
      <polyline
        fill="none" stroke="#f59e0b" strokeWidth="1.5" strokeDasharray="4,2"
        points={points.map((p, i) => `${i * 20 + 10},${h - ((p.avg_latency ?? 0) / maxLat) * (h - 4)}`).join(' ')}
      />
    </svg>
  )
}

// ── Version compare result ────────────────────────────────────────────────────

function CompareResult({
  v1, v2, onClear,
}: {
  v1: string; v2: string; onClear: () => void
}) {
  const { data, isLoading } = useQuery({
    queryKey: ['compare', v1, v2],
    queryFn: () => compareTraceVersions(v1, v2),
    enabled: !!v1 && !!v2,
  })

  if (isLoading) return <div className="pm-loading">comparing…</div>
  if (!data) return null

  const sides = [
    { label: v1, stats: data.v1 },
    { label: v2, stats: data.v2 },
  ]

  return (
    <div>
      <div style={{ padding: '0 18px 10px', display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ fontSize: 12, color: '#374151', fontWeight: 600 }}>
          Comparing {v1.slice(0, 8)} ↔ {v2.slice(0, 8)}
        </span>
        <button className="pm-clear-compare" onClick={onClear}>Clear</button>
      </div>
      <div className="pm-compare-result">
        {sides.map(({ label, stats }) => (
          <div key={label} className="pm-compare-side">
            <h4>#{label.slice(0, 8)}</h4>
            {[
              ['Runs',        stats.runs],
              ['Errors',      stats.errors],
              ['Error rate',  stats.error_rate != null ? `${(stats.error_rate * 100).toFixed(1)}%` : '—'],
              ['Avg latency', stats.avg_latency != null ? `${stats.avg_latency}s` : '—'],
              ['Avg quality', stats.avg_quality != null ? stats.avg_quality.toFixed(2) : '—'],
            ].map(([k, v]) => (
              <div key={k as string} className="pm-compare-row">
                <span>{k}</span>
                <span>{String(v ?? '—')}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

const DAY_OPTIONS = [7, 14, 30]

export default function PromptMetricsPage() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const [days, setDays] = useState(14)
  const [selectedVersions, setSelectedVersions] = useState<string[]>([])
  const [comparing, setComparing] = useState(false)
  const [compareV1, setCompareV1] = useState<string | null>(null)
  const [compareV2, setCompareV2] = useState<string | null>(null)

  const { data: promptDetail } = useQuery({
    queryKey: ['prompt-detail', name],
    queryFn: () => getPromptDetail(name!),
    enabled: !!name,
  })

  const { data: timeline = [] } = useQuery({
    queryKey: ['pm-timeline', name, days],
    queryFn: () => getTraceTimeline(name, undefined, days),
    enabled: !!name,
  })

  const { data: allVersionStats = [] } = useQuery({
    queryKey: ['pm-by-version'],
    queryFn: getTracesByPromptVersion,
  })

  const { data: recentTraces = [], isLoading: tracesLoading } = useQuery({
    queryKey: ['pm-traces', name],
    queryFn: () => getTraces(40, { prompt_name: name }),
    enabled: !!name,
  })

  // Filter version stats to this prompt
  const versionStats: TraceGroupStats[] = allVersionStats.filter(s =>
    s.key.startsWith(`${name}@`) || s.key === name,
  )

  // Map hash → synced_at from prompt detail
  const hashDateMap: Record<string, string> = {}
  promptDetail?.versions.forEach(v => { hashDateMap[v.hash] = v.synced_at })

  // KPI aggregation
  const totalRuns = versionStats.reduce((a, s) => a + s.runs, 0)
  const totalErrors = versionStats.reduce((a, s) => a + s.errors, 0)
  const errRate = totalRuns > 0 ? (totalErrors / totalRuns * 100).toFixed(1) : '—'
  const avgQuality = (() => {
    const vals = versionStats.filter(s => s.avg_quality_score != null).map(s => s.avg_quality_score!)
    return vals.length > 0 ? (vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2) : '—'
  })()
  const avgLatency = (() => {
    const vals = versionStats.filter(s => s.avg_latency != null).map(s => s.avg_latency!)
    return vals.length > 0 ? `${(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1)}s` : '—'
  })()

  const toggleVersion = (hash: string) => {
    setSelectedVersions(prev => {
      if (prev.includes(hash)) return prev.filter(h => h !== hash)
      if (prev.length >= 2) return [prev[1], hash]
      return [...prev, hash]
    })
  }

  const handleCompare = () => {
    if (selectedVersions.length !== 2) return
    setCompareV1(selectedVersions[0])
    setCompareV2(selectedVersions[1])
    setComparing(true)
  }

  return (
    <div className="pm-page">
      {/* Header */}
      <div className="pm-header">
        <button className="pm-back-btn" onClick={() => navigate(`/admin/prompts/${encodeURIComponent(name!)}`)}>
          ← {name}
        </button>
        <span className="pm-title">Metrics</span>
        <Link to={`/admin/prompts/${encodeURIComponent(name!)}`} className="pm-prompt-link">
          View content ↗
        </Link>
      </div>

      <div className="pm-scroll">
        {/* KPI row */}
        <div className="pm-kpi-row">
          <div className="pm-kpi">
            <span className="pm-kpi-label">Total Runs</span>
            <div className="pm-kpi-value">{totalRuns.toLocaleString()}</div>
          </div>
          <div className="pm-kpi">
            <span className="pm-kpi-label">Avg Quality</span>
            <div className="pm-kpi-value">{avgQuality}</div>
          </div>
          <div className="pm-kpi">
            <span className="pm-kpi-label">Avg Latency</span>
            <div className="pm-kpi-value">{avgLatency}</div>
          </div>
          <div className="pm-kpi">
            <span className="pm-kpi-label">Error Rate</span>
            <div className="pm-kpi-value">{totalRuns > 0 ? `${errRate}%` : '—'}</div>
          </div>
        </div>

        {/* Timeline */}
        <div className="pm-section">
          <div className="pm-section-header">
            <h2 className="pm-section-title">Quality &amp; Latency Trend</h2>
            <div className="pm-day-btns">
              {DAY_OPTIONS.map(d => (
                <button
                  key={d}
                  className={`pm-day-btn${days === d ? ' active' : ''}`}
                  onClick={() => setDays(d)}
                >{d}d</button>
              ))}
            </div>
          </div>
          <div className="pm-chart-wrap">
            <TimelineChart points={timeline} days={days} />
          </div>
          <div className="pm-chart-legend">
            <span><span className="pm-legend-dot pm-legend-dot--blue" /> Avg Quality</span>
            <span><span className="pm-legend-dot pm-legend-dot--orange" /> Avg Latency</span>
          </div>
        </div>

        {/* Version table */}
        <div className="pm-section">
          <div className="pm-section-header">
            <h2 className="pm-section-title">By Version</h2>
            {selectedVersions.length === 2 && (
              <button className="pm-compare-btn" onClick={handleCompare}>↕ Compare selected</button>
            )}
            {selectedVersions.length === 1 && (
              <span className="pm-compare-hint">Select one more version to compare</span>
            )}
          </div>

          {versionStats.length === 0 ? (
            <div className="pm-empty">No version data</div>
          ) : (
            <table className="pm-table">
              <thead>
                <tr>
                  <th>Hash</th>
                  <th>Synced At</th>
                  <th>Runs</th>
                  <th>Avg Quality</th>
                  <th>Avg Latency</th>
                  <th>Error %</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {versionStats.map(s => {
                  const hash = s.key.includes('@') ? s.key.split('@').pop()! : s.key
                  const syncedAt = hashDateMap[hash] ?? null
                  const errPct = s.runs > 0 ? (s.errors / s.runs * 100).toFixed(1) : '0.0'
                  const isSelected = selectedVersions.includes(hash)
                  return (
                    <tr key={s.key}>
                      <td className="pm-td-hash"><code>#{hash.slice(0, 8)}</code></td>
                      <td className="pm-td-date">{fmtDate(syncedAt)}</td>
                      <td className="pm-td-num">{s.runs}</td>
                      <td className="pm-td-num">
                        {s.avg_quality_score != null
                          ? <span style={{ color: s.avg_quality_score >= 4 ? '#16a34a' : s.avg_quality_score >= 3 ? '#d97706' : '#dc2626', fontWeight: 600 }}>
                              {s.avg_quality_score.toFixed(2)}
                            </span>
                          : '—'}
                      </td>
                      <td className="pm-td-num">{s.avg_latency != null ? `${s.avg_latency}s` : '—'}</td>
                      <td className="pm-td-num">{errPct}%</td>
                      <td>
                        <button
                          className={`pm-select-btn${isSelected ? ' selected' : ''}`}
                          onClick={() => toggleVersion(hash)}
                        >
                          {isSelected ? '✓ Selected' : 'Select'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}

          {comparing && compareV1 && compareV2 && (
            <div style={{ borderTop: '1px solid #e5e7eb' }}>
              <CompareResult
                v1={compareV1}
                v2={compareV2}
                onClear={() => { setComparing(false); setCompareV1(null); setCompareV2(null); setSelectedVersions([]) }}
              />
            </div>
          )}
        </div>

        {/* Recent traces */}
        <div className="pm-section">
          <div className="pm-section-header">
            <h2 className="pm-section-title">Recent Traces</h2>
            <span style={{ fontSize: 12, color: '#9ca3af' }}>{recentTraces.length} runs</span>
          </div>

          {tracesLoading ? (
            <div className="pm-loading">Loading…</div>
          ) : recentTraces.length === 0 ? (
            <div className="pm-empty">No traces found for this prompt</div>
          ) : (
            <table className="pm-traces-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Agent</th>
                  <th>Status</th>
                  <th>Latency</th>
                  <th>Score</th>
                </tr>
              </thead>
              <tbody>
                {recentTraces.map((t: TraceItem) => {
                  const isErr = t.status === 'ERROR'
                  const score = t.quality_score
                  const scoreCls = score == null ? '' : score >= 4 ? 'high' : score >= 3 ? 'mid' : 'low'
                  return (
                    <tr
                      key={t.id}
                      className="pm-traces-row"
                      onClick={() => navigate(`/admin/traces/${t.id}`)}
                    >
                      <td style={{ whiteSpace: 'nowrap', color: '#6b7280' }}>{fmtDate(t.start_time)}</td>
                      <td>
                        {t.agent_name ? <span className="pm-agent-badge">{t.agent_name}</span> : '—'}
                      </td>
                      <td>
                        <span className={isErr ? 'pm-status-err' : 'pm-status-ok'}>
                          {isErr ? '✕' : '✓'}
                        </span>
                      </td>
                      <td className="pm-td-num">{t.latency != null ? `${t.latency}s` : '—'}</td>
                      <td>
                        {score != null
                          ? <span className={`pm-score ${scoreCls}`}>{score.toFixed(1)}</span>
                          : <span style={{ color: '#d1d5db' }}>—</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
