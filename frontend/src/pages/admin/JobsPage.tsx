import { useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useJobStore } from '../../store/jobStore'
import { useJobSSE } from '../../hooks/useJobSSE'
import { cancelJob, clearJobHistory, getJobs, type JobItem } from '../../api'
import './JobsPage.css'

const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued', running: 'Running', done: 'Done',
  error: 'Error', cancelled: 'Cancelled',
}

function StatusBadge({ status }: { status: JobItem['status'] }) {
  return <span className={`jobs-status jobs-status--${status}`}>{STATUS_LABEL[status] ?? status}</span>
}

function JobTypeLabel({ type }: { type?: string }) {
  if (!type) return <span className="jobs-type-empty">—</span>
  const short = type.replace('extract_', '').replace('parse_', 'parse ').replace('_', ' ')
  return <span className="jobs-type">{short}</span>
}

function formatTs(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

const ACTIVE = new Set(['queued', 'running'])

function sortJobs(jobs: JobItem[]): JobItem[] {
  return [...jobs].sort((a, b) => {
    const aActive = ACTIVE.has(a.status) ? 0 : 1
    const bActive = ACTIVE.has(b.status) ? 0 : 1
    if (aActive !== bActive) return aActive - bActive
    const aTs = a.started_at ?? a.updated_at ?? ''
    const bTs = b.started_at ?? b.updated_at ?? ''
    return bTs.localeCompare(aTs)
  })
}

export default function JobsPage() {
  const qc = useQueryClient()
  const storeJobs = useJobStore(s => s.jobs)
  const dummyRef = useRef<number | null>(null)

  // Keep SSE connection alive; updates jobStore automatically
  useJobSSE({
    onItemsDone: () => qc.invalidateQueries({ queryKey: ['jobs'] }),
    onParseDone: () => {},
    onExtractDone: () => {},
    selectedIdRef: dummyRef,
  })

  // Initial load (fallback if SSE hasn't fired yet)
  useQuery({ queryKey: ['jobs'], queryFn: getJobs, staleTime: 5000 })

  const jobs = sortJobs(storeJobs.length > 0 ? storeJobs : [])
  const activeCount = jobs.filter(j => ACTIVE.has(j.status)).length

  const handleCancel = async (docId: number) => {
    await cancelJob(docId)
    qc.invalidateQueries({ queryKey: ['jobs'] })
  }

  const handleClear = async () => {
    await clearJobHistory()
    qc.invalidateQueries({ queryKey: ['jobs'] })
  }

  return (
    <div className="jobs-page">
      <div className="jobs-header">
        <div>
          <h1 className="jobs-title">Jobs</h1>
          {activeCount > 0 && (
            <span className="jobs-active-badge">{activeCount} active</span>
          )}
        </div>
        <button className="jobs-clear-btn" onClick={handleClear}>
          Clear history
        </button>
      </div>

      <div className="jobs-table-wrapper">
        <table className="jobs-table">
          <thead>
            <tr>
              <th>Filename</th>
              <th>Job Type</th>
              <th>Status</th>
              <th>Stage</th>
              <th>Started</th>
              <th>Updated</th>
              <th>Error</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.length === 0 && (
              <tr>
                <td colSpan={8} className="jobs-empty">No jobs found</td>
              </tr>
            )}
            {jobs.map((job) => (
              <tr key={`${job.doc_id}-${job.job_type}`} className={`jobs-row jobs-row--${job.status}`}>
                <td className="jobs-td-file" title={job.filename}>
                  {job.filename.length > 36 ? job.filename.slice(0, 36) + '…' : job.filename}
                </td>
                <td><JobTypeLabel type={job.job_type} /></td>
                <td><StatusBadge status={job.status} /></td>
                <td className="jobs-td-stage">{job.stage ?? '—'}</td>
                <td className="jobs-td-time">{formatTs(job.started_at)}</td>
                <td className="jobs-td-time">{formatTs(job.updated_at)}</td>
                <td className="jobs-td-error" title={job.error ?? ''}>
                  {job.error ? job.error.slice(0, 50) + (job.error.length > 50 ? '…' : '') : '—'}
                </td>
                <td className="jobs-td-action">
                  {ACTIVE.has(job.status) && (
                    <button
                      className="jobs-cancel-btn"
                      onClick={() => handleCancel(job.doc_id)}
                      title="Cancel"
                    >
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
  )
}
