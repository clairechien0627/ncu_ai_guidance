import { ScoreBar } from './ScoreBar'

const DIMS = [
  { key: 'grounding',            label: 'Grounding' },
  { key: 'task_fit',             label: 'Task Fit' },
  { key: 'completeness',         label: 'Completeness' },
  { key: 'specificity',          label: 'Specificity' },
  { key: 'source_quality',       label: 'Source Quality' },
  { key: 'uncertainty_honesty',  label: 'Uncertainty' },
  { key: 'format_fit',           label: 'Format Fit' },
]

type DimScores = Record<string, number | null | undefined>

export function DimensionGrid({ scores }: { scores: DimScores }) {
  return (
    <div className="adm-dim-grid">
      {DIMS.map(({ key, label }) => (
        <div key={key} className="adm-dim-row">
          <span className="adm-dim-label">{label}</span>
          <ScoreBar value={scores[key]} />
        </div>
      ))}
    </div>
  )
}
