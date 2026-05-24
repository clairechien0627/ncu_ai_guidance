import type { TraceDetail } from '../../api'
import './LatencyWaterfall.css'

const OBS_TYPE_COLORS: Record<string, string> = {
  llm:   '#3b82f6',   // blue
  tool:  '#f59e0b',   // amber
  chain: '#8b5cf6',   // purple
  generation: '#3b82f6',
  span: '#8b5cf6',
  retriever: '#10b981', // emerald
}

function colorFor(obsType: string | undefined, name: string | undefined) {
  const key = obsType?.toLowerCase()
  if (key && OBS_TYPE_COLORS[key]) return OBS_TYPE_COLORS[key]
  if (name?.includes('search') || name?.includes('retriev')) return OBS_TYPE_COLORS.tool
  return '#6b7280'
}

function formatMs(ms: number) {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`
  if (ms >= 1_000)  return `${(ms / 1_000).toFixed(1)}s`
  return `${Math.round(ms)}ms`
}

function shortName(name: string) {
  // Shorten common verbose names
  return name
    .replace(/research_graph\//g, '')
    .replace(/ChatOpenAI|AzureChatOpenAI/g, 'LLM')
    .replace(/RunnableSequence/g, 'chain')
    .replace(/_/g, ' ')
}

interface Bar {
  id: string
  name: string
  type: string | undefined
  startMs: number
  durationMs: number
  isRoot: boolean
}

interface Props {
  root: TraceDetail
}

export default function LatencyWaterfall({ root }: Props) {
  const children = root.children ?? []

  // Collect all spans with timing
  const allSpans = [root, ...children].filter(
    (s) => s.start_time && s.end_time,
  )

  if (allSpans.length === 0) {
    return <p className="lw-empty">No timing data available for this trace.</p>
  }

  const rootStartMs = new Date(root.start_time!).getTime()
  const rootEndMs   = root.end_time
    ? new Date(root.end_time).getTime()
    : Math.max(...allSpans.map((s) => new Date(s.end_time!).getTime()))
  const totalMs = Math.max(rootEndMs - rootStartMs, 1)

  const bars: Bar[] = allSpans
    .sort((a, b) => new Date(a.start_time!).getTime() - new Date(b.start_time!).getTime())
    .map((s) => {
      const startMs    = new Date(s.start_time!).getTime() - rootStartMs
      const endMs      = s.end_time ? new Date(s.end_time).getTime() - rootStartMs : totalMs
      const durationMs = Math.max(endMs - startMs, 1)
      return {
        id:         s.id ?? s.name,
        name:       shortName(s.name ?? 'unknown'),
        type:       s.type,
        startMs,
        durationMs,
        isRoot:     s.id === root.id,
      }
    })

  return (
    <div className="lw-root">
      {/* Header */}
      <div className="lw-header">
        <span className="lw-header-name">Span</span>
        <span className="lw-header-chart">Timeline ({formatMs(totalMs)} total)</span>
        <span className="lw-header-dur">Duration</span>
      </div>

      {/* Rows */}
      {bars.map((bar) => {
        const leftPct  = (bar.startMs / totalMs) * 100
        const widthPct = Math.max((bar.durationMs / totalMs) * 100, 0.3)
        const color    = colorFor(bar.type, bar.name)
        const pct      = Math.round((bar.durationMs / totalMs) * 100)

        return (
          <div key={bar.id} className={`lw-row${bar.isRoot ? ' lw-row--root' : ''}`}>
            {/* Name */}
            <div className="lw-name" title={bar.name}>
              {bar.type && (
                <span
                  className="lw-type-dot"
                  style={{ background: color }}
                  title={bar.type}
                />
              )}
              <span className="lw-name-text">{bar.name}</span>
            </div>

            {/* Chart */}
            <div className="lw-chart">
              <div
                className="lw-bar"
                style={{
                  left:       `${leftPct}%`,
                  width:      `${widthPct}%`,
                  background: color,
                  opacity:    bar.isRoot ? 0.25 : 0.85,
                }}
                title={`${bar.name}: ${formatMs(bar.durationMs)} (${pct}%)`}
              />
            </div>

            {/* Duration */}
            <div className="lw-dur">
              <span>{formatMs(bar.durationMs)}</span>
              {!bar.isRoot && (
                <span className="lw-dur-pct">{pct}%</span>
              )}
            </div>
          </div>
        )
      })}

      {/* Legend */}
      <div className="lw-legend">
        {Object.entries(OBS_TYPE_COLORS).map(([type, color]) => (
          <span key={type} className="lw-legend-item">
            <span className="lw-legend-dot" style={{ background: color }} />
            {type}
          </span>
        ))}
      </div>
    </div>
  )
}
