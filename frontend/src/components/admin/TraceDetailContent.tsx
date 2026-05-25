/**
 * TraceDetailContent — 共用於側欄 (TracesPage Drawer) 和獨立頁面 (TraceDetailPage)
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, Save, Star } from 'lucide-react'
import { getTraceDetail, updateTraceBookmark, updateTraceFeedback, type TraceDetail } from '../../api'
import LatencyWaterfall from '../viewer/LatencyWaterfall'
import { ScoreBar } from './ScoreBar'
import { DimensionGrid } from './DimensionGrid'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtTime(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function fmtJson(v: unknown) {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

function fmtObsInput(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object' && !Array.isArray(v)) {
    const obj = v as Record<string, unknown>
    // no_tool_runner format: { user_message, payload, sources }
    if ('user_message' in obj) {
      const parts: string[] = [String(obj.user_message)]
      const payload = obj.payload
      if (payload && typeof payload === 'object' && Object.keys(payload).length > 0)
        parts.push(`payload: ${JSON.stringify(payload, null, 2)}`)
      return parts.join('\n\n')
    }
    // LangChain messages format: { messages: [...] }
    if (Array.isArray(obj.messages)) {
      return (obj.messages as any[]).map(m =>
        `[${m.role ?? m.type ?? '?'}]: ${typeof m.content === 'string' ? m.content : JSON.stringify(m.content)}`
      ).join('\n\n')
    }
  }
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

function fmtObsOutput(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  if (typeof v === 'object' && !Array.isArray(v)) {
    const obj = v as Record<string, unknown>
    // no_tool_runner format: { answer, sources }
    if ('answer' in obj && typeof obj.answer === 'string') return obj.answer
    // LangChain LLM output: { generations: [[{ text }]] }
    if (Array.isArray(obj.generations)) {
      const gen = (obj.generations as any[][])[0]?.[0]
      if (gen?.text) return gen.text
      if (gen?.message?.content) return gen.message.content
    }
  }
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

function sourcesFromTrace(trace: TraceDetail): unknown[] {
  const outputs = trace.outputs_raw
  if (outputs && typeof outputs === 'object' && !Array.isArray(outputs)) {
    const raw = (outputs as Record<string, unknown>).sources
    if (Array.isArray(raw)) return raw
  }
  const childSources: unknown[] = []
  for (const child of trace.children ?? []) {
    const output = child.outputs_raw
    if (output && typeof output === 'object' && !Array.isArray(output)) {
      const raw = (output as Record<string, unknown>).sources
      if (Array.isArray(raw)) childSources.push(...raw)
    }
  }
  if (childSources.length > 0) return childSources
  return []
}

// ── Quality breakdown ─────────────────────────────────────────────────────────

function QualityBars({ detail }: { detail: NonNullable<TraceDetail['quality_detail']> }) {
  const scores: Record<string, number | null | undefined> = {
    grounding: detail.grounding, task_fit: detail.task_fit,
    completeness: detail.completeness, specificity: detail.specificity,
    source_quality: detail.source_quality, uncertainty_honesty: detail.uncertainty_honesty,
    format_fit: detail.format_fit,
  }
  return (
    <div className="adm-card" style={{ marginBottom: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
        <div className="adm-card-title" style={{ margin: 0 }}>品質細項</div>
        {detail.verdict && <span style={{ fontSize: 11, color: 'var(--adm-text-3)', fontStyle: 'italic' }}>{detail.verdict}</span>}
      </div>
      {detail.overall != null && <div style={{ marginBottom: 10 }}><ScoreBar value={detail.overall} /></div>}
      <DimensionGrid scores={scores} />
      {([['issues', 'var(--adm-red)', '問題'], ['evidence_gaps', 'var(--adm-amber)', '證據缺口'], ['suggested_fixes', 'var(--adm-green)', '建議修正']] as const).map(([key, color, label]) => {
        const items = (detail as any)[key] as string[] | undefined
        if (!items?.length) return null
        return (
          <div key={key} style={{ marginTop: 8 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color, marginBottom: 3 }}>{label}</div>
            {items.map((s, i) => <div key={i} style={{ fontSize: 11, color: 'var(--adm-text-2)', paddingLeft: 8 }}>• {s}</div>)}
          </div>
        )
      })}
    </div>
  )
}

// ── Feedback form ─────────────────────────────────────────────────────────────

function FeedbackForm({ trace, onSaved }: { trace: TraceDetail; onSaved: (t: TraceDetail) => void }) {
  const [score, setScore] = useState(trace.quality_score != null ? String(trace.quality_score) : '')
  const [text,  setText]  = useState(trace.user_feedback ?? '')
  const [msg,   setMsg]   = useState<string | null>(null)
  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => updateTraceFeedback(trace.id, {
      quality_score: score.trim() === '' ? null : Number(score),
      user_feedback: text.trim() || null,
    }),
    onSuccess: (updated) => {
      onSaved(updated)
      setMsg('已儲存')
      qc.invalidateQueries({ queryKey: ['trace-detail', trace.id] })
      qc.invalidateQueries({ queryKey: ['traces'] })
      setTimeout(() => setMsg(null), 2000)
    },
  })

  return (
    <div className="adm-card" style={{ marginBottom: 12 }}>
      <div className="adm-card-title">Feedback</div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ flex: '0 0 100px' }}>
          <div className="adm-form-label" style={{ marginBottom: 3 }}>Score (0–5)</div>
          <input className="adm-form-input" value={score} onChange={e => setScore(e.target.value)} placeholder="0–5" inputMode="decimal" />
        </div>
        <div style={{ flex: 1, minWidth: 160 }}>
          <div className="adm-form-label" style={{ marginBottom: 3 }}>Comment</div>
          <textarea className="adm-form-textarea" style={{ minHeight: 52 }} value={text} onChange={e => setText(e.target.value)} placeholder="What should be improved?" />
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button className="adm-btn adm-btn-primary adm-btn-sm" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
          <Save size={12} /> Save
        </button>
        {msg && <span style={{ fontSize: 12, color: 'var(--adm-green)' }}>{msg}</span>}
      </div>
    </div>
  )
}

// ── Child span ────────────────────────────────────────────────────────────────

function ChildSpan({ child }: { child: TraceDetail }) {
  const [open, setOpen] = useState(false)
  const isErr = child.status === 'ERROR'
  return (
    <div style={{ border: '1px solid var(--adm-border)', borderRadius: 'var(--adm-radius)', marginBottom: 6, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', cursor: 'pointer', background: open ? 'var(--adm-surface-2)' : 'var(--adm-surface)' }}
        onClick={() => setOpen(o => !o)}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span className="adm-badge adm-badge--info" style={{ fontSize: 10, textTransform: 'uppercase' }}>{child.type ?? 'SPAN'}</span>
        <span style={{ fontSize: 12, fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{child.name}</span>
        <div style={{ display: 'flex', gap: 8, fontSize: 11, color: 'var(--adm-text-3)', flexShrink: 0 }}>
          {child.latency != null && <span>{child.latency}s</span>}
          <span style={{ color: isErr ? 'var(--adm-red)' : 'var(--adm-green)' }}>{isErr ? '✕' : '✓'}</span>
        </div>
      </div>
      {open && (
        <div style={{ padding: 12, background: 'var(--adm-bg)', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {['Input', 'Output'].map((label, i) => (
            <div key={label}>
              <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--adm-text-3)', marginBottom: 4 }}>{label}</div>
              <pre style={{ fontSize: 11, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 180, overflowY: 'auto', background: 'var(--adm-surface)', padding: 8, borderRadius: 4, border: '1px solid var(--adm-border)' }}>
                {i === 0 ? fmtObsInput(child.inputs_raw) : fmtObsOutput(child.outputs_raw)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main content ──────────────────────────────────────────────────────────────

type Tab = 'overview' | 'children' | 'evidence' | 'latency' | 'raw'

const TAB_STYLE = (active: boolean): React.CSSProperties => ({
  padding: '6px 12px', border: 'none', background: 'none', cursor: 'pointer',
  fontSize: 12, fontWeight: active ? 600 : 500,
  color: active ? 'var(--adm-text)' : 'var(--adm-text-3)',
  borderBottom: `2px solid ${active ? 'var(--adm-text)' : 'transparent'}`,
  transition: 'color 120ms, border-color 120ms',
  whiteSpace: 'nowrap',
})

interface Props {
  traceId: string
  /** Side drawer mode — hides PageHeader, adjusts raw tab to single column */
  compact?: boolean
}

export function TraceDetailContent({ traceId, compact = false }: Props) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [tab,   setTab]   = useState<Tab>('overview')
  const [trace, setTrace] = useState<TraceDetail | null>(null)

  const { isLoading, data: fetchedTrace } = useQuery({
    queryKey: ['trace-detail', traceId],
    queryFn:  () => getTraceDetail(traceId),
    enabled:  !!traceId,
  })
  useEffect(() => { if (fetchedTrace) setTrace(fetchedTrace) }, [fetchedTrace])
  const bookmarkMutation = useMutation({
    mutationFn: () => {
      if (!trace) throw new Error('Trace not loaded')
      return updateTraceBookmark(trace.id, !trace.bookmarked)
    },
    onSuccess: (next) => {
      setTrace(next)
      qc.invalidateQueries({ queryKey: ['traces'] })
      qc.invalidateQueries({ queryKey: ['trace-detail', next.id] })
    },
  })

  if (isLoading) return <div style={{ padding: 32, textAlign: 'center', color: 'var(--adm-text-3)' }}><div className="adm-spinner" style={{ margin: '0 auto 8px' }} />載入中…</div>
  if (!trace) return <div style={{ padding: 32, color: 'var(--adm-red)' }}>Trace 不存在</div>

  const isErr      = trace.status === 'ERROR'
  const sources    = sourcesFromTrace(trace)
  const hasChildren = (trace.children?.length ?? 0) > 0

  const tabs: { key: Tab; label: string; show: boolean }[] = [
    { key: 'overview',  label: 'Overview',                          show: true },
    { key: 'children',  label: `Spans (${trace.children?.length ?? 0})`, show: hasChildren },
    { key: 'evidence',  label: 'Evidence',                          show: sources.length > 0 },
    { key: 'latency',   label: 'Latency',                           show: hasChildren },
    { key: 'raw',       label: 'Raw',                               show: true },
  ]

  const metaItems = [
    ['Agent',  trace.agent_name ?? null],
    ['Mode',    trace.mode],
    ['Latency', trace.latency != null ? `${trace.latency}s` : null],
    ['Tokens',  String(trace.total_tokens ?? ((trace.prompt_tokens ?? 0) + (trace.completion_tokens ?? 0)))],
    ['Tools',   trace.tool_count != null ? String(trace.tool_count) : null],
    ['LLM',     trace.llm_call_count != null ? String(trace.llm_call_count) : null],
    ['Time',    fmtTime(trace.start_time)],
  ].filter(([, v]) => v)

  return (
    <div>
      {/* Compact header (inside drawer) */}
      {compact && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
          <span className={`adm-badge adm-badge--${isErr ? 'error' : 'completed'}`}>
            {isErr ? 'Error' : 'OK'}
          </span>
          {trace.quality_score != null && <ScoreBar value={trace.quality_score} />}
          <button
            className="adm-btn-icon"
            title={trace.bookmarked ? '取消收藏' : '收藏'}
            onClick={() => bookmarkMutation.mutate()}
          >
            <Star size={13} fill={trace.bookmarked ? 'currentColor' : 'none'} />
          </button>
          <span style={{ fontSize: 10, color: 'var(--adm-text-3)', fontFamily: 'var(--adm-font-mono)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {traceId}
          </span>
          <button
            className="adm-btn adm-btn-ghost adm-btn-sm"
            onClick={() => navigate(`/admin/traces/${traceId}`)}
            title="在獨立頁面開啟"
            style={{ flexShrink: 0 }}
          >
            <ExternalLink size={12} />
          </button>
        </div>
      )}

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--adm-border)', marginBottom: 14, overflowX: 'auto', scrollbarWidth: 'none' }}>
        {tabs.filter(t => t.show).map(t => (
          <button key={t.key} style={{ ...TAB_STYLE(tab === t.key), flex: 1, textAlign: 'center', whiteSpace: 'nowrap' }} onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </div>

      {/* Overview */}
      {tab === 'overview' && (
        <div>
          {/* Meta grid */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(130px, 1fr))', gap: 6, marginBottom: 12 }}>
            {metaItems.map(([label, value]) => (
              <div key={String(label)} style={{ background: 'var(--adm-surface)', border: '1px solid var(--adm-border)', borderRadius: 'var(--adm-radius)', padding: '6px 10px' }}>
                <div style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--adm-text-3)', marginBottom: 2 }}>{label}</div>
                <div style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--adm-text-2)' }} title={String(value)}>{value}</div>
              </div>
            ))}
          </div>

          {isErr && trace.error && (
            <div style={{ display: 'flex', gap: 8, padding: '8px 12px', background: 'var(--adm-red-bg)', color: 'var(--adm-red-text)', borderRadius: 'var(--adm-radius)', fontSize: 12, marginBottom: 12 }}>
              <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />{trace.error}
            </div>
          )}

          {trace.quality_detail && <QualityBars detail={trace.quality_detail} />}
          <FeedbackForm trace={trace} onSaved={setTrace} />
        </div>
      )}

      {/* Spans */}
      {tab === 'children' && (
        <div>
          {(trace.children ?? []).length === 0
            ? <div style={{ padding: 24, textAlign: 'center', color: 'var(--adm-text-3)' }}>No child spans</div>
            : (trace.children ?? []).map(c => <ChildSpan key={c.id} child={c} />)}
        </div>
      )}

      {/* Evidence */}
      {tab === 'evidence' && (
        <div>
          {sources.length === 0
            ? <div style={{ padding: 24, textAlign: 'center', color: 'var(--adm-text-3)' }}>No sources</div>
            : typeof sources[0] === 'string'
              ? <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>{(sources as string[]).map((s, i) => <span key={i} className="adm-badge adm-badge--info">{s}</span>)}</div>
              : (sources as Array<{ filename?: string; page?: number; content?: string }>).map((s, i) => (
                <div key={i} className="adm-card" style={{ marginBottom: 8 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontWeight: 600, fontSize: 13 }}>{s.filename ?? `Source ${i + 1}`}</span>
                    {s.page != null && <span className="adm-badge adm-badge--info">p.{s.page}</span>}
                  </div>
                  {s.content && <p style={{ fontSize: 12, color: 'var(--adm-text-2)', lineHeight: 1.6, margin: 0 }}>{s.content}</p>}
                </div>
              ))
          }
        </div>
      )}

      {tab === 'latency' && <LatencyWaterfall root={trace} />}

      {/* Raw — single column in compact mode */}
      {tab === 'raw' && (
        <div style={{ display: 'grid', gridTemplateColumns: compact ? '1fr' : '1fr 1fr', gap: 12 }}>
          {['Input', 'Output'].map((label, i) => (
            <div key={label} className="adm-card">
              <div className="adm-card-title">{label}</div>
              <pre style={{ fontSize: 11, margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 350, overflowY: 'auto', lineHeight: 1.5 }}>
                {fmtJson(i === 0 ? trace.inputs_raw : trace.outputs_raw)}
              </pre>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
