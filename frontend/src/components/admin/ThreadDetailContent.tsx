import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ExternalLink } from 'lucide-react'
import { getSessionDetail, type TraceItem } from '../../api'
import { ScoreBar } from './ScoreBar'
import { DrawerPanel } from './DrawerPanel'
import { TraceDetailContent } from './TraceDetailContent'

function fmtDuration(s: number | null) {
  if (s === null) return '—'
  if (s < 60) return `${s.toFixed(1)}s`
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
}
function fmtTokens(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}
function fmtTs(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function IOPreview({ trace }: { trace: TraceItem }) {
  const display  = trace.display
  const humanMsg = display?.messages?.find((m: any) => m.role === 'human')
  const userText = humanMsg && 'content' in humanMsg ? String(humanMsg.content ?? '') : (typeof trace.input === 'string' ? trace.input : '')
  const answer   = display?.answer ? String(display.answer) : (typeof trace.output === 'string' ? trace.output : '')
  if (!userText && !answer) return <p style={{ fontSize: 12, color: 'var(--adm-text-3)', margin: 0 }}>No I/O recorded</p>
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {userText && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--adm-text-3)', marginBottom: 3 }}>Input</div>
          <div style={{ fontSize: 12, color: 'var(--adm-text-2)', lineHeight: 1.5 }}>{userText.slice(0, 200)}{userText.length > 200 ? '…' : ''}</div>
        </div>
      )}
      {answer && (
        <div>
          <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--adm-text-3)', marginBottom: 3 }}>Output</div>
          <div style={{ fontSize: 12, color: 'var(--adm-text)', lineHeight: 1.5 }}>{answer.slice(0, 300)}{answer.length > 300 ? '…' : ''}</div>
        </div>
      )}
    </div>
  )
}

function TraceCard({ trace, idx, onOpenTrace }: { trace: TraceItem; idx: number; onOpenTrace: (id: string) => void }) {
  const isErr = trace.level === 'ERROR'
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 12, padding: '14px 16px', background: isErr ? 'rgba(220,38,38,0.03)' : 'var(--adm-surface)', border: `1px solid ${isErr ? 'rgba(220,38,38,0.25)' : 'var(--adm-border)'}`, borderRadius: 'var(--adm-radius-lg)', marginBottom: 8 }}>
      <IOPreview trace={trace} />
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5, minWidth: 140, fontSize: 12, color: 'var(--adm-text-2)', borderLeft: '1px solid var(--adm-border)', paddingLeft: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 4, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, color: 'var(--adm-text-3)' }}>#{idx + 1}</span>
          {trace.original_intent && <span className="adm-badge adm-badge--info" style={{ fontSize: 10 }}>{trace.original_intent}</span>}
          {isErr && <span className="adm-badge adm-badge--error" style={{ fontSize: 10 }}>Error</span>}
        </div>
        {[
          ['Prompt',  trace.prompt_name],
          ['Time',    fmtTs(trace.start_time)],
          ['Latency', trace.latency != null ? `${trace.latency.toFixed(1)}s` : null],
        ].filter(([, v]) => v).map(([label, value]) => (
          <div key={String(label)} style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
            <span style={{ color: 'var(--adm-text-3)', flexShrink: 0, fontSize: 11 }}>{label}</span>
            <span style={{ textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 110, fontSize: 11 }}>{value}</span>
          </div>
        ))}
        {trace.quality_score != null && <div style={{ marginTop: 4 }}><ScoreBar value={trace.quality_score} /></div>}
        <button
          className="adm-btn adm-btn-ghost adm-btn-sm"
          style={{ marginTop: 6, justifyContent: 'center' }}
          onClick={() => onOpenTrace(trace.id)}
        >
          <ExternalLink size={11} /> Trace
        </button>
      </div>
    </div>
  )
}

interface Props {
  threadId: string
}

export function ThreadDetailContent({ threadId }: Props) {
  const [traceDrawerId, setTraceDrawerId] = useState<string | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['thread-detail', threadId],
    queryFn:  () => getSessionDetail(threadId),
    enabled:  !!threadId,
  })

  if (isLoading) return <div style={{ padding: 24, textAlign: 'center', color: 'var(--adm-text-3)' }}><div className="adm-spinner" style={{ margin: '0 auto 8px' }} />載入中…</div>
  if (isError || !data) return <div style={{ padding: 24, color: 'var(--adm-red)', fontSize: 12 }}>Thread not found</div>

  const traces: TraceItem[] = (data as any).traces ?? []

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 16 }}>
        {[
          `${traces.length} traces`,
          `${fmtTokens(data.total_tokens)} tokens`,
          fmtDuration(data.duration_seconds),
        ].map(s => <span key={s} className="adm-badge adm-badge--info">{s}</span>)}
        {data.task_type && <span className="adm-badge adm-badge--info">{data.task_type}</span>}
        {data.avg_quality_score != null && <ScoreBar value={data.avg_quality_score} />}
      </div>

      <div style={{ fontSize: 10, fontFamily: 'var(--adm-font-mono)', color: 'var(--adm-text-3)', marginBottom: 14, wordBreak: 'break-all' }}>
        {threadId}
      </div>

      {traces.length === 0 ? (
        <div style={{ padding: '24px 0', textAlign: 'center', color: 'var(--adm-text-3)', fontSize: 12 }}>No traces found</div>
      ) : (
        traces.map((trace, i) => (
          <TraceCard key={trace.id} trace={trace} idx={i} onOpenTrace={setTraceDrawerId} />
        ))
      )}

      <DrawerPanel
        open={!!traceDrawerId}
        title={traceDrawerId ? `Trace · ${traceDrawerId.slice(-12)}…` : ''}
        onClose={() => setTraceDrawerId(null)}
        width={600}
      >
        {traceDrawerId && <TraceDetailContent traceId={traceDrawerId} compact />}
      </DrawerPanel>
    </div>
  )
}
