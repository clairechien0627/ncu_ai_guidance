import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { getEvalRunDetail, getDatasets, addTraceToDataset, type EvalRunItem, type DatasetData } from '../../api'
import { StatusBadge } from '../../components/admin/StatusBadge'
import { DimensionGrid } from '../../components/admin/DimensionGrid'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function AddToDatasetModal({ traceId, onClose, onDone }: { traceId: string; onClose: () => void; onDone: () => void }) {
  const [datasetId, setDatasetId] = useState('')
  const [loading, setLoading] = useState(false)
  const { data } = useQuery({ queryKey: ['datasets-list'], queryFn: getDatasets })
  const datasets: DatasetData[] = data?.datasets ?? []

  const handleAdd = async () => {
    if (!datasetId) return
    setLoading(true)
    try { await addTraceToDataset(datasetId, traceId); onDone() }
    catch { setLoading(false) }
  }

  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" style={{ width: 360 }} onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">加入 Dataset</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <div className="adm-form-group">
            <label className="adm-form-label">選擇 Dataset</label>
            <select className="adm-form-select" value={datasetId} onChange={e => setDatasetId(e.target.value)}>
              <option value="">— 選擇 —</option>
              {datasets.map(d => <option key={d.dataset_id} value={d.dataset_id}>{d.name}</option>)}
            </select>
          </div>
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-primary" disabled={!datasetId || loading} onClick={handleAdd}>
            {loading ? '加入中…' : '確認加入'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function EvaluationDetailPage() {
  const { evalRunId } = useParams<{ evalRunId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [addToDatasetTraceId, setAddToDatasetTraceId] = useState<string | null>(null)

  const { data: run, isLoading } = useQuery({
    queryKey: ['eval-run-detail', evalRunId],
    queryFn: () => getEvalRunDetail(evalRunId!),
    enabled: !!evalRunId,
    refetchInterval: (query) => query.state.data?.status === 'running' ? 5000 : false,
  })

  // Fetch per-run score stats (dimension averages)
  const { data: scoreStats } = useQuery({
    queryKey: ['eval-run-score-stats', evalRunId],
    queryFn: async () => {
      const { default: axios } = await import('axios')
      const API_BASE = import.meta.env.VITE_API_URL ?? ''
      const { data } = await axios.get(`${API_BASE}/api/evaluations/runs/${evalRunId}/score-stats`)
      return data
    },
    enabled: !!evalRunId && run?.status === 'completed',
  })

  if (isLoading) return <div style={{ padding: 32, color: 'var(--adm-text-3)' }}>載入中…</div>
  if (!run) return <div style={{ padding: 32, color: 'var(--adm-red)' }}>找不到此 Eval Run</div>

  const items: EvalRunItem[] = run.items ?? []
  const dimAvgs = scoreStats?.dimension_avgs ?? run.dimension_avgs ?? {}

  return (
    <div>
      <PageHeader
        title={run.name}
        crumbs={[{ label: 'Eval Runs', to: '/admin/evaluations' }, { label: run.name }]}
        subtitle={`${run.succeeded_count}/${run.total_count} items  ·  avg ${scoreStats?.avg_score != null ? scoreStats.avg_score.toFixed(2) : '—'}`}
        actions={<StatusBadge status={run.status} />}
      />

      {/* Dimension overview */}
      {Object.keys(dimAvgs).length > 0 && (
        <div className="adm-card" style={{ marginBottom: 16 }}>
          <div className="adm-card-title">7 維度總覽</div>
          <DimensionGrid scores={dimAvgs} />
        </div>
      )}

      {/* Score distribution from stats */}
      {scoreStats?.distribution && (
        <div className="adm-card" style={{ marginBottom: 16 }}>
          <div className="adm-card-title">分數分布</div>
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            {scoreStats.distribution.map((d: any) => (
              <div key={d.bucket} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--adm-text-3)', width: 28 }}>{d.bucket}</span>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--adm-text)' }}>{d.count}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Items table */}
      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}>#</th>
              <th style={{ width: 130 }}>Trace ID</th>
              <th className="adm-col-status">Status</th>
              <th style={{ width: 120 }}>Dataset Item</th>
              <th>Error</th>
              <th className="adm-col-time">完成時間</th>
              <th className="adm-col-action">動作</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={7} className="adm-table-empty">Items 尚未產生</td></tr>
            ) : items.map((item, idx) => {
              const isFailed = item.status === 'failed'
              return (
                <tr key={item.eval_item_id} className={isFailed ? 'adm-row--danger' : ''}>
                  <td style={{ color: 'var(--adm-text-3)', fontSize: 11 }}>{idx + 1}</td>
                  <td className="adm-cell-mono" style={{ cursor: 'pointer', color: 'var(--adm-blue)' }}
                    onClick={() => navigate('/admin/traces')}
                    title={item.trace_id ?? undefined}
                  >{item.trace_id ? item.trace_id.slice(-12) : '—'}</td>
                  <td><StatusBadge status={item.status} /></td>
                  <td className="adm-cell-mono">{item.dataset_item_id ? item.dataset_item_id.slice(-10) : '—'}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-red)' }}>{item.error ?? ''}</td>
                  <td className="adm-cell-mono">{fmtDate(item.completed_at)}</td>
                  <td>
                    <div className="adm-action-row">
                      {item.trace_id && (
                        <button className="adm-btn adm-btn-ghost adm-btn-sm"
                          onClick={() => setAddToDatasetTraceId(item.trace_id!)}
                          title="加入 Dataset"
                        >
                          <Plus size={12} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {addToDatasetTraceId && (
        <AddToDatasetModal
          traceId={addToDatasetTraceId}
          onClose={() => setAddToDatasetTraceId(null)}
          onDone={() => { setAddToDatasetTraceId(null); qc.invalidateQueries({ queryKey: ['datasets-list'] }) }}
        />
      )}
    </div>
  )
}
