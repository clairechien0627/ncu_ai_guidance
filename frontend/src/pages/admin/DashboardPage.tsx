import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  getTraceStats, getTracesByAgent, getTraceErrors, getTraceTimeline,
  type TraceGroupStats, type TraceItem, type TimelinePoint,
} from '../../api'
import { ScoreBar } from '../../components/admin/ScoreBar'

// ── Mini sparkline ─────────────────────────────────────────────────────────────

function TimelineChart({ points, days }: { points: TimelinePoint[]; days: number }) {
  if (points.length === 0) return <div style={{ padding: '24px', color: 'var(--adm-text-3)', fontSize: 12 }}>No data</div>
  const maxQ   = Math.max(...points.map(p => p.avg_quality  ?? 0), 0.1)
  const maxLat = Math.max(...points.map(p => p.avg_latency  ?? 0), 0.1)
  const h = 80
  return (
    <svg style={{ width: '100%', height: h + 20 }} viewBox={`0 0 ${days * 20} ${h + 20}`} preserveAspectRatio="none">
      <polyline fill="none" stroke="var(--adm-blue)" strokeWidth="2"
        points={points.map((p, i) => `${i * 20 + 10},${h - ((p.avg_quality ?? 0) / maxQ) * (h - 4)}`).join(' ')} />
      <polyline fill="none" stroke="var(--adm-amber)" strokeWidth="1.5" strokeDasharray="4,2"
        points={points.map((p, i) => `${i * 20 + 10},${h - ((p.avg_latency ?? 0) / maxLat) * (h - 4)}`).join(' ')} />
    </svg>
  )
}

// ── Route table ───────────────────────────────────────────────────────────────

function RouteTable({ rows }: { rows: TraceGroupStats[] }) {
  if (rows.length === 0) return <div style={{ padding: '16px', color: 'var(--adm-text-3)', fontSize: 12 }}>No data</div>
  return (
    <div className="adm-table-wrap">
      <table className="adm-table">
        <thead>
          <tr>
            <th>Route</th>
            <th style={{ width: 60 }}>Runs</th>
            <th style={{ width: 80 }}>Err%</th>
            <th style={{ width: 120 }}>Avg Quality</th>
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 6).map(r => (
            <tr key={r.key}>
              <td><span className="adm-badge adm-badge--info" style={{ fontFamily: 'var(--adm-font-mono)' }}>{r.key}</span></td>
              <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{r.runs}</td>
              <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>
                {r.errors ? `${((r.errors / r.runs) * 100).toFixed(1)}%` : '—'}
              </td>
              <td><ScoreBar value={r.avg_quality_score} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Recent errors ─────────────────────────────────────────────────────────────

function RecentErrors({ errors }: { errors: TraceItem[] }) {
  const navigate = useNavigate()
  if (errors.length === 0) return <div style={{ padding: '16px', color: 'var(--adm-text-3)', fontSize: 12 }}>No recent errors</div>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {errors.slice(0, 5).map(e => (
        <div key={e.id} onClick={() => navigate('/admin/traces')}
          style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 0', borderBottom: '1px solid var(--adm-border)', cursor: 'pointer' }}>
          <span style={{ fontSize: 11, color: 'var(--adm-text-3)', whiteSpace: 'nowrap', width: 100, flexShrink: 0 }}>
            {e.start_time ? new Date(e.start_time).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }) : '—'}
          </span>
          <span className="adm-badge adm-badge--error">{e.agent_name ?? 'unknown'}</span>
          <span style={{ fontSize: 12, color: 'var(--adm-text-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {(e.error ?? '').slice(0, 80)}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

const DAY_OPTIONS = [7, 14, 30]

export default function DashboardPage() {
  const [days, setDays] = useState(14)

  const { data: stats }       = useQuery({ queryKey: ['dash-stats', days],   queryFn: () => getTraceStats(days) })
  const { data: byRouteRaw }  = useQuery({ queryKey: ['dash-route'],          queryFn: getTracesByAgent })
  const byRoute: TraceGroupStats[] = Array.isArray(byRouteRaw) ? byRouteRaw : []
  const { data: errors = [] } = useQuery({ queryKey: ['dash-errors'],         queryFn: () => getTraceErrors(5) })
  const { data: timeline = [] } = useQuery({ queryKey: ['dash-timeline', days], queryFn: () => getTraceTimeline(undefined, undefined, days) })

  return (
    <div>
      {/* Trend chart */}
      <div className="adm-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <span className="adm-card-title" style={{ margin: 0 }}>Quality &amp; Latency Trend</span>
          <div style={{ display: 'flex', gap: 4 }}>
            {DAY_OPTIONS.map(d => (
              <button key={d}
                className={`adm-btn adm-btn-sm ${days === d ? 'adm-btn-primary' : 'adm-btn-secondary'}`}
                onClick={() => setDays(d)}
              >{d}d</button>
            ))}
          </div>
        </div>
        <TimelineChart points={timeline} days={days} />
        <div style={{ display: 'flex', gap: 16, marginTop: 8, fontSize: 11, color: 'var(--adm-text-3)' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 16, height: 2, background: 'var(--adm-blue)', display: 'inline-block' }} /> Avg Quality
          </span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={{ width: 16, height: 2, background: 'var(--adm-amber)', display: 'inline-block', borderTop: '2px dashed var(--adm-amber)' }} /> Avg Latency
          </span>
        </div>
      </div>

      {/* Two columns */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <div className="adm-card">
          <div className="adm-card-title">By Agent</div>
          <RouteTable rows={byRoute} />
        </div>
        <div className="adm-card">
          <div className="adm-card-title">Recent Errors</div>
          <RecentErrors errors={errors} />
        </div>
      </div>
    </div>
  )
}
