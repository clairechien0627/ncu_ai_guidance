import { useParams } from 'react-router-dom'
import { PageHeader } from '../../components/admin/PageHeader'
import { TraceDetailContent } from '../../components/admin/TraceDetailContent'

export default function TraceDetailPage() {
  const { runId } = useParams<{ runId: string }>()
  if (!runId) return null

  return (
    <div>
      <PageHeader
        crumbs={[{ label: 'Traces', to: '/admin/traces' }, { label: runId.slice(-12) + '…' }]}
        title="Trace Detail"
      />
      <TraceDetailContent traceId={runId} compact={false} />
    </div>
  )
}
