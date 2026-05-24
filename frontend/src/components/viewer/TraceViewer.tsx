import { useEffect, useState } from 'react'
import { X, RefreshCw, ExternalLink, Clock, OctagonX, TriangleAlert, CircleCheck, SquareTerminal } from 'lucide-react'
import { getTraces } from '../../api'
import type { TraceItem } from '../../api'

interface Props {
  onClose: () => void
}

export default function TraceViewer({ onClose }: Props) {
  const [traces, setTraces] = useState<TraceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      setTraces(await getTraces(40))
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  return (
    <div style={S.overlay}>
      <div style={S.panel}>
        <div style={S.header}>
          <span style={S.title}>Trace 追蹤紀錄</span>
          <div style={S.headerRight}>
            <button style={S.iconBtn} onClick={load} title="重新整理">
              <RefreshCw size={14} />
            </button>
            <button style={S.iconBtn} onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </div>

        <div style={S.body}>
          {loading ? (
            <div style={S.center}>載入中...</div>
          ) : error ? (
            <div style={S.center} >錯誤：{error}</div>
          ) : traces.length === 0 ? (
            <div style={S.center}>無追蹤紀錄</div>
          ) : traces.map(t => (
            <div key={t.id} style={S.row}>
              <div style={S.rowTop} onClick={() => setExpanded(v => v === t.id ? null : t.id)}>
                <StatusIcon status={t.status} />
                <span style={S.name}>{t.name}</span>
                {t.latency != null && (
                  <span style={S.latency}><Clock size={11} style={{ marginRight: 2 }} />{t.latency}s</span>
                )}
                <span style={S.time}>{t.start_time ? new Date(t.start_time).toLocaleString('zh-TW', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—'}</span>
                {t.url && (
                  <a href={t.url} target="_blank" rel="noopener noreferrer" style={S.link}
                    onClick={e => e.stopPropagation()}>
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>

              {expanded === t.id && (
                <div style={S.detail}>
                  {t.error && <div style={S.errorText}>錯誤：{t.error}</div>}
                  {t.input && (
                    <div style={S.detailBlock}>
                      <div style={S.detailLabel}>Input</div>
                      <pre style={S.pre}>{t.input}</pre>
                    </div>
                  )}
                  {t.output && (
                    <div style={S.detailBlock}>
                      <div style={S.detailLabel}>Output</div>
                      <pre style={S.pre}>{t.output}</pre>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function StatusIcon({ status }: { status: string | undefined }) {
  if (status === 'ERROR')   return <OctagonX size={13} style={{ color: '#dc2626', flexShrink: 0 }} />
  if (status === 'WARNING') return <TriangleAlert size={13} style={{ color: '#d97706', flexShrink: 0 }} />
  if (status === 'DEBUG')   return <SquareTerminal size={13} style={{ color: '#94a3b8', flexShrink: 0 }} />
  return <CircleCheck size={13} style={{ color: '#16a34a', flexShrink: 0 }} />
}

const S: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.4)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 300,
  },
  panel: {
    backgroundColor: '#1e2030', color: '#c0c8e8',
    borderRadius: 12, width: '80vw', maxWidth: 860, height: '80vh',
    display: 'flex', flexDirection: 'column',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
  },
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '12px 18px', borderBottom: '1px solid #2e3152', flexShrink: 0,
  },
  title: { fontSize: 14, fontWeight: 600, color: '#e2e8f0' },
  headerRight: { display: 'flex', gap: 6 },
  iconBtn: {
    display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    padding: '4px 8px', borderRadius: 6,
    border: '1px solid #3d4060', backgroundColor: 'transparent',
    color: '#c0c8e8', cursor: 'pointer',
  },
  body: { flex: 1, overflowY: 'auto', padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4 },
  center: { padding: 40, textAlign: 'center', color: '#7c85a8' },
  row: { borderRadius: 6, overflow: 'hidden', backgroundColor: '#252840' },
  rowTop: {
    display: 'flex', alignItems: 'center', gap: 8,
    padding: '8px 12px', cursor: 'pointer',
  },
  name: { fontSize: 13, flex: 1, color: '#c0c8e8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  latency: { fontSize: 11, color: '#7c85a8', display: 'flex', alignItems: 'center', flexShrink: 0 },
  time: { fontSize: 11, color: '#7c85a8', flexShrink: 0 },
  link: { color: '#4f7fe8', display: 'flex', alignItems: 'center', flexShrink: 0 },
  detail: { padding: '0 12px 10px', display: 'flex', flexDirection: 'column', gap: 8, borderTop: '1px solid #2e3152' },
  errorText: { fontSize: 12, color: '#f87171', paddingTop: 8 },
  detailBlock: {},
  detailLabel: { fontSize: 10, color: '#7c85a8', fontWeight: 600, textTransform: 'uppercase', marginBottom: 3, marginTop: 6 },
  pre: {
    fontSize: 11, color: '#a0aec0', backgroundColor: '#1a1b2e',
    borderRadius: 4, padding: '6px 10px', margin: 0,
    whiteSpace: 'pre-wrap', wordBreak: 'break-all', lineHeight: 1.6,
  },
}
