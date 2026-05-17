import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import ChatApp from './ChatApp'
import AdminLayout from './layouts/AdminLayout'
import DashboardPage from './pages/admin/DashboardPage'
import TracesPage from './pages/admin/TracesPage'
import SessionsPage from './pages/admin/SessionsPage'
import SessionDetailPage from './pages/admin/SessionDetailPage'
import UsersPage from './pages/admin/UsersPage'
import UserDetailPage from './pages/admin/UserDetailPage'
import DocumentsPage from './pages/admin/DocumentsPage'
import JobsPage from './pages/admin/JobsPage'
import PromptsPage from './pages/admin/PromptsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Navigate to="/admin/dashboard" replace />} />
        <Route path="/chat" element={<ChatApp />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard" element={<DashboardPage />} />
          <Route path="traces" element={<TracesPage />} />
          <Route path="sessions" element={<SessionsPage />} />
          <Route path="sessions/:threadId" element={<SessionDetailPage />} />
          <Route path="users" element={<UsersPage />} />
          <Route path="users/:userId" element={<UserDetailPage />} />
          <Route path="documents" element={<DocumentsPage />} />
          <Route path="jobs" element={<JobsPage />} />
          <Route path="prompts" element={<PromptsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/admin/dashboard" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
