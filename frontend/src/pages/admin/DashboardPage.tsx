import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  getTraceStats,
  getTracesByRouteIntent,
  getTraceErrors,
  getTraceTimeline,
  type TraceGroupStats,
  type TraceItem,
  type TimelinePoint,
} from '../../api'
import './DashboardPage.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

function Trend({ value }: { value: number | null | undefined }) {
  if (value == null) return null
  const up = value >= 0
  return (
    <span className={`dash-trend ${up ? 'dash-trend--up' : 'dash-trend--down'}`}>
      {up ? '↑' : '↓'} {Math.abs(value).toFixed(1)}%
    </span>
  )
}

// ── KPI Card ─────────────────────────────────────────────────────────────────

function KpiCard({
  label, value, trend, sub,
}: { label: string; value: string | number; trend?: number | null; sub?: string }) {
  return (
    <div className="dash-kpi">
      <span className="dash-kpi-label">{label}</span>
      <div className="dash-kpi-value">
        {value}
        {trend != null && <Trend value={trend} />}
      </div>
      {sub && <span className="dash-kpi-sub">{sub}</span>}
    </div>
  )
}

// ── Mini Timeline Chart ───────────────────────────────────────────────────────

function TimelineChart({ points, days }: { points: TimelinePoint[]; days: number }) {
  if (points.length === 0) return <div className="dash-chart-empty">No data</div>

  const maxQ   = Math.max(...points.map(p => p.avg_quality  ?? 0), 0.1)
  const maxLat = Math.max(...points.map(p => p.avg_latency  ?? 0), 0.1)
  const h = 80

  return (
    <svg className="dash-timeline-svg" viewBox={`0 0 ${days * 20} ${h + 20}`} preserveAspectRatio="none">
      {/* Quality line (blue) */}
      <polyline
        fill="none"
        stroke="#3b82f6"
        strokeWidth="2"
        points={points.map((p, i) => {
          const x = i * 20 + 10
          const y = h - ((p.avg_quality ?? 0) / maxQ) * (h - 4)
          return `${x},${y}`
        }).join(' ')}
      />
      {/* Latency line (dashed orange) */}
      <polyline
        fill="none"
        stroke="#f59e0b"
        strokeWidth="1.5"
        strokeDasharray="4,2"
        points={points.map((p, i) => {
          const x = i * 20 + 10
          const y = h - ((p.avg_latency ?? 0) / maxLat) * (h - 4)
          return `${x},${y}`
        }).join(' ')}
      />
    </svg>
  )
}

// ── Route Intent Table ────────────────────────────────────────────────────────

function RouteTable({ rows }: { rows: TraceGroupStats[] }) {
  if (rows.length === 0) return <div className="dash-table-empty">No data</div>
  return (
    <table className="dash-route-table">
      <thead>
        <tr>
          <th>Route</th>
          <th>Runs</th>
          <th>Error rate</th>
          <th>Avg quality</th>
        </tr>
      </thead>
      <tbody>
        {rows.slice(0, 6).map(r => (
          <tr key={r.key}>
            <td><span className={`dash-route-badge dash-route-badge--${r.key}`}>{r.key}</span></td>
            <td className="dash-td-num">{r.runs}</td>
            <td className="dash-td-num">{r.errors ? `${((r.errors / r.runs) * 100).toFixed(1)}%` : '—'}</td>
            <td className="dash-td-num">
              {r.avg_quality_score != null
                ? <span style={{ color: r.avg_quality_score >= 4 ? '#16a34a' : r.avg_quality_score >= 3 ? '#d97706' : '#dc2626', fontWeight: 600 }}>
                    {r.avg_quality_score.toFixed(1)}
                  </span>
                : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Recent Errors ─────────────────────────────────────────────────────────────

function RecentErrors({ errors }: { errors: TraceItem[] }) {
  const navigate = useNavigate()
  if (errors.length === 0) return <div className="dash-table-empty">No recent errors</div>
  return (
    <ul className="dash-errors-list">
      {errors.slice(0, 5).map(e => (
        <li
          key={e.id}
          className="dash-error-item"
          onClick={() => navigate(`/admin/traces`)}
        >
          <span className="dash-error-time">
            {e.start_time ? new Date(e.start_time).toLocaleString('zh-TW', {
              month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
            }) : '—'}
          </span>
          <span className={`dash-route-badge dash-route-badge--${e.original_intent ?? 'unknown'}`}>
            {e.original_intent ?? 'unknown'}
          </span>
          <span className="dash-error-msg">{(e.error ?? '').slice(0, 60)}</span>
        </li>
      ))}
    </ul>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

const DAY_OPTIONS = [7, 14, 30]

export default function DashboardPage() {
  const [days, setDays] = useState(14)

  const { data: stats }   = useQuery({ queryKey: ['dash-stats',    days], queryFn: () => getTraceStats(days) })
  const { data: byRouteRaw } = useQuery({ queryKey: ['dash-route'], queryFn: getTracesByRouteIntent })
  const byRoute: TraceGroupStats[] = Array.isArray(byRouteRaw) ? byRouteRaw : []
  const { data: errors = [] }  = useQuery({ queryKey: ['dash-errors'],     queryFn: () => getTraceErrors(5) })
  const { data: timeline = [] } = useQuery({ queryKey: ['dash-timeline', days], queryFn: () => getTraceTimeline(undefined, undefined, days) })

  return (
    <div className="dash-page">
      {/* KPI row */}
      <div className="dash-kpi-row">
        <KpiCard
          label="Total Traces (all time)"
          value={(stats?.total_runs ?? 0).toLocaleString()}
        />
        <KpiCard
          label={`Traces (last ${days}d)`}
          value={(stats?.period_runs ?? 0).toLocaleString()}
          trend={stats?.runs_trend}
        />
        <KpiCard
          label="Error Rate"
          value={`${stats?.error_rate?.toFixed(1) ?? '—'}%`}
          trend={stats?.error_rate_trend != null ? -stats.error_rate_trend : null}
          sub="↓ is better"
        />
        <KpiCard
          label="Avg Latency"
          value={stats?.avg_latency != null ? `${stats.avg_latency.toFixed(1)}s` : '—'}
          trend={stats?.latency_trend != null ? -stats.latency_trend : null}
          sub="↓ is better"
        />
        <KpiCard
          label="Avg Quality"
          value={stats?.avg_quality != null ? `${stats.avg_quality.toFixed(1)} / 5` : '—'}
          trend={stats?.quality_trend}
        />
      </div>

      {/* Timeline */}
      <div className="dash-section">
        <div className="dash-section-header">
          <h2 className="dash-section-title">Quality &amp; Latency Trend</h2>
          <div className="dash-day-buttons">
            {DAY_OPTIONS.map(d => (
              <button
                key={d}
                className={`dash-day-btn${days === d ? ' active' : ''}`}
                onClick={() => setDays(d)}
              >
                {d}d
              </button>
            ))}
          </div>
        </div>
        <div className="dash-chart-wrapper">
          <TimelineChart points={timeline} days={days} />
          <div className="dash-chart-legend">
            <span><span className="dash-legend-dot dash-legend-dot--blue" /> Avg Quality</span>
            <span><span className="dash-legend-dot dash-legend-dot--orange" /> Avg Latency</span>
          </div>
        </div>
      </div>

      {/* Route table + Recent errors */}
      <div className="dash-two-col">
        <div className="dash-section">
          <h2 className="dash-section-title">By Route Intent</h2>
          <RouteTable rows={byRoute} />
        </div>
        <div className="dash-section">
          <h2 className="dash-section-title">Recent Errors</h2>
          <RecentErrors errors={errors} />
        </div>
      </div>
    </div>
  )
}
