import { useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Download } from 'lucide-react'
import { compareExperimentRuns, getExperimentRuns, type ExperimentRunData } from '../../api'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { PageHeader } from '../../components/admin/PageHeader'

const DIMS = ['grounding', 'task_fit', 'completeness', 'specificity', 'source_quality', 'uncertainty_honesty', 'format_fit', 'overall']

function DeltaCell({ delta }: { delta: number | null }) {
  if (delta == null) return <span style={{ color: 'var(--adm-text-3)' }}>—</span>
  const cls = delta > 0.05 ? 'adm-delta--pos' : delta < -0.05 ? 'adm-delta--neg' : 'adm-delta--neu'
  const arrow = delta > 0.05 ? '↑' : delta < -0.05 ? '↓' : '—'
  return <span className={`adm-delta ${cls}`}>{delta > 0 ? '+' : ''}{delta.toFixed(2)} {arrow}</span>
}

export default function ExperimentComparePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [expandedItem, setExpandedItem] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'improved' | 'regressed'>('all')

  const paramA = searchParams.get('a') ?? ''
  const paramB = searchParams.get('b') ?? ''
  const [selA, setSelA] = useState(paramA)
  const [selB, setSelB] = useState(paramB)

  const { data: runsData } = useQuery({ queryKey: ['experiment-runs-all'], queryFn: () => getExperimentRuns({ status: 'completed', limit: 50 }) })
  const completedRuns: ExperimentRunData[] = runsData?.runs ?? []

  const canCompare = selA && selB && selA !== selB
  const { data: compareResult, isLoading, error } = useQuery({
    queryKey: ['exp-compare', selA, selB],
    queryFn: () => compareExperimentRuns(selA, selB),
    enabled: !!(selA && selB && selA !== selB),
  })

  const handleCompare = () => {
    if (canCompare) setSearchParams({ a: selA, b: selB })
  }

  const handleExport = () => {
    if (!compareResult) return
    const blob = new Blob([JSON.stringify(compareResult, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a'); a.href = url; a.download = `compare-${selA.slice(-6)}-vs-${selB.slice(-6)}.json`; a.click()
    URL.revokeObjectURL(url)
  }

  const filteredItems = (compareResult?.items ?? []).filter(i =>
    filter === 'all' ? true : i.status === filter
  )

  return (
    <div>
      <PageHeader
        title="Experiment Compare"
        crumbs={[{ label: 'Experiments', to: '/admin/experiments' }, { label: 'Compare' }]}
      />

      {/* Selector */}
      <div className="adm-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div className="adm-form-label">Experiment A</div>
            <select className="adm-form-select" value={selA} onChange={e => setSelA(e.target.value)}>
              <option value="">— 選擇 —</option>
              {completedRuns.map(r => <option key={r.experiment_run_id} value={r.experiment_run_id}>{r.name} ({r.experiment_run_id.slice(-8)})</option>)}
            </select>
          </div>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div className="adm-form-label">Experiment B</div>
            <select className="adm-form-select" value={selB} onChange={e => setSelB(e.target.value)}>
              <option value="">— 選擇 —</option>
              {completedRuns.filter(r => r.experiment_run_id !== selA).map(r =>
                <option key={r.experiment_run_id} value={r.experiment_run_id}>{r.name} ({r.experiment_run_id.slice(-8)})</option>)}
            </select>
          </div>
          <button className="adm-btn adm-btn-primary" disabled={!canCompare} onClick={handleCompare}>比較</button>
          {compareResult && <button className="adm-btn adm-btn-secondary" onClick={handleExport}><Download size={13} /> 下載報告</button>}
        </div>
      </div>

      {isLoading && <div style={{ padding: 32, color: 'var(--adm-text-3)' }}><div className="adm-spinner" style={{ margin: '0 auto 8px' }} />比較中…</div>}
      {error && <div style={{ padding: 16, color: 'var(--adm-red)' }}>比較失敗，請確認兩個 experiment 都有完整的評分</div>}

      {compareResult && (
        <>
          {/* Summary */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 12, marginBottom: 16 }}>
            <div className="adm-kpi-card" style={{ borderLeft: '3px solid var(--adm-green)' }}>
              <div className="adm-kpi-label">進步</div>
              <div className="adm-kpi-value" style={{ color: 'var(--adm-green)' }}>{compareResult.improved_count} 題</div>
            </div>
            <div className="adm-kpi-card" style={{ borderLeft: '3px solid var(--adm-red)' }}>
              <div className="adm-kpi-label">退步</div>
              <div className="adm-kpi-value" style={{ color: 'var(--adm-red)' }}>{compareResult.regressed_count} 題</div>
            </div>
            <div className="adm-kpi-card" style={{ borderLeft: '3px solid var(--adm-border-2)' }}>
              <div className="adm-kpi-label">持平</div>
              <div className="adm-kpi-value" style={{ color: 'var(--adm-text-2)' }}>{compareResult.neutral_count} 題</div>
            </div>
          </div>

          {/* Dimension delta table */}
          <div className="adm-card" style={{ marginBottom: 16 }}>
            <div className="adm-card-title">維度對比</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', padding: '4px 0', width: 160 }}>Dimension</th>
                  <th style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', padding: '4px 0', width: 120 }}>A ({compareResult.experiment_a.name})</th>
                  <th style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', padding: '4px 0', width: 120 }}>B ({compareResult.experiment_b.name})</th>
                  <th style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', padding: '4px 0', width: 100 }}>Δ</th>
                </tr>
              </thead>
              <tbody>
                {DIMS.map(dim => {
                  const d = compareResult.dimension_deltas[dim]
                  if (!d) return null
                  return (
                    <tr key={dim} style={{ borderTop: '1px solid var(--adm-border)' }}>
                      <td style={{ padding: '8px 0', fontSize: 12, color: 'var(--adm-text-2)', textTransform: 'capitalize' }}>{dim.replace(/_/g, ' ')}</td>
                      <td><ScoreBar value={d.a} width="100px" /></td>
                      <td><ScoreBar value={d.b} width="100px" /></td>
                      <td><DeltaCell delta={d.delta} /></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Items */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--adm-text)' }}>Items</span>
            {(['all', 'improved', 'regressed'] as const).map(f => (
              <button key={f} className={`adm-btn adm-btn-sm ${filter === f ? 'adm-btn-primary' : 'adm-btn-secondary'}`}
                onClick={() => setFilter(f)}>
                {f === 'all' ? `全部 (${compareResult.compared_item_count})` : f === 'improved' ? `進步 (${compareResult.improved_count})` : `退步 (${compareResult.regressed_count})`}
              </button>
            ))}
          </div>

          <div className="adm-table-wrap">
            <table className="adm-table">
              <thead>
                <tr>
                  <th style={{ width: 120 }}>Dataset Item</th>
                  <th style={{ width: 120 }}>A Score</th>
                  <th style={{ width: 120 }}>B Score</th>
                  <th style={{ width: 80 }}>Δ</th>
                  <th className="adm-col-action">展開</th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.length === 0 ? (
                  <tr><td colSpan={5} className="adm-table-empty">無符合條件的 items</td></tr>
                ) : filteredItems.map(item => (
                  <>
                    <tr key={item.dataset_item_id} className="adm-row--clickable"
                      onClick={() => setExpandedItem(expandedItem === item.dataset_item_id ? null : item.dataset_item_id)}>
                      <td className="adm-cell-mono">{item.dataset_item_id.slice(-12)}</td>
                      <td><ScoreBar value={item.a_score} /></td>
                      <td><ScoreBar value={item.b_score} /></td>
                      <td><DeltaCell delta={item.delta} /></td>
                      <td><span style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{expandedItem === item.dataset_item_id ? '▲' : '▼'}</span></td>
                    </tr>
                    {expandedItem === item.dataset_item_id && (
                      <tr key={`${item.dataset_item_id}-expand`} className="adm-expand-row">
                        <td colSpan={5}>
                          <div className="adm-diff-grid">
                            <div className="adm-diff-pane">
                              <div className="adm-diff-label">A: {compareResult.experiment_a.name}</div>
                              <div className="adm-diff-text">{item.a_output || '（無輸出）'}</div>
                            </div>
                            <div className="adm-diff-pane">
                              <div className="adm-diff-label">B: {compareResult.experiment_b.name}</div>
                              <div className="adm-diff-text">{item.b_output || '（無輸出）'}</div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
