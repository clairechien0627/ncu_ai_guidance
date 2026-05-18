import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { TestTube } from 'lucide-react'
import { getExperimentRuns, createExperimentRun, getDatasets, triggerExperimentEval, type ExperimentRunData, type DatasetData } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { StatusBadge } from '../../components/admin/StatusBadge'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { PageHeader } from '../../components/admin/PageHeader'
import { BulkActionBar } from '../../components/admin/BulkActionBar'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function NewExpModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [datasetId, setDatasetId] = useState('')
  const [name, setName] = useState('')
  const [promptVersion, setPromptVersion] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const { data } = useQuery({ queryKey: ['datasets-list'], queryFn: getDatasets })
  const datasets: DatasetData[] = data?.datasets ?? []

  const handleCreate = async () => {
    if (!datasetId || !name.trim()) return
    setLoading(true); setErr(null)
    try {
      await createExperimentRun({ dataset_id: datasetId, name: name.trim(), prompt_version: promptVersion.trim() || undefined })
      onDone()
    } catch (e: any) { setErr(e?.response?.data?.detail ?? '建立失敗'); setLoading(false) }
  }

  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" style={{ width: 420 }} onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">New Experiment</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <div className="adm-form-group">
            <label className="adm-form-label">Dataset *</label>
            <select className="adm-form-select" value={datasetId} onChange={e => setDatasetId(e.target.value)}>
              <option value="">— 選擇 Dataset —</option>
              {datasets.map(d => <option key={d.dataset_id} value={d.dataset_id}>{d.name} ({d.item_count ?? '?'} items)</option>)}
            </select>
          </div>
          <div className="adm-form-group">
            <label className="adm-form-label">Run Name *</label>
            <input className="adm-form-input" placeholder="e.g. v2-prompt-test" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div className="adm-form-group">
            <label className="adm-form-label">Prompt Version（選填）</label>
            <input className="adm-form-input" placeholder="e.g. langfuse:8" value={promptVersion} onChange={e => setPromptVersion(e.target.value)} />
          </div>
          {err && <div style={{ fontSize: 12, color: 'var(--adm-red)', marginTop: 4 }}>{err}</div>}
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-primary" disabled={!datasetId || !name.trim() || loading} onClick={handleCreate}>
            {loading ? '建立中…' : '▶ 建立並執行'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 40,  resizable: false },  // 0 checkbox
  { width: 140, resizable: false },  // 1 Run ID
  { width: 160, resizable: true  },  // 2 Name
  { width: 140, resizable: true  },  // 3 Dataset
  { width: 100, resizable: false },  // 4 Status
  { width: 80,  resizable: false },  // 5 Progress
  { width: 70,  resizable: false },  // 6 Prompt Ver
  { width: 120, resizable: false },  // 7 建立時間
  { width: 100, resizable: false },  // 8 動作
]

export default function ExperimentsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [showModal, setShowModal] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-experiments-col-widths')
  const sort = useTableSort<ExperimentRunData>({
    created_at:      r => r.created_at,
    succeeded_count: r => r.succeeded_count,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['experiment-runs', statusFilter],
    queryFn: () => getExperimentRuns({ status: statusFilter }),
    refetchInterval: 8000,
  })
  const runs: ExperimentRunData[] = data?.runs ?? []

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })

  const canCompare = selected.size === 2 && [...selected].every(id => {
    const r = runs.find(r => r.experiment_run_id === id)
    return r?.status === 'completed'
  })

  const handleCompare = () => {
    const [a, b] = Array.from(selected)
    navigate(`/admin/experiments/compare?a=${a}&b=${b}`)
  }

  const handleEval = async (expRunId: string, e: React.MouseEvent) => {
    e.stopPropagation()
    await triggerExperimentEval(expRunId)
    qc.invalidateQueries({ queryKey: ['experiment-runs'] })
  }

  const handleDone = () => {
    setShowModal(false)
    setTimeout(() => qc.invalidateQueries({ queryKey: ['experiment-runs'] }), 1500)
  }

  return (
    <div>
      <PageHeader
        title="Experiments"
        subtitle="Dataset Replay — 用固定資料集比較不同版本的輸出品質"
        actions={
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="adm-btn adm-btn-secondary" disabled={!canCompare} onClick={handleCompare}>
              比較（{selected.size}/2）
            </button>
            <button className="adm-btn adm-btn-primary" onClick={() => setShowModal(true)}>
              <TestTube size={14} /> + New Experiment
            </button>
          </div>
        }
      />

      {selected.size > 0 && (
        <BulkActionBar count={selected.size}
          actions={[{ label: '比較兩個', onClick: handleCompare, variant: 'primary', disabled: !canCompare }]}
          onClear={() => setSelected(new Set())}
        />
      )}

      {/* Filter */}
      <div className="adm-filters">
        {['all', 'pending', 'running', 'completed', 'partial', 'failed'].map(s => (
          <button key={s} className={`adm-btn adm-btn-sm ${statusFilter === s ? 'adm-btn-primary' : 'adm-btn-secondary'}`}
            onClick={() => setStatusFilter(s)}
          >{s === 'all' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}</button>
        ))}
        <span className="adm-filter-spacer" />
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{data?.total ?? 0} 筆</span>
      </div>

      {/* Table */}
      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th className="adm-col-checkbox" />
              <th>Run ID<span {...div(1)} /></th>
              <th>Name<span {...div(2)} /></th>
              <th>Dataset<span {...div(3)} /></th>
              <th>Status<span {...div(4)} /></th>
              <th {...sort.th('succeeded_count')}>Progress{sort.ind('succeeded_count')}<span {...div(5)} /></th>
              <th>Prompt Ver<span {...div(6)} /></th>
              <th {...sort.th('created_at')}>建立時間{sort.ind('created_at')}<span {...div(7)} /></th>
              <th>動作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={9} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : runs.length === 0 ? (
              <tr><td colSpan={9} className="adm-table-empty">尚無 experiment — 按「+ New Experiment」開始</td></tr>
            ) : sort.apply(runs).slice((page-1)*pageSize, page*pageSize).map(run => {
              const isSelected = selected.has(run.experiment_run_id)
              const hasEval = !!run.metadata?.eval_run_id || run.items?.some(i => i.eval_run_id)
              return (
                <tr key={run.experiment_run_id}
                  className={`adm-row--clickable${isSelected ? ' adm-row--selected' : ''}`}
                  onClick={() => navigate(`/admin/experiments/${run.experiment_run_id}`)}>
                  <td onClick={e => { e.stopPropagation(); toggle(run.experiment_run_id) }}>
                    <input type="checkbox" className="adm-checkbox" readOnly checked={isSelected} />
                  </td>
                  <td className="adm-cell-mono">{run.experiment_run_id.slice(4, 18)}…</td>
                  <td style={{ fontWeight: 500 }}>{run.name}</td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{run.dataset_id ? run.dataset_id.slice(0, 12) + '…' : '—'}</td>
                  <td><StatusBadge status={run.status} /></td>
                  <td className="adm-progress-text">{run.succeeded_count}/{run.total_count}</td>
                  <td className="adm-cell-mono" style={{ fontSize: 10 }}>{run.prompt_version ?? '—'}</td>
                  <td className="adm-cell-mono">{fmtDate(run.created_at)}</td>
                  <td>
                    <div className="adm-action-row">
                      {run.status === 'completed' && !hasEval && (
                        <button className="adm-btn adm-btn-secondary adm-btn-sm"
                          onClick={e => handleEval(run.experiment_run_id, e)}>
                          評估
                        </button>
                      )}
                      <button className="adm-btn adm-btn-ghost adm-btn-sm"
                        onClick={e => { e.stopPropagation(); navigate(`/admin/experiments/${run.experiment_run_id}`) }}>
                        詳情
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <TablePagination
        page={page} pageCount={Math.max(1, Math.ceil(runs.length / pageSize))}
        pageSize={pageSize} total={runs.length}
        onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
      />
      {showModal && <NewExpModal onClose={() => setShowModal(false)} onDone={handleDone} />}
    </div>
  )
}
