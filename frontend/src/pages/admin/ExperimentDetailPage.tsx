import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getExperimentRunDetail, type ExperimentRunItemData } from '../../api'
import { StatusBadge } from '../../components/admin/StatusBadge'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function outputSummary(output: unknown): string {
  if (output == null) return '—'
  if (typeof output === 'string') return output.slice(0, 100)
  const obj = output as Record<string, unknown>
  const answer = obj?.answer ?? obj?.response ?? obj?.content
  if (typeof answer === 'string') return answer.slice(0, 100)
  return JSON.stringify(output).slice(0, 100)
}

export default function ExperimentDetailPage() {
  const { expRunId } = useParams<{ expRunId: string }>()
  const navigate = useNavigate()

  const { data: run, isLoading } = useQuery({
    queryKey: ['experiment-run-detail', expRunId],
    queryFn: () => getExperimentRunDetail(expRunId!),
    enabled: !!expRunId,
    refetchInterval: (query) => query.state.data?.status === 'running' ? 5000 : false,
  })

  if (isLoading) return <div style={{ padding: 32, color: 'var(--adm-text-3)' }}>載入中…</div>
  if (!run) return <div style={{ padding: 32, color: 'var(--adm-red)' }}>找不到此 Experiment</div>

  const items: ExperimentRunItemData[] = run.items ?? []

  return (
    <div>
      <PageHeader
        title={run.name}
        crumbs={[{ label: 'Experiments', to: '/admin/experiments' }, { label: run.name }]}
        subtitle={`${run.succeeded_count}/${run.total_count} items  ·  ${run.prompt_version ?? 'default prompt'}`}
        actions={
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <StatusBadge status={run.status} />
            {run.status === 'completed' && (
              <button className="adm-btn adm-btn-secondary adm-btn-sm"
                onClick={() => navigate(`/admin/experiments/compare?a=${run.experiment_run_id}&b=`)}>
                比較
              </button>
            )}
          </div>
        }
      />

      {run.last_error && (
        <div style={{ padding: '8px 12px', background: 'var(--adm-red-bg)', color: 'var(--adm-red-text)', borderRadius: 'var(--adm-radius)', fontSize: 12, marginBottom: 12 }}>
          Last error: {run.last_error}
        </div>
      )}

      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}>#</th>
              <th style={{ width: 120 }}>Dataset Item</th>
              <th className="adm-col-status">Status</th>
              <th>Generated Output</th>
              <th style={{ width: 100 }}>Trace</th>
              <th className="adm-col-time">完成時間</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={6} className="adm-table-empty">Items 尚未產生</td></tr>
            ) : items.map((item, idx) => {
              const isFailed = item.status === 'failed'
              return (
                <tr key={item.experiment_item_id} className={isFailed ? 'adm-row--danger' : ''}>
                  <td style={{ color: 'var(--adm-text-3)', fontSize: 11 }}>{idx + 1}</td>
                  <td className="adm-cell-mono">{item.dataset_item_id.slice(-10)}</td>
                  <td><StatusBadge status={item.status} /></td>
                  <td style={{ fontSize: 12, color: isFailed ? 'var(--adm-red)' : 'var(--adm-text-2)' }}>
                    {isFailed ? (item.error ?? 'Error') : outputSummary(item.generated_output)}
                    {!isFailed && outputSummary(item.generated_output).length >= 100 ? '…' : ''}
                  </td>
                  <td className="adm-cell-mono" style={{ cursor: item.trace_id ? 'pointer' : 'default', color: item.trace_id ? 'var(--adm-blue)' : 'var(--adm-text-3)' }}
                    onClick={() => item.trace_id && navigate('/admin/traces')}>
                    {item.trace_id ? item.trace_id.slice(-10) : '—'}
                  </td>
                  <td className="adm-cell-mono">{fmtDate(item.completed_at)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
