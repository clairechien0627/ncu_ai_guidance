import { useState } from 'react'
import { Trash2, X } from 'lucide-react'
import { useJobStore, selectJobs } from '../store/jobStore'
import { cancelJob, clearJobHistory, type JobItem } from '../api'
import { getDisplayTitle, relativeTime } from '../utils/summaryUtils'

function getJobActionLabel(jobType?: string | null) {
  if (jobType === 'extract_step1') return 'Step 1'
  if (jobType === 'extract_step2') return 'Step 2'
  if (jobType === 'extract_step3') return 'Step 3'
  if (jobType === 'extract') return '摘要'
  if (jobType?.startsWith('parse_')) return '解析'
  return '嵌入'
}

function shouldShowStageInLog(stage: string) {
  return stage !== 'RAG 分析中' && stage !== 'Step 1 研究檢索中'
}

function shortStage(stage: string, maxLength = 72) {
  return stage.length > maxLength ? `${stage.slice(0, maxLength)}...` : stage
}

function jobKey(job: JobItem, index = 0) {
  return job.job_id ?? `${job.doc_id}_${job.status}_${job.completed_at ?? index}`
}

interface JobDrawerProps {
  open: boolean
  onClose: () => void
  onRefreshJobs: () => void
  onRefreshItems: () => void
  setParsingTabs: React.Dispatch<React.SetStateAction<Set<string>>>
}

export function JobDrawer({ open, onClose, onRefreshJobs, onRefreshItems, setParsingTabs }: JobDrawerProps) {
  const jobs = useJobStore(selectJobs)
  const [dismissedJobKeys, setDismissedJobKeys] = useState<Set<string>>(new Set())
  const [expandedJobLogs, setExpandedJobLogs] = useState<Set<string>>(new Set())

  const visibleJobs = jobs.filter((job, index) => !dismissedJobKeys.has(jobKey(job, index)))
  const activeJobs = visibleJobs.filter(job => job.status === 'queued' || job.status === 'running')
  const doneJobs = visibleJobs
    .filter(job => job.status === 'done' || job.status === 'error' || job.status === 'cancelled')
    .slice()
    .sort((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? ''))

  const renderJobRow = (job: JobItem, index: number) => {
    const logKey = `${job.job_id ?? job.doc_id}_${job.job_type ?? 'job'}`
    const stageLog = (job.stage_log ?? []).filter(shouldShowStageInLog)
    const compactLog = stageLog.filter((stage, stageIndex) => stageIndex === 0 || stage !== stageLog[stageIndex - 1])
    const isRunning = job.status === 'running'
    // Running: always auto-expand (no toggle — avoids accidentally pinning log open after completion)
    // Done/error/cancelled: user-controlled via expandedJobLogs
    const isLogExpanded = isRunning || expandedJobLogs.has(logKey)
    // While running, show the latest stage as status text
    const currentStage = isRunning
      ? shortStage(compactLog.length > 0 ? (compactLog[compactLog.length - 1] ?? job.stage ?? '執行中') : (job.stage ?? '執行中'))
      : null
    const statusText =
      job.status === 'error' || (job.status === 'done' && job.error)
        ? `失敗${job.completed_at ? ` · ${relativeTime(job.completed_at)}` : ''}`
        : job.status === 'done'
        ? `已完成${getJobActionLabel(job.job_type)}${job.completed_at ? ` · ${relativeTime(job.completed_at)}` : ''}`
        : job.status === 'cancelled'
        ? `已取消${getJobActionLabel(job.job_type)}`
        : job.status === 'running'
        ? (currentStage ?? '執行中')
        : `等待${getJobActionLabel(job.job_type)}`

    return (
      <div
        key={jobKey(job, index)}
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          padding: '11px 16px',
          borderBottom: '1px solid rgba(236,241,247,0.95)',
          opacity: activeJobs.includes(job) ? 1 : 0.66,
          background: activeJobs.includes(job) ? 'rgba(255,255,255,0.72)' : 'rgba(248,250,252,0.72)',
        }}
      >
        <span
          style={{ width: 10, height: 10 }}
          className={[
            'job-status-dot',
            job.status === 'error' || (job.status === 'done' && job.error) ? 'error'
              : job.status === 'done' ? 'done'
              : job.status === 'cancelled' ? 'cancelled'
              : job.status === 'running'
                ? (job.job_type?.startsWith('extract') ? 'running-extract' : 'running')
              : 'queued',
          ].join(' ')}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, overflow: 'hidden' }}>
            <span style={{ fontSize: 13, color: '#334155', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
              {getDisplayTitle(job.filename)}
            </span>
            <span style={{ fontSize: 10, color: '#94a3b8', flexShrink: 0, fontFamily: 'monospace' }}>#{job.doc_id}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, minWidth: 0, marginTop: 1 }}>
            <span style={{ fontSize: 11, color: '#94a3b8', whiteSpace: 'normal', overflowWrap: 'anywhere', lineHeight: 1.45, flex: 1 }}>
              {statusText}
            </span>
            {compactLog.length > 0 && !isRunning ? (
              <button
                type="button"
                onClick={() => {
                  setExpandedJobLogs(prev => {
                    const next = new Set(prev)
                    if (next.has(logKey)) next.delete(logKey)
                    else next.add(logKey)
                    return next
                  })
                }}
                style={{
                  border: 'none',
                  background: 'rgba(226,232,240,0.75)',
                  color: '#64748b',
                  borderRadius: 999,
                  fontSize: 10,
                  padding: '2px 7px',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                {isLogExpanded ? '收合' : `紀錄 ${compactLog.length}`}
              </button>
            ) : null}
          </div>
          {isLogExpanded && compactLog.length > 0 ? (
            <div style={{ marginTop: 7, display: 'flex', flexDirection: 'column', gap: 3, fontSize: 10, color: '#64748b', lineHeight: 1.45 }}>
              {compactLog.map((stage, stageIndex) => (
                <span key={`${stage}_${stageIndex}`} style={{ whiteSpace: 'normal', overflowWrap: 'anywhere' }}>
                  {stageIndex === compactLog.length - 1 ? '• ' : '  '}
                  {stage}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <button
          className="job-cancel-btn"
          style={{ border: 'none', cursor: 'pointer', borderRadius: 8 }}
          onClick={() => {
            if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
              setDismissedJobKeys(prev => new Set(prev).add(jobKey(job, index)))
              return
            }
            cancelJob(job.doc_id).then(() => {
              setParsingTabs(prev => {
                const next = new Set(prev)
                for (const key of next) {
                  if (key.startsWith(`${job.doc_id}_`)) next.delete(key)
                }
                return next
              })
              onRefreshJobs()
              onRefreshItems()
            }).catch(() => { onRefreshJobs(); onRefreshItems() })
          }}
          title={(job.status === 'done' || job.status === 'error' || job.status === 'cancelled') ? '刪除此紀錄' : '取消工作'}
        >
          <Trash2 size={13} />
        </button>
      </div>
    )
  }

  if (!open) return null

  return (
    <div
      className="summary-job-drawer"
      style={{
        position: 'absolute',
        top: 14,
        right: 14,
        bottom: 14,
        width: 360,
        background: 'rgba(255,255,255,0.94)',
        border: '1px solid rgba(207,219,232,0.95)',
        borderRadius: 24,
        display: 'flex',
        flexDirection: 'column',
        boxShadow: '0 18px 40px rgba(148,163,184,0.22)',
        zIndex: 10,
        backdropFilter: 'blur(18px)',
        overflow: 'hidden',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px', height: 58, borderBottom: '1px solid rgba(236,241,247,0.95)', background: 'rgba(255,255,255,0.58)' }}>
        <span style={{ fontSize: 13, fontWeight: 700, color: '#163257' }}>
          工作佇列{activeJobs.length > 0 ? ` (${activeJobs.length} 筆進行中)` : ''}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {doneJobs.length > 0 && (
            <button
              style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 34, background: '#fff', border: '1px solid rgba(207,219,232,0.95)', borderRadius: 999, color: '#64748b', cursor: 'pointer', fontSize: 11, fontWeight: 600, padding: '0 12px', boxShadow: '0 8px 18px rgba(148,163,184,0.08)' }}
              onClick={() => { if (window.confirm('要清除已完成的工作紀錄嗎？')) clearJobHistory().then(() => onRefreshJobs()).catch(() => {}) }}
            >
              清除
            </button>
          )}
          <button
            style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, background: '#fff', border: '1px solid rgba(226,232,240,0.95)', borderRadius: 999, color: '#64748b', cursor: 'pointer', padding: 0 }}
            onClick={onClose}
            title="關閉"
            aria-label="關閉工作佇列"
          >
            <X size={16} />
          </button>
        </div>
      </div>
      <div style={{ overflowY: 'auto', flex: 1, padding: 6 }}>
        {jobs.length === 0 ? (
          <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: 13 }}>目前沒有工作</div>
        ) : (
          <>
            {activeJobs.map(renderJobRow)}
            {activeJobs.length > 0 && doneJobs.length > 0 && <div style={{ borderTop: '1px solid #e5e7eb', margin: '4px 0' }} />}
            {doneJobs.map(renderJobRow)}
          </>
        )}
      </div>
    </div>
  )
}
