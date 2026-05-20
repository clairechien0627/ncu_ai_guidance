import { useParams } from 'react-router-dom'
import { PageHeader } from '../../components/admin/PageHeader'
import { ThreadDetailContent } from '../../components/admin/ThreadDetailContent'

export default function ThreadDetailPage() {
  const { threadId } = useParams<{ threadId: string }>()
  if (!threadId) return null
  return (
    <div>
      <PageHeader
        title={threadId.length > 40 ? threadId.slice(0, 40) + '…' : threadId}
        crumbs={[{ label: 'Threads', to: '/admin/threads' }, { label: 'Detail' }]}
      />
      <ThreadDetailContent threadId={threadId} />
    </div>
  )
}
