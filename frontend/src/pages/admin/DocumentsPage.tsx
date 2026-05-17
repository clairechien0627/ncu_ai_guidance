import { Suspense, lazy } from 'react'
import { useNavigate } from 'react-router-dom'

const SummaryPage = lazy(() => import('../SummaryPage'))

export default function DocumentsPage() {
  const navigate = useNavigate()
  return (
    <Suspense fallback={<div style={{ padding: 32 }}>載入中…</div>}>
      <SummaryPage onBack={() => navigate('/chat')} />
    </Suspense>
  )
}
