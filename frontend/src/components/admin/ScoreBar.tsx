function tier(v: number) {
  if (v < 2.5) return 'low'
  if (v < 3.5) return 'mid'
  return 'high'
}

export function ScoreBar({ value, max = 5, width }: { value: number | null | undefined; max?: number; width?: string }) {
  if (value == null) return <span className="adm-score-null">—</span>
  const t = tier(value)
  const pct = Math.min(100, (value / max) * 100)
  return (
    <div className="adm-score-bar-wrap" style={width ? { width } : undefined}>
      <div className="adm-score-bar-track">
        <div className={`adm-score-bar-fill adm-score-bar-fill--${t}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`adm-score-bar-val adm-score-bar-val--${t}`}>{value.toFixed(1)}</span>
    </div>
  )
}
