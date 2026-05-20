/**
 * System Jobs — 統一顯示所有後台作業
 *  1. Document pipeline jobs (SSE real-time)
 *  2. Evaluation runs (polled)
 *  3. Experiment runs (polled)
 */
import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { cancelJob, clearJobHistory, getJobs, getEvalRuns, getExperimentRuns, type JobItem, type EvalRun, type ExperimentRunData } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useJobStore } from '../../stores/jobStore'
import { useJobSSE } from '../../hooks/useJobSSE'
import { StatusBadge } from '../../components/admin/StatusBadge'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtTs(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function elapsed(iso?: string | null): string {
  if (!iso) return '—'
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  return `${Math.floor(s / 3600)}h ago`
}

const DOC_STATUS_MAP: Record<string, string> = {
  queued: 'pending', running: 'running', done: 'completed', error: 'failed', cancelled: 'skipped',
}
const ACTIVE = new Set(['queued', 'running'])

function sortDocJobs(jobs: JobItem[]): JobItem[] {
  return [...jobs].sort((a, b) => {
    const aA = ACTIVE.has(a.status) ? 0 : 1
    const bA = ACTIVE.has(b.status) ? 0 : 1
    if (aA !== bA) return aA - bA
    return (b.started_at ?? b.updated_at ?? '').localeCompare(a.started_at ?? a.updated_at ?? '')
  })
}

// ── Section header ────────────────────────────────────────────────────────────

function SectionHeader({ icon, title, count, active, onRefresh }: {
  icon: string; title: string; count: number; active: number; onRefresh?: () => void
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
      <span style={{ fontSize: 14 }}>{icon}</span>
      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--adm-text)' }}>{title}</span>
      {active > 0 && <span className="adm-badge adm-badge--running">{active} active</span>}
      <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{count} total</span>
      {onRefresh && (
        <button className="adm-btn-icon" onClick={onRefresh} style={{ marginLeft: 'auto' }} title="Refresh">
          <RefreshCw size={13} />
        </button>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

// ── Column layouts ─────────────────────────────────────────────────────────────
const COLS_DOC: ColDef[] = [
  { width: 240, resizable: true  },  // 0 Filename
  { width: 110, resizable: false },  // 1 Job Type
  { width: 100, resizable: false },  // 2 Status
  { width: 90,  resizable: false },  // 3 Stage
  { width: 120, resizable: false },  // 4 Started
  { width: 80,  resizable: false },  // 5 Elapsed
  { width: 160, resizable: true  },  // 6 Error
  { width: 40,  resizable: false },  // 7 Actions
]
const COLS_EVAL: ColDef[] = [
  { width: 200, resizable: true  },  // 0 Name
  { width: 100, resizable: false },  // 1 Status
  { width: 80,  resizable: false },  // 2 Progress
  { width: 120, resizable: false },  // 3 Started
  { width: 80,  resizable: false },  // 4 Elapsed
  { width: 100, resizable: false },  // 5 Actions
]
const COLS_EXP: ColDef[] = [
  { width: 200, resizable: true  },  // 0 Name
  { width: 120, resizable: true  },  // 1 Dataset
  { width: 100, resizable: false },  // 2 Status
  { width: 80,  resizable: false },  // 3 Progress
  { width: 120, resizable: false },  // 4 Started
  { width: 80,  resizable: false },  // 5 Elapsed
  { width: 100, resizable: false },  // 6 Actions
]

export default function JobsPage() {
  const navigate   = useNavigate()
  const qc         = useQueryClient()
  const storeJobs  = useJobStore(s => s.jobs)
  const dummyRef   = useRef<number | null>(null)
  const [filter, setFilter] = useState<'all' | 'active'>('all')
  const { widths: wJD, div: dJD } = useLinkedColumnResize(COLS_DOC, 'adm-jobs-doc-col-widths')
  const { widths: wJE, div: dJE } = useLinkedColumnResize(COLS_EVAL, 'adm-jobs-eval-col-widths')
  const { widths: wJX, div: dJX } = useLinkedColumnResize(COLS_EXP, 'adm-jobs-exp-col-widths')

  // Document jobs via SSE
  useJobSSE({ onItemsDone: () => qc.invalidateQueries({ queryKey: ['jobs'] }), onParseDone: () => {}, onExtractDone: () => {}, selectedIdRef: dummyRef })
  useQuery({ queryKey: ['jobs'], queryFn: getJobs, staleTime: 5000 })

  // Eval + Experiment runs (polled)
  const { data: evalData, refetch: refetchEval } = useQuery({
    queryKey: ['eval-runs-jobs'],
    queryFn: () => getEvalRuns({ limit: 30 }),
    refetchInterval: 8000,
  })
  const { data: expData, refetch: refetchExp } = useQuery({
    queryKey: ['experiment-runs-jobs'],
    queryFn: () => getExperimentRuns({ limit: 30 }),
    refetchInterval: 8000,
  })

  const docJobs  = sortDocJobs(storeJobs.length > 0 ? storeJobs : [])
  const evalRuns: EvalRun[]             = evalData?.runs ?? []
  const expRuns:  ExperimentRunData[]   = expData?.runs ?? []

  const activeDoc  = docJobs.filter(j => ACTIVE.has(j.status)).length
  const activeEval = evalRuns.filter(r => r.status === 'running' || r.status === 'pending').length
  const activeExp  = expRuns.filter(r => r.status === 'running' || r.status === 'pending').length
  const totalActive = activeDoc + activeEval + activeExp

  const shownDocs  = filter === 'active' ? docJobs.filter(j => ACTIVE.has(j.status)) : docJobs
  const shownEvals = filter === 'active' ? evalRuns.filter(r => r.status !== 'completed') : evalRuns
  const shownExps  = filter === 'active' ? expRuns.filter(r => r.status !== 'completed') : expRuns

  return (
    <div>
      {/* Filter + summary */}
      <div className="adm-filters" style={{ marginBottom: 16 }}>
        <button className={`adm-btn adm-btn-sm ${filter === 'all' ? 'adm-btn-primary' : 'adm-btn-secondary'}`} onClick={() => setFilter('all')}>All</button>
        <button className={`adm-btn adm-btn-sm ${filter === 'active' ? 'adm-btn-primary' : 'adm-btn-secondary'}`} onClick={() => setFilter('active')}>
          Active {totalActive > 0 && `(${totalActive})`}
        </button>
        <span className="adm-filter-spacer" />
        <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={() => { clearJobHistory(); qc.invalidateQueries({ queryKey: ['jobs'] }) }}>
          清除文件記錄
        </button>
      </div>

      {/* ── Document pipeline jobs ── */}
      <div style={{ marginBottom: 24 }}>
        <SectionHeader icon="📄" title="Document Pipeline" count={shownDocs.length} active={activeDoc} />
        <div className="adm-table-wrap">
          <table className="adm-table" style={{ tableLayout: 'fixed' }}>
            <colgroup>{wJD.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
            <thead>
              <tr>
                <th>Filename<span {...dJD(0)} /></th>
                <th>Job Type<span {...dJD(1)} /></th>
                <th>Status<span {...dJD(2)} /></th>
                <th>Stage<span {...dJD(3)} /></th>
                <th>Started<span {...dJD(4)} /></th>
                <th>Elapsed<span {...dJD(5)} /></th>
                <th>Error<span {...dJD(6)} /></th>
                <th />
              </tr>
            </thead>
            <tbody>
              {shownDocs.length === 0 ? (
                <tr><td colSpan={8} className="adm-table-empty">No document jobs</td></tr>
              ) : shownDocs.map((job, i) => (
                <tr key={job.job_id ?? `${job.doc_id}-${i}`} className={job.status === 'error' ? 'adm-row--danger' : ''}>
                  <td style={{ fontSize: 12, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }} title={job.filename}>
                    {job.filename.length > 40 ? job.filename.slice(0, 40) + '…' : job.filename}
                  </td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-2)' }}>
                    {job.job_type?.replace(/_/g, ' ') ?? '—'}
                  </td>
                  <td><StatusBadge status={DOC_STATUS_MAP[job.status] ?? job.status} label={job.status} /></td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-2)' }}>{job.stage ?? '—'}</td>
                  <td className="adm-cell-mono">{fmtTs(job.started_at)}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{elapsed(job.started_at)}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-red)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis' }} title={job.error ?? ''}>
                    {job.error ? job.error.slice(0, 50) + (job.error.length > 50 ? '…' : '') : '—'}
                  </td>
                  <td>
                    {ACTIVE.has(job.status) && (
                      <button className="adm-btn-icon" title="Cancel"
                        onClick={() => { cancelJob(job.doc_id); qc.invalidateQueries({ queryKey: ['jobs'] }) }}>
                        ✕
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Eval runs ── */}
      <div style={{ marginBottom: 24 }}>
        <SectionHeader icon="🔬" title="Evaluation Runs" count={shownEvals.length} active={activeEval} onRefresh={() => refetchEval()} />
        <div className="adm-table-wrap">
          <table className="adm-table" style={{ tableLayout: 'fixed' }}>
            <colgroup>{wJE.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
            <thead>
              <tr>
                <th>Name<span {...dJE(0)} /></th>
                <th>Status<span {...dJE(1)} /></th>
                <th>Progress<span {...dJE(2)} /></th>
                <th>Started<span {...dJE(3)} /></th>
                <th>Elapsed<span {...dJE(4)} /></th>
                <th>動作</th>
              </tr>
            </thead>
            <tbody>
              {shownEvals.length === 0 ? (
                <tr><td colSpan={6} className="adm-table-empty">No eval runs</td></tr>
              ) : shownEvals.map(run => (
                <tr key={run.eval_run_id} className="adm-row--clickable" onClick={() => navigate(`/admin/evaluations/${run.eval_run_id}`)}>
                  <td style={{ fontWeight: 500 }}>{run.name}</td>
                  <td><StatusBadge status={run.status} /></td>
                  <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{run.succeeded_count}/{run.total_count}</td>
                  <td className="adm-cell-mono">{fmtTs(run.started_at)}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{elapsed(run.started_at)}</td>
                  <td><div className="adm-action-row"><span className="adm-cell-link" style={{ fontSize: 11 }}>→ 詳情</span></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Experiment runs ── */}
      <div>
        <SectionHeader icon="🧪" title="Experiment Runs" count={shownExps.length} active={activeExp} onRefresh={() => refetchExp()} />
        <div className="adm-table-wrap">
          <table className="adm-table" style={{ tableLayout: 'fixed' }}>
            <colgroup>{wJX.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
            <thead>
              <tr>
                <th>Name<span {...dJX(0)} /></th>
                <th>Dataset<span {...dJX(1)} /></th>
                <th>Status<span {...dJX(2)} /></th>
                <th>Progress<span {...dJX(3)} /></th>
                <th>Started<span {...dJX(4)} /></th>
                <th>Elapsed<span {...dJX(5)} /></th>
                <th>動作</th>
              </tr>
            </thead>
            <tbody>
              {shownExps.length === 0 ? (
                <tr><td colSpan={7} className="adm-table-empty">No experiment runs</td></tr>
              ) : shownExps.map(run => (
                <tr key={run.experiment_run_id} className="adm-row--clickable" onClick={() => navigate(`/admin/experiments/${run.experiment_run_id}`)}>
                  <td style={{ fontWeight: 500 }}>{run.name}</td>
                  <td className="adm-cell-mono" style={{ fontSize: 11 }}>{run.dataset_id?.slice(-10) ?? '—'}</td>
                  <td><StatusBadge status={run.status} /></td>
                  <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{run.succeeded_count}/{run.total_count}</td>
                  <td className="adm-cell-mono">{fmtTs(run.started_at)}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{elapsed(run.started_at)}</td>
                  <td><div className="adm-action-row"><span className="adm-cell-link" style={{ fontSize: 11 }}>→ 詳情</span></div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
