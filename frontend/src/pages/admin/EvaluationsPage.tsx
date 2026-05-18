import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical } from 'lucide-react'
import { getEvalRuns, createEvalRun, type EvalRun } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { StatusBadge } from '../../components/admin/StatusBadge'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function RunEvalModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [limit, setLimit] = useState(50)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<string | null>(null)

  const handleSubmit = async () => {
    setLoading(true)
    try {
      const res = await createEvalRun({ limit })
      setResult(res.message ?? '已啟動批次評分')
      setTimeout(onDone, 1500)
    } catch { setResult('啟動失敗'); setLoading(false) }
  }

  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" style={{ width: 380 }} onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">Run Batch Evaluation</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <p style={{ fontSize: 13, color: 'var(--adm-text-2)', marginBottom: 16 }}>
            自動選取最近尚未評分的 trace，送入 AI 評分管線。
          </p>
          <div className="adm-form-group">
            <label className="adm-form-label">最多評分筆數</label>
            <input className="adm-form-input" type="number" min={1} max={200} value={limit}
              onChange={e => setLimit(Number(e.target.value))} />
            <div className="adm-form-hint">系統會從未評分的 trace 中自動挑選</div>
          </div>
          {result && <div style={{ fontSize: 12, color: result.includes('失敗') ? 'var(--adm-red)' : 'var(--adm-green)', marginTop: 4 }}>{result}</div>}
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-primary" disabled={loading} onClick={handleSubmit}>
            {loading ? '啟動中…' : `▶ 評分最多 ${limit} 筆`}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 150, resizable: true  },  // 0 Run ID
  { width: 200, resizable: true  },  // 1 Name
  { width: 100, resizable: false },  // 2 Status
  { width: 80,  resizable: false },  // 3 Progress
  { width: 130, resizable: false },  // 4 Avg Score
  { width: 120, resizable: false },  // 5 建立時間
  { width: 100, resizable: false },  // 6 動作
]

export default function EvaluationsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState('all')
  const [showModal, setShowModal] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-evaluations-col-widths')
  const sort = useTableSort<EvalRun>({
    avg_score:  r => r.avg_score,
    created_at: r => r.created_at,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['eval-runs', statusFilter],
    queryFn: () => getEvalRuns({ status: statusFilter }),
    refetchInterval: 8000,
  })
  const runs: EvalRun[] = data?.runs ?? []

  const handleDone = () => {
    setShowModal(false)
    setTimeout(() => qc.invalidateQueries({ queryKey: ['eval-runs'] }), 1500)
  }

  return (
    <div>
      <PageHeader
        title="Eval Runs"
        subtitle="批次評分 trace，追蹤各維度品質"
        actions={
          <button className="adm-btn adm-btn-primary" onClick={() => setShowModal(true)}>
            <FlaskConical size={14} /> + Run Evaluation
          </button>
        }
      />

      {/* Filter */}
      <div className="adm-filters">
        {['all', 'pending', 'running', 'completed', 'failed'].map(s => (
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
              <th>Run ID<span {...div(0)} /></th>
              <th>Name<span {...div(1)} /></th>
              <th>Status<span {...div(2)} /></th>
              <th>Progress<span {...div(3)} /></th>
              <th {...sort.th('avg_score')}>Avg Score{sort.ind('avg_score')}<span {...div(4)} /></th>
              <th {...sort.th('created_at')}>建立時間{sort.ind('created_at')}<span {...div(5)} /></th>
              <th>動作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={7} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : runs.length === 0 ? (
              <tr><td colSpan={7} className="adm-table-empty">尚無 eval run — 按「+ Run Evaluation」開始</td></tr>
            ) : sort.apply(runs).slice((page-1)*pageSize, page*pageSize).map(run => (
              <tr key={run.eval_run_id} className="adm-row--clickable" onClick={() => navigate(`/admin/evaluations/${run.eval_run_id}`)}>
                <td className="adm-cell-mono">{run.eval_run_id.slice(0, 14)}…</td>
                <td style={{ fontWeight: 500 }}>{run.name}</td>
                <td><StatusBadge status={run.status} /></td>
                <td className="adm-progress-text">{run.succeeded_count}/{run.total_count}</td>
                <td><ScoreBar value={run.avg_score} /></td>
                <td className="adm-cell-mono">{fmtDate(run.created_at)}</td>
                <td>
                  <div className="adm-action-row">
                    <button className="adm-btn adm-btn-secondary adm-btn-sm"
                      onClick={e => { e.stopPropagation(); navigate(`/admin/evaluations/${run.eval_run_id}`) }}>
                      詳情
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <TablePagination
        page={page} pageCount={Math.max(1, Math.ceil(runs.length / pageSize))}
        pageSize={pageSize} total={runs.length}
        onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
      />
      {showModal && <RunEvalModal onClose={() => setShowModal(false)} onDone={handleDone} />}
    </div>
  )
}
