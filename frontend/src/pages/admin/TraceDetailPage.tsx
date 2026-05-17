import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ChevronDown, ChevronRight, Save } from 'lucide-react'
import { getTraceDetail, updateTraceFeedback, type TraceDetail } from '../../api'
import LatencyWaterfall from '../../components/trace/LatencyWaterfall'
import './TraceDetailPage.css'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(v: number | null | undefined, suffix = '') {
  return v != null ? `${v}${suffix}` : '—'
}

function fmtTime(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function fmtJson(v: unknown) {
  if (v == null) return '—'
  if (typeof v === 'string') return v
  try { return JSON.stringify(v, null, 2) } catch { return String(v) }
}

// ── Overview sub-components ───────────────────────────────────────────────────

function QualityBars({ detail }: { detail: NonNullable<TraceDetail['quality_detail']> }) {
  const dims: { key: keyof typeof detail; label: string; weight: string }[] = [
    { key: 'grounding',          label: '證據支撐',   weight: '25%' },
    { key: 'task_fit',           label: '任務符合',   weight: '18%' },
    { key: 'completeness',       label: '完整度',     weight: '17%' },
    { key: 'specificity',        label: '具體性',     weight: '12%' },
    { key: 'source_quality',     label: '來源品質',   weight: '10%' },
    { key: 'uncertainty_honesty', label: '不確定誠實', weight: '10%' },
    { key: 'format_fit',         label: '格式適切',   weight: '8%' },
  ]
  const overall = detail.overall ?? null
  const cls = (v: number) => v >= 4 ? 'high' : v >= 2.5 ? 'mid' : 'low'

  return (
    <div className="td-quality">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <p className="td-quality-title" style={{ margin: 0 }}>品質細項</p>
        {detail.verdict && (
          <span style={{ fontSize: 12, color: '#64748b', fontStyle: 'italic' }}>{detail.verdict}</span>
        )}
      </div>
      {dims.map(({ key, label, weight }) => {
        const v = detail[key] as number | undefined
        if (v == null) return null
        return (
          <div key={key} className="td-q-row">
            <span className="td-q-label" title={`weight: ${weight}`}>{label}</span>
            <div className="td-q-track">
              <div className={`td-q-fill ${cls(v)}`} style={{ width: `${(v / 5) * 100}%` }} />
            </div>
            <span className="td-q-val">{v.toFixed(1)}</span>
          </div>
        )
      })}
      {overall != null && (
        <div className="td-q-overall">
          <span className="td-q-overall-label">Overall</span>
          <span className={`td-q-overall-val ${cls(overall)}`}>{overall.toFixed(1)} / 5</span>
        </div>
      )}
      {(detail.issues ?? []).length > 0 && (
        <div className="td-q-issues">
          <span style={{ fontSize: 11, fontWeight: 600, color: '#dc2626', marginBottom: 3, display: 'block' }}>問題</span>
          {detail.issues!.map((s, i) => <span key={i}>• {s}</span>)}
        </div>
      )}
      {(detail.evidence_gaps ?? []).length > 0 && (
        <div className="td-q-issues" style={{ marginTop: 6 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: '#d97706', marginBottom: 3, display: 'block' }}>證據缺口</span>
          {detail.evidence_gaps!.map((s, i) => <span key={i}>• {s}</span>)}
        </div>
      )}
      {(detail.suggested_fixes ?? []).length > 0 && (
        <div className="td-q-issues" style={{ marginTop: 6 }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: '#16a34a', marginBottom: 3, display: 'block' }}>建議修正</span>
          {detail.suggested_fixes!.map((s, i) => <span key={i}>• {s}</span>)}
        </div>
      )}
      {(detail.should_rerun_retrieval || detail.should_rerun_research) && (
        <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
          {detail.should_rerun_retrieval && (
            <span style={{ fontSize: 11, background: '#fef3c7', color: '#92400e', padding: '2px 8px', borderRadius: 999, fontWeight: 600 }}>
              建議重跑 retrieval
            </span>
          )}
          {detail.should_rerun_research && (
            <span style={{ fontSize: 11, background: '#fce7f3', color: '#9d174d', padding: '2px 8px', borderRadius: 999, fontWeight: 600 }}>
              建議重跑 research
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function FeedbackForm({ trace, onSaved }: { trace: TraceDetail; onSaved: (t: TraceDetail) => void }) {
  const [score, setScore] = useState(trace.quality_score != null ? String(trace.quality_score) : '')
  const [text, setText] = useState(trace.user_feedback ?? '')
  const [msg, setMsg] = useState<string | null>(null)
  const qc = useQueryClient()

  const mutation = useMutation({
    mutationFn: () => {
      const parsed = score.trim() === '' ? null : Number(score)
      return updateTraceFeedback(trace.id, { quality_score: parsed, user_feedback: text.trim() || null })
    },
    onSuccess: (updated) => {
      onSaved(updated)
      setMsg('已儲存')
      qc.invalidateQueries({ queryKey: ['trace-detail', trace.id] })
      setTimeout(() => setMsg(null), 2000)
    },
  })

  return (
    <div className="td-feedback">
      <p className="td-feedback-title">Feedback</p>
      <label>
        Quality score (0–5)
        <input value={score} onChange={e => setScore(e.target.value)} placeholder="0–5" inputMode="decimal" />
      </label>
      <label>
        Comment
        <textarea value={text} onChange={e => setText(e.target.value)} placeholder="What should be improved?" />
      </label>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button className="td-feedback-save" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
          <Save size={13} /> Save
        </button>
        {msg && <span className="td-feedback-msg">{msg}</span>}
      </div>
    </div>
  )
}

// ── Child span item (expandable) ──────────────────────────────────────────────

function ChildSpan({ child }: { child: TraceDetail }) {
  const [open, setOpen] = useState(false)
  const isErr = child.status === 'error'
  const type = child.run_type ?? 'chain'

  return (
    <div className="td-child">
      <div className="td-child-head" onClick={() => setOpen(o => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span className={`td-child-type td-child-type--${type}`}>{type}</span>
        <span className="td-child-name">{child.name}</span>
        <div className="td-child-meta">
          {child.latency != null && <span>{child.latency}s</span>}
          <span className={`td-child-status td-child-status--${isErr ? 'err' : 'ok'}`}>
            {isErr ? '✕' : '✓'}
          </span>
        </div>
      </div>
      {open && (
        <div className="td-child-body">
          <div>
            <h4>Input</h4>
            <pre>{fmtJson(child.inputs_raw)}</pre>
          </div>
          <div>
            <h4>Output</h4>
            <pre>{fmtJson(child.outputs_raw)}</pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

type Tab = 'overview' | 'children' | 'evidence' | 'latency' | 'raw'

export default function TraceDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('overview')
  const [trace, setTrace] = useState<TraceDetail | null>(null)

  const { isLoading, data: fetchedTrace } = useQuery({
    queryKey: ['trace-detail', runId],
    queryFn: () => getTraceDetail(runId!),
    enabled: !!runId,
  })

  useEffect(() => {
    if (fetchedTrace) setTrace(fetchedTrace)
  }, [fetchedTrace])

  if (isLoading) return <div className="td-loading">載入中…</div>
  if (!trace) return <div className="td-not-found">Trace 不存在</div>

  const isErr = trace.status === 'error'
  const display = trace.display
  const sources = display?.sources ?? []
  const hasChildren = (trace.children?.length ?? 0) > 0
  const hasLatency = hasChildren

  const promptStack = (() => {
    const stack = trace.prompt_stack_json
    if (Array.isArray(stack)) return stack.map(s => s.name ?? s.source_name ?? '').filter(Boolean).join(' + ')
    if (typeof stack === 'string') {
      try { const arr = JSON.parse(stack); return Array.isArray(arr) ? arr.map((s: { name?: string }) => s.name ?? '').join(' + ') : stack } catch { return stack }
    }
    return null
  })()

  const tabs: { key: Tab; label: string; show: boolean }[] = [
    { key: 'overview',  label: 'Overview',  show: true },
    { key: 'children',  label: `Spans (${trace.children?.length ?? 0})`, show: hasChildren },
    { key: 'evidence',  label: 'Evidence',  show: sources.length > 0 },
    { key: 'latency',   label: 'Latency',   show: hasLatency },
    { key: 'raw',       label: 'Raw',       show: true },
  ]

  return (
    <div className="td-page">
      {/* Header */}
      <div className="td-header">
        <button className="td-back-btn" onClick={() => navigate('/admin/traces')}>← Traces</button>
        <div className="td-header-info">
          <div className="td-header-row1">
            <span className={`td-type-badge`}>{trace.run_type ?? 'chain'}</span>
            <code className="td-run-id">{trace.id}</code>
            <span className={`td-status-badge td-status-badge--${isErr ? 'error' : 'success'}`}>
              {isErr ? 'Error' : 'Success'}
            </span>
          </div>
          <div className="td-header-row2">
            {trace.start_time && <span>{fmtTime(trace.start_time)}</span>}
            {trace.latency != null && <span>Latency <strong>{trace.latency}s</strong></span>}
            {trace.agent_name && <span>Agent <strong>{trace.agent_name}</strong></span>}
            {trace.prompt_name && <span>Prompt <strong>{trace.prompt_name}</strong></span>}
            {trace.thread_id && <span>Thread <strong style={{ fontFamily: 'monospace', fontSize: 11 }}>{trace.thread_id.slice(-8)}</strong></span>}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="td-tabs">
        {tabs.filter(t => t.show).map(t => (
          <button
            key={t.key}
            className={`td-tab-btn${tab === t.key ? ' active' : ''}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="td-content">
        {/* Overview */}
        {tab === 'overview' && (
          <>
            <div className="td-grid">
              <div className="td-card"><span>Mode</span><strong>{trace.mode ?? '—'}</strong></div>
              <div className="td-card"><span>Agent</span><strong>{trace.agent_name ?? '—'}</strong></div>
              <div className="td-card"><span>Prompt</span><strong>{trace.prompt_name ?? '—'}</strong></div>
              <div className="td-card"><span>Version</span><strong>{trace.prompt_version ?? '—'}</strong></div>
              <div className="td-card"><span>Stack</span><strong>{trace.prompt_stack_name ?? '—'}</strong></div>
              <div className="td-card"><span>Stack Tokens</span><strong>{fmt(trace.prompt_stack_tokens)}</strong></div>
              <div className="td-card"><span>Latency</span><strong>{fmt(trace.latency, 's')}</strong></div>
              <div className="td-card">
                <span>Tokens</span>
                <strong>{(trace.prompt_tokens ?? 0) + (trace.completion_tokens ?? 0)}</strong>
              </div>
              <div className="td-card"><span>Tool calls</span><strong>{fmt(trace.tool_count)}</strong></div>
              <div className="td-card"><span>LLM calls</span><strong>{fmt(trace.llm_call_count)}</strong></div>
              {(trace.original_intent || trace.resolved_intent) && (
                <div className="td-card td-card--wide">
                  <span>Route</span>
                  <strong>
                    {trace.original_intent ?? '—'}
                    {trace.original_intent !== trace.resolved_intent && (
                      <span className="td-route-arrow">→ {trace.resolved_intent}</span>
                    )}
                  </strong>
                </div>
              )}
              {promptStack && (
                <div className="td-card td-card--wide">
                  <span>Stack Prompts</span>
                  <strong>{promptStack}</strong>
                </div>
              )}
              {trace.quality_score != null && (
                <div className="td-card">
                  <span>Quality Score</span>
                  <strong style={{
                    color: trace.quality_score >= 4 ? '#16a34a' : trace.quality_score >= 3 ? '#d97706' : '#dc2626',
                    fontSize: 16,
                  }}>
                    {trace.quality_score.toFixed(1)} / 5
                  </strong>
                </div>
              )}
            </div>

            {trace.quality_detail && <QualityBars detail={trace.quality_detail} />}

            {isErr && trace.error && (
              <div className="td-error-box">
                <AlertTriangle size={14} style={{ flexShrink: 0, marginTop: 1 }} />
                {trace.error}
              </div>
            )}

            <FeedbackForm trace={trace} onSaved={setTrace} />
          </>
        )}

        {/* Children / Spans */}
        {tab === 'children' && (
          <div className="td-children">
            {(trace.children ?? []).length === 0
              ? <div className="td-empty">No child spans</div>
              : (trace.children ?? []).map(c => <ChildSpan key={c.id} child={c} />)
            }
          </div>
        )}

        {/* Evidence */}
        {tab === 'evidence' && (
          <div>
            {sources.length === 0
              ? <div className="td-empty">No sources</div>
              : typeof sources[0] === 'string'
                ? (
                  <div className="td-sources-raw">
                    {(sources as string[]).map((s, i) => <span key={i}>{s}</span>)}
                  </div>
                )
                : (
                  <div className="td-sources">
                    {(sources as Array<{ filename?: string; page?: number; content?: string }>).map((s, i) => (
                      <div key={i} className="td-source">
                        <div className="td-source-head">
                          <span className="td-source-file">{s.filename ?? `Source ${i + 1}`}</span>
                          {s.page != null && <span className="td-source-page">p.{s.page}</span>}
                        </div>
                        {s.content && <p className="td-source-content">{s.content}</p>}
                      </div>
                    ))}
                  </div>
                )
            }
          </div>
        )}

        {/* Latency */}
        {tab === 'latency' && <LatencyWaterfall root={trace} />}

        {/* Raw */}
        {tab === 'raw' && (
          <div className="td-raw">
            <div>
              <h3>Input</h3>
              <pre>{fmtJson(trace.inputs_raw)}</pre>
            </div>
            <div>
              <h3>Output</h3>
              <pre>{fmtJson(trace.outputs_raw)}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
