import { useParams } from 'react-router-dom'
import { PageHeader } from '../../components/admin/PageHeader'
import { SessionDetailContent } from '../../components/admin/SessionDetailContent'

export default function SessionDetailPage() {
  const { threadId } = useParams<{ threadId: string }>()
  if (!threadId) return null
  return (
    <div>
      <PageHeader
        title={threadId.length > 40 ? threadId.slice(0, 40) + '…' : threadId}
        crumbs={[{ label: 'Sessions', to: '/admin/sessions' }, { label: 'Detail' }]}
      />
      <SessionDetailContent threadId={threadId} />
    </div>
  )
}
