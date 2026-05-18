type Status = 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'skipped'
  | 'ready' | 'processing' | 'error' | 'info' | string

const LABEL: Record<string, string> = {
  pending: 'Pending', running: 'Running', completed: 'Completed',
  partial: 'Partial', failed: 'Failed', skipped: 'Skipped',
  ready: 'Ready', processing: 'Processing', error: 'Error',
}

export function StatusBadge({ status, label }: { status: Status; label?: string }) {
  const cls = `adm-badge adm-badge--${status}`
  return (
    <span className={cls}>
      <span className="adm-badge-dot" />
      {label ?? LABEL[status] ?? status}
    </span>
  )
}
