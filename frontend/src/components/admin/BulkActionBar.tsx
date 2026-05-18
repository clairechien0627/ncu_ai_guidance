interface Action {
  label: string
  onClick: () => void
  variant?: 'primary' | 'secondary' | 'danger'
  disabled?: boolean
}

interface Props {
  count: number
  actions: Action[]
  onClear: () => void
}

export function BulkActionBar({ count, actions, onClear }: Props) {
  if (count === 0) return null
  return (
    <div className="adm-bulk-bar">
      <span className="adm-bulk-bar-count">已選取 {count} 筆</span>
      <div className="adm-bulk-bar-actions">
        {actions.map(a => (
          <button
            key={a.label}
            className={`adm-btn adm-btn-${a.variant ?? 'secondary'} adm-btn-sm`}
            onClick={a.onClick}
            disabled={a.disabled}
          >
            {a.label}
          </button>
        ))}
        <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={onClear}>取消選取</button>
      </div>
    </div>
  )
}
