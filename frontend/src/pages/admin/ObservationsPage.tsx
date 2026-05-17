import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getObservations, getObservationStats, type ObservationItem } from '../../api'
import './ObservationsPage.css'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function fmtTokens(p: number | null, c: number | null) {
  if (p == null && c == null) return '—'
  const total = (p ?? 0) + (c ?? 0)
  return total.toLocaleString()
}

const TYPE_OPTIONS = ['all', 'llm', 'tool', 'chain', 'retriever']

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ObservationsPage() {
  const navigate = useNavigate()
  const [runType, setRunType] = useState('all')

  const { data: stats } = useQuery({
    queryKey: ['obs-stats'],
    queryFn: getObservationStats,
  })

  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['observations', runType],
    queryFn: () => getObservations(200, runType),
  })

  const byType = stats?.by_type ?? {}
  const llmCount  = byType['llm']?.count ?? 0
  const toolCount = byType['tool']?.count ?? 0
  const llmTokens = (byType['llm']?.prompt_tokens ?? 0) + (byType['llm']?.completion_tokens ?? 0)

  return (
    <div className="obs-page">
      {/* KPI row */}
      <div className="obs-kpi-row">
        <div className="obs-kpi">
          <span className="obs-kpi-label">Total Spans</span>
          <div className="obs-kpi-value">{(stats?.total ?? 0).toLocaleString()}</div>
        </div>
        <div className="obs-kpi">
          <span className="obs-kpi-label">LLM Calls</span>
          <div className="obs-kpi-value">{llmCount.toLocaleString()}</div>
        </div>
        <div className="obs-kpi">
          <span className="obs-kpi-label">Tool Calls</span>
          <div className="obs-kpi-value">{toolCount.toLocaleString()}</div>
        </div>
        <div className="obs-kpi">
          <span className="obs-kpi-label">LLM Tokens</span>
          <div className="obs-kpi-value">{llmTokens.toLocaleString()}</div>
        </div>
        <div className="obs-kpi">
          <span className="obs-kpi-label">Total Tokens</span>
          <div className="obs-kpi-value">{(stats?.total_tokens ?? 0).toLocaleString()}</div>
        </div>
      </div>

      {/* Type filter */}
      <div className="obs-toolbar">
        {TYPE_OPTIONS.map(t => (
          <button
            key={t}
            className={`obs-type-btn${runType === t ? ' active' : ''}`}
            onClick={() => setRunType(t)}
          >
            {t === 'all' ? 'All' : t.toUpperCase()}
            {t !== 'all' && byType[t] ? ` (${byType[t].count})` : ''}
          </button>
        ))}
        <span className="obs-count">{rows.length} rows</span>
      </div>

      {/* Table */}
      <div className="obs-table-wrap">
        {isLoading ? (
          <div className="obs-loading">載入中…</div>
        ) : rows.length === 0 ? (
          <div className="obs-empty">No observations</div>
        ) : (
          <table className="obs-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Type</th>
                <th>Name</th>
                <th>Input</th>
                <th>Output</th>
                <th>Tokens</th>
                <th>Latency</th>
                <th>Status</th>
                <th>Parent</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row: ObservationItem) => {
                const isErr = !!row.error
                const typeCls = `obs-type--${row.run_type ?? 'unknown'}`
                return (
                  <tr
                    key={row.id}
                    className="obs-row"
                    onClick={() => row.parent_run_id && navigate(`/admin/traces/${row.parent_run_id}`)}
                  >
                    <td style={{ whiteSpace: 'nowrap', color: '#6b7280' }}>{fmtDate(row.start_time)}</td>
                    <td><span className={`obs-type ${typeCls}`}>{row.run_type ?? '?'}</span></td>
                    <td><span className="obs-name" title={row.name}>{row.name}</span></td>
                    <td>
                      {row.input
                        ? <span className="obs-io" title={row.input}>{row.input}</span>
                        : <span style={{ color: '#d1d5db' }}>—</span>}
                    </td>
                    <td>
                      {row.error
                        ? <span className="obs-error-msg" title={row.error}>{row.error}</span>
                        : row.output
                          ? <span className="obs-io" title={row.output}>{row.output}</span>
                          : <span style={{ color: '#d1d5db' }}>—</span>}
                    </td>
                    <td className="obs-tokens">
                      {fmtTokens(row.prompt_tokens, row.completion_tokens)}
                    </td>
                    <td className="obs-latency">
                      {row.latency != null ? `${row.latency}s` : '—'}
                    </td>
                    <td>
                      <span className={isErr ? 'obs-status-err' : 'obs-status-ok'}>
                        {isErr ? '✕' : '✓'}
                      </span>
                    </td>
                    <td>
                      {row.parent_run_id && (
                        <span
                          className="obs-parent-link"
                          title={row.parent_run_id}
                          onClick={e => { e.stopPropagation(); navigate(`/admin/traces/${row.parent_run_id}`) }}
                        >
                          {row.parent_run_id.slice(-8)}
                        </span>
                      )}
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
