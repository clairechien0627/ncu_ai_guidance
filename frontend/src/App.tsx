import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import UserLayout from './layouts/UserLayout'
import AdminLayout from './layouts/AdminLayout'
import ChatApp from './ChatApp'

// User-facing pages
import HomePage from './pages/HomePage'
import ProjectsPage from './pages/ProjectsPage'
import AssessmentPage from './pages/AssessmentPage'

// Admin pages
import DashboardPage from './pages/admin/DashboardPage'
import TracesPage from './pages/admin/TracesPage'
import TraceDetailPage from './pages/admin/TraceDetailPage'
import ScoresPage from './pages/admin/ScoresPage'
import ObservationsPage from './pages/admin/ObservationsPage'
import SessionsPage from './pages/admin/SessionsPage'
import SessionDetailPage from './pages/admin/SessionDetailPage'
import UsersPage from './pages/admin/UsersPage'
import UserDetailPage from './pages/admin/UserDetailPage'
import DocumentsPage from './pages/admin/DocumentsPage'
import JobsPage from './pages/admin/JobsPage'
import PromptsPage from './pages/admin/PromptsPage'
import PromptDetailPage from './pages/admin/PromptDetailPage'
import PromptMetricsPage from './pages/admin/PromptMetricsPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* User-facing (with Navbar) */}
        <Route element={<UserLayout />}>
          <Route path="/"            element={<HomePage />} />
          <Route path="/projects"    element={<ProjectsPage />} />
          <Route path="/chat"        element={<ChatApp />} />
          <Route path="/assessment"  element={<AssessmentPage />} />
        </Route>

        {/* Legacy redirect */}
        <Route path="/summaries" element={<Navigate to="/projects" replace />} />

        {/* Admin */}
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<Navigate to="dashboard" replace />} />
          <Route path="dashboard"              element={<DashboardPage />} />
          <Route path="traces"                 element={<TracesPage />} />
          <Route path="traces/:runId"          element={<TraceDetailPage />} />
          <Route path="scores"                 element={<ScoresPage />} />
          <Route path="observations"           element={<ObservationsPage />} />
          <Route path="sessions"               element={<SessionsPage />} />
          <Route path="sessions/:threadId"     element={<SessionDetailPage />} />
          <Route path="users"                  element={<UsersPage />} />
          <Route path="users/:userId"          element={<UserDetailPage />} />
          <Route path="documents"              element={<DocumentsPage />} />
          <Route path="jobs"                   element={<JobsPage />} />
          <Route path="prompts"                element={<PromptsPage />} />
          <Route path="prompts/:name"          element={<PromptDetailPage />} />
          <Route path="prompts/:name/metrics"  element={<PromptMetricsPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
