import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getSessionDetail, type TraceItem } from '../../api'
import './SessionDetailPage.css'

// ── Helpers ──────────────────────────────────────────────────────────────────

function formatDuration(s: number | null) {
  if (s === null) return '—'
  if (s < 60) return `${s.toFixed(1)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}

function formatTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

function formatTs(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function QualityDot({ score }: { score?: number | null }) {
  if (score == null) return null
  const color = score >= 4 ? '#16a34a' : score >= 3 ? '#d97706' : '#dc2626'
  return (
    <span className="sd-quality-dot" style={{ background: color }}>
      {score.toFixed(1)}
    </span>
  )
}

function IntentBadge({ type }: { type?: string | null }) {
  if (!type) return null
  return <span className={`sd-intent-badge sd-intent-badge--${type}`}>{type}</span>
}

// ── IO Preview ───────────────────────────────────────────────────────────────

function IOPreview({ trace }: { trace: TraceItem }) {
  const display = trace.display
  if (!display) {
    return <p className="sd-io-empty">No input / output recorded</p>
  }

  // Extract user message from display.messages
  const humanMsg = display.messages?.find((m) => m.role === 'human')
  const userText = humanMsg && 'content' in humanMsg ? humanMsg.content : null

  // Answer
  const answer = display.answer

  return (
    <div className="sd-io">
      {userText && (
        <div className="sd-io-block sd-io-input">
          <span className="sd-io-label">Input</span>
          <p className="sd-io-text">{userText}</p>
        </div>
      )}
      {answer && (
        <div className="sd-io-block sd-io-output">
          <span className="sd-io-label">Output</span>
          <p className="sd-io-text sd-io-text--clamp">{answer}</p>
        </div>
      )}
      {!userText && !answer && (
        <p className="sd-io-empty">No input / output recorded</p>
      )}
    </div>
  )
}

// ── Trace Card ───────────────────────────────────────────────────────────────

function TraceCard({ trace, idx }: { trace: TraceItem; idx: number }) {
  const latency = trace.latency != null ? `${trace.latency.toFixed(1)}s` : null

  return (
    <div className="sd-card">
      {/* Left: IO */}
      <div className="sd-card-io">
        <IOPreview trace={trace} />
      </div>

      {/* Divider */}
      <div className="sd-card-divider" />

      {/* Right: Metadata */}
      <div className="sd-card-meta">
        <div className="sd-card-meta-row">
          <span className="sd-card-idx">#{idx + 1}</span>
          <IntentBadge type={trace.original_intent ?? trace.resolved_intent} />
          {trace.quality_score != null && <QualityDot score={trace.quality_score} />}
        </div>
        <div className="sd-card-meta-item">
          <span className="sd-card-meta-label">Agent</span>
          <span>{trace.agent_name ?? '—'}</span>
        </div>
        {trace.prompt_name && (
          <div className="sd-card-meta-item">
            <span className="sd-card-meta-label">Prompt</span>
            <span className="sd-card-meta-mono">{trace.prompt_name}</span>
          </div>
        )}
        <div className="sd-card-meta-item">
          <span className="sd-card-meta-label">Time</span>
          <span>{formatTs(trace.start_time)}</span>
        </div>
        {latency && (
          <div className="sd-card-meta-item">
            <span className="sd-card-meta-label">Latency</span>
            <span>{latency}</span>
          </div>
        )}
        {trace.status === 'error' && trace.error && (
          <div className="sd-card-error" title={trace.error}>
            ✗ Error
          </div>
        )}
      </div>
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export default function SessionDetailPage() {
  const { threadId } = useParams<{ threadId: string }>()
  const navigate = useNavigate()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['session-detail', threadId],
    queryFn: () => getSessionDetail(threadId!),
    enabled: !!threadId,
  })

  if (isLoading) {
    return <div className="sd-loading">Loading session…</div>
  }
  if (isError || !data) {
    return <div className="sd-error">Session not found</div>
  }

  const session = data
  const traces: TraceItem[] = (data as any).traces ?? []
  const totalTokens = session.total_tokens
  const duration = session.duration_seconds

  return (
    <div className="sd-page">
      {/* Sticky header */}
      <div className="sd-header">
        <div className="sd-header-left">
          <button className="sd-back-btn" onClick={() => navigate('/admin/sessions')}>
            ← Sessions
          </button>
          <div>
            <h1 className="sd-thread-id" title={session.thread_id}>
              {session.thread_id.length > 40
                ? session.thread_id.slice(0, 40) + '…'
                : session.thread_id}
            </h1>
            {session.task_type && (
              <IntentBadge type={session.task_type} />
            )}
          </div>
        </div>
        <div className="sd-header-badges">
          <span className="sd-badge">{traces.length} traces</span>
          <span className="sd-badge">{formatTokens(totalTokens)} tokens</span>
          {session.avg_quality_score != null && (
            <span className="sd-badge">
              Quality {session.avg_quality_score.toFixed(1)}
            </span>
          )}
          <span className="sd-badge">{formatDuration(duration)}</span>
        </div>
      </div>

      {/* Trace list */}
      <div className="sd-list">
        {traces.length === 0 && (
          <div className="sd-empty">No traces found for this session</div>
        )}
        {traces.map((trace, i) => (
          <TraceCard key={trace.id} trace={trace} idx={i} />
        ))}
      </div>
    </div>
  )
}
