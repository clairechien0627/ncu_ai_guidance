import { useState, useRef, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { RefreshCw, Send, Zap, Activity, Wrench, ChevronDown, ChevronRight, ExternalLink } from 'lucide-react'
import {
  getQueueStatus, runBackfill, testRoute, createEvalRun,
  sendChat, getHealth,
  type QueueStatus, type BackfillResult, type TestRouteResult,
} from '../../api'

// ── Chat Tester ───────────────────────────────────────────────────────────────

function ChatTester() {
  const navigate = useNavigate()
  const [threadId, setThreadId] = useState('pg-test-001')
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<{ response: string; route?: string; trace_id?: string; latency?: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const t0 = useRef(0)

  const send = useCallback(async () => {
    if (!message.trim() || loading) return
    setLoading(true); setError(null); setResult(null)
    t0.current = Date.now()
    try {
      const data = await sendChat({
        message,
        thread_id: threadId || `pg-${Date.now()}`,
        document_ids: [],
      })
      const latency = ((Date.now() - t0.current) / 1000)
      setResult({
        response: data?.response ?? data?.answer ?? JSON.stringify(data),
        route: data?.route ?? data?.agent_name,
        trace_id: data?.trace_id,
        latency,
      })
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }, [message, threadId, loading])

  return (
    <div className="adm-card" style={{ marginBottom: 16 }}>
      <div className="adm-card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <Send size={14} /> Chat Tester
      </div>

      <div style={{ display: 'flex', gap: 10, marginBottom: 10 }}>
        <label style={S.label}>Thread ID
          <input className="adm-input" style={S.input} value={threadId} onChange={e => setThreadId(e.target.value)} placeholder="pg-test-001" />
        </label>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <textarea
          className="adm-input"
          style={{ ...S.input, flex: 1, minHeight: 72, resize: 'vertical', fontFamily: 'inherit' }}
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder="輸入測試訊息…"
          onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) send() }}
        />
        <button className="adm-btn adm-btn-primary" onClick={send} disabled={loading || !message.trim()} style={{ whiteSpace: 'nowrap', alignSelf: 'flex-end' }}>
          {loading ? <span className="adm-spinner" style={{ width: 12, height: 12 }} /> : <><Send size={12} style={{ marginRight: 4 }} />Send</>}
        </button>
      </div>

      {error && <div style={S.errorBox}>{error}</div>}

      {result && (
        <div style={S.resultBox}>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
            {result.route && <span style={S.chip}>Route: <strong>{result.route}</strong></span>}
            {result.latency != null && <span style={S.chip}>Latency: <strong>{result.latency.toFixed(2)}s</strong></span>}
            {result.trace_id && (
              <button className="adm-btn adm-btn-ghost" style={{ fontSize: 11, padding: '2px 8px' }}
                onClick={() => navigate(`/admin/traces/${result.trace_id}`)}>
                <ExternalLink size={11} style={{ marginRight: 4 }} />View Trace
              </button>
            )}
          </div>
          {result.trace_id && (
            <div style={{ fontSize: 10, color: 'var(--adm-text-3)', marginBottom: 6, fontFamily: 'monospace' }}>
              trace_id: {result.trace_id}
            </div>
          )}
          <div style={S.responseBox}>{result.response}</div>
        </div>
      )}
    </div>
  )
}

// ── Route Tester ──────────────────────────────────────────────────────────────

function RouteTester() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<TestRouteResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  const test = async () => {
    if (!query.trim() || loading) return
    setLoading(true); setError(null); setResult(null)
    try {
      setResult(await testRoute(query))
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="adm-card" style={{ flex: '0 0 58%' }}>
      <div className="adm-card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <Zap size={14} /> Route Tester <span style={{ fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 400 }}>（不執行 agent，毫秒回應）</span>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <input className="adm-input" style={{ ...S.input, flex: 1 }} value={query} onChange={e => setQuery(e.target.value)}
          placeholder="輸入查詢…" onKeyDown={e => e.key === 'Enter' && test()} />
        <button className="adm-btn adm-btn-primary" onClick={test} disabled={loading || !query.trim()}>
          {loading ? <span className="adm-spinner" style={{ width: 12, height: 12 }} /> : 'Test'}
        </button>
      </div>
      {error && <div style={S.errorBox}>{error}</div>}
      {result && (
        <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <Row label="Agent" value={result.agent_name} />
          <Row label="Path" value={result.path} />
          <Row label="Prompt" value={`${result.prompt_name} v${result.prompt_version}`} />
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 12 }}>
      <span style={{ color: 'var(--adm-text-3)', minWidth: 64 }}>{label}</span>
      <strong style={{ color: 'var(--adm-text)' }}>{value}</strong>
    </div>
  )
}

// ── System Health ─────────────────────────────────────────────────────────────

function SystemHealth() {
  const [loading, setLoading] = useState(false)
  const [health, setHealth] = useState<any>(null)
  const [queue, setQueue] = useState<QueueStatus | null>(null)

  const refresh = async () => {
    setLoading(true)
    try {
      const [h, q] = await Promise.all([
        getHealth(),
        getQueueStatus(),
      ])
      setHealth(h); setQueue(q)
    } catch { /* ignore */ } finally {
      setLoading(false)
    }
  }

  const dot = (ok: boolean | null) => (
    <span style={{ color: ok === null ? 'var(--adm-text-3)' : ok ? 'var(--adm-green)' : 'var(--adm-red)', marginRight: 6 }}>●</span>
  )

  return (
    <div className="adm-card" style={{ flex: 1 }}>
      <div className="adm-card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <Activity size={14} /> System Health
        <button className="adm-btn adm-btn-ghost" style={{ marginLeft: 'auto', padding: '2px 8px' }} onClick={refresh} disabled={loading}>
          <RefreshCw size={12} className={loading ? 'adm-spin' : ''} />
        </button>
      </div>

      {!health && !queue && (
        <div style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>點 Refresh 載入狀態</div>
      )}

      {health && (
        <div style={{ marginBottom: 12 }}>
          {Object.entries(health?.services ?? {}).map(([k, v]: [string, any]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', fontSize: 12, marginBottom: 3 }}>
              {dot(v?.status === 'ok')}
              <span style={{ minWidth: 110 }}>{k}</span>
              <span style={{ color: 'var(--adm-text-3)' }}>{v?.status}</span>
            </div>
          ))}
        </div>
      )}

      {queue && (
        <div style={{ borderTop: '1px solid var(--adm-border)', paddingTop: 10 }}>
          <div style={{ fontSize: 11, color: 'var(--adm-text-3)', marginBottom: 6, fontWeight: 600, textTransform: 'uppercase' }}>Queue</div>
          <QRow label="Outbox pending" value={queue.outbox.pending} warn={queue.outbox.pending > 0} />
          <QRow label="Outbox failed" value={queue.outbox.failed} warn={queue.outbox.failed > 0} />
          <QRow label="Eval pending" value={queue.evaluation.items_pending} />
          <QRow label="Eval failed" value={queue.evaluation.items_failed} warn={queue.evaluation.items_failed > 0} />
          <div style={{ display: 'flex', gap: 8, fontSize: 12, marginTop: 4 }}>
            <span style={{ color: 'var(--adm-text-3)', minWidth: 110 }}>Eval worker</span>
            <strong style={{ color: queue.evaluation.worker_running ? 'var(--adm-green)' : 'var(--adm-amber)' }}>
              {queue.evaluation.worker_running === null ? '?' : queue.evaluation.worker_running ? 'running' : 'stopped'}
            </strong>
          </div>
        </div>
      )}
    </div>
  )
}

function QRow({ label, value, warn }: { label: string; value: number; warn?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 8, fontSize: 12, marginBottom: 3 }}>
      <span style={{ color: 'var(--adm-text-3)', minWidth: 110 }}>{label}</span>
      <strong style={{ color: warn && value > 0 ? 'var(--adm-amber)' : 'var(--adm-text)' }}>{value}</strong>
    </div>
  )
}

// ── Maintenance ───────────────────────────────────────────────────────────────

function Maintenance() {
  const [open, setOpen] = useState(false)
  const [dryRun, setDryRun] = useState(true)
  const [batchLimit, setBatchLimit] = useState(50)
  const [backfillResult, setBackfillResult] = useState<BackfillResult | null>(null)
  const [batchMsg, setBatchMsg] = useState<string | null>(null)
  const [loading, setLoading] = useState<string | null>(null)

  const doBackfill = async () => {
    setLoading('backfill'); setBackfillResult(null)
    try { setBackfillResult(await runBackfill({ dry_run: dryRun })) }
    catch { setBackfillResult({ ok: false, error: 'Request failed' }) }
    finally { setLoading(null) }
  }

  const doBatch = async () => {
    setLoading('batch'); setBatchMsg(null)
    try {
      const r = await createEvalRun({ limit: batchLimit })
      setBatchMsg(r?.message ?? `Queued ${r?.queued ?? 0} traces`)
    } catch { setBatchMsg('Failed') } finally { setLoading(null) }
  }

  return (
    <div className="adm-card" style={{ flex: 1, marginTop: 12 }}>
      <button style={{ all: 'unset', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8, width: '100%' }}
        onClick={() => setOpen(o => !o)}>
        <div className="adm-card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Wrench size={14} /> Maintenance
        </div>
        {open ? <ChevronDown size={12} style={{ marginLeft: 'auto', color: 'var(--adm-text-3)' }} /> : <ChevronRight size={12} style={{ marginLeft: 'auto', color: 'var(--adm-text-3)' }} />}
      </button>

      {open && (
        <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <div style={S.sectionLabel}>Backfill traces_v2</div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 4, color: 'var(--adm-text-2)' }}>
                <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} />
                dry-run
              </label>
              <button className="adm-btn adm-btn-secondary" onClick={doBackfill} disabled={loading === 'backfill'} style={{ fontSize: 11 }}>
                {loading === 'backfill' ? '…' : 'Run Backfill'}
              </button>
            </div>
            {backfillResult && (
              <div style={{ fontSize: 11, marginTop: 6, color: backfillResult.ok ? 'var(--adm-text-2)' : 'var(--adm-red)' }}>
                {backfillResult.ok
                  ? `${backfillResult.dry_run ? '[dry-run] ' : ''}roots=${backfillResult.roots_seen} traces=${backfillResult.traces_written} obs=${backfillResult.observations_written}`
                  : `Error: ${backfillResult.error}`}
              </div>
            )}
          </div>

          <div>
            <div style={S.sectionLabel}>Batch Score</div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <input className="adm-input" style={{ ...S.input, width: 64 }} type="number" value={batchLimit}
                onChange={e => setBatchLimit(Number(e.target.value))} min={1} max={200} />
              <button className="adm-btn adm-btn-secondary" onClick={doBatch} disabled={loading === 'batch'} style={{ fontSize: 11 }}>
                {loading === 'batch' ? '…' : 'Batch Score'}
              </button>
            </div>
            {batchMsg && <div style={{ fontSize: 11, marginTop: 6, color: 'var(--adm-text-2)' }}>{batchMsg}</div>}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function PlaygroundPage() {
  return (
    <div style={{ padding: '24px 28px', maxWidth: 1100 }}>
      <ChatTester />
      <div style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <RouteTester />
        <div style={{ flex: '0 0 38%', display: 'flex', flexDirection: 'column', gap: 0 }}>
          <SystemHealth />
          <Maintenance />
        </div>
      </div>
    </div>
  )
}

// ── Styles ────────────────────────────────────────────────────────────────────

const S: Record<string, React.CSSProperties> = {
  label: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: 'var(--adm-text-2)' },
  input: { padding: '6px 10px', fontSize: 13 },
  errorBox: { marginTop: 8, fontSize: 12, color: 'var(--adm-red)', background: 'color-mix(in srgb, var(--adm-red) 8%, transparent)', borderRadius: 6, padding: '6px 10px' },
  resultBox: { marginTop: 12, padding: '10px 12px', background: 'var(--adm-surface-2)', borderRadius: 8 },
  chip: { fontSize: 12, color: 'var(--adm-text-2)', background: 'var(--adm-border)', padding: '2px 8px', borderRadius: 4 },
  responseBox: { fontSize: 12, color: 'var(--adm-text-2)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 200, overflowY: 'auto' },
  sectionLabel: { fontSize: 11, color: 'var(--adm-text-3)', fontWeight: 600, textTransform: 'uppercase', marginBottom: 8 },
}
