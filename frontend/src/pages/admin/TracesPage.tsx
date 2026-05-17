import { Suspense, lazy } from 'react'
import { useNavigate } from 'react-router-dom'

const AdminTracesPage = lazy(() => import('../AdminTracesPage'))

export default function TracesPage() {
  const navigate = useNavigate()
  return (
    <Suspense fallback={<div style={{ padding: 32 }}>載入中…</div>}>
      <AdminTracesPage onBack={() => navigate('/chat')} />
    </Suspense>
  )
}
