import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import UserLayout from './layouts/UserLayout'
import AdminLayout from './layouts/AdminLayout'
import ChatApp from './ChatApp'
import LoginPage from './pages/LoginPage'
import { useAuthStore } from './stores/authStore'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore(s => s.token)
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const user = useAuthStore(s => s.user)
  if (!user) return <Navigate to="/login" replace />
  if (user.role !== 'admin') return <Navigate to="/chat" replace />
  return <>{children}</>
}

// User-facing pages
import HomePage from './pages/HomePage'
import ProjectsPage from './pages/ProjectsPage'
import AssessmentPage from './pages/AssessmentPage'

// Admin pages — Observability
import DashboardPage from './pages/admin/DashboardPage'
import TracesPage from './pages/admin/TracesPage'
import TraceDetailPage from './pages/admin/TraceDetailPage'
import ScoresPage from './pages/admin/ScoresPage'
import ObservationsPage from './pages/admin/ObservationsPage'
import SessionsPage from './pages/admin/SessionsPage'
import SessionDetailPage from './pages/admin/SessionDetailPage'
import UsersPage from './pages/admin/UsersPage'
import UserDetailPage from './pages/admin/UserDetailPage'
import UsersManagePage from './pages/admin/UsersManagePage'

// Admin pages — Documents / Prompts
import DocumentsPage from './pages/admin/DocumentsPage'
import JobsPage from './pages/admin/JobsPage'
import PlaygroundPage from './pages/admin/PlaygroundPage'
import PromptsPage from './pages/admin/PromptsPage'
import PromptDetailPage from './pages/admin/PromptDetailPage'
import PromptMetricsPage from './pages/admin/PromptMetricsPage'

// Admin pages — Evaluation (new)
import EvaluationsPage from './pages/admin/EvaluationsPage'
import EvaluationDetailPage from './pages/admin/EvaluationDetailPage'
import DatasetsPage from './pages/admin/DatasetsPage'
import DatasetDetailPage from './pages/admin/DatasetDetailPage'
import ExperimentsPage from './pages/admin/ExperimentsPage'
import ExperimentDetailPage from './pages/admin/ExperimentDetailPage'
import ExperimentComparePage from './pages/admin/ExperimentComparePage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />

        {/* User-facing (with Navbar) — no login required */}
        <Route element={<UserLayout />}>
          <Route path="/"           element={<HomePage />} />
          <Route path="/projects"   element={<ProjectsPage />} />
          <Route path="/chat"       element={<ChatApp />} />
          <Route path="/assessment" element={<AssessmentPage />} />
        </Route>

        {/* Legacy redirect */}
        <Route path="/summaries" element={<Navigate to="/projects" replace />} />

        {/* Admin */}
        <Route path="/admin" element={<RequireAdmin><AdminLayout /></RequireAdmin>}>
          <Route index element={<Navigate to="dashboard" replace />} />

          {/* Observability */}
          <Route path="dashboard"              element={<DashboardPage />} />
          <Route path="traces"                 element={<TracesPage />} />
          <Route path="traces/:runId"          element={<TraceDetailPage />} />
          <Route path="scores"                 element={<ScoresPage />} />
          <Route path="observations"           element={<ObservationsPage />} />
          <Route path="sessions"               element={<SessionsPage />} />
          <Route path="sessions/:threadId"     element={<SessionDetailPage />} />
          <Route path="users"                  element={<UsersPage />} />
          <Route path="users/:userId"          element={<UserDetailPage />} />
          <Route path="users-manage"           element={<UsersManagePage />} />

          {/* Documents / Prompts */}
          <Route path="documents"              element={<DocumentsPage />} />
          <Route path="jobs"                   element={<JobsPage />} />
          <Route path="playground"             element={<PlaygroundPage />} />
          <Route path="prompts"                element={<PromptsPage />} />
          <Route path="prompts/:name"          element={<PromptDetailPage />} />
          <Route path="prompts/:name/metrics"  element={<PromptMetricsPage />} />

          {/* Evaluation */}
          <Route path="evaluations"                        element={<EvaluationsPage />} />
          <Route path="evaluations/:evalRunId"             element={<EvaluationDetailPage />} />
          <Route path="datasets"                           element={<DatasetsPage />} />
          <Route path="datasets/:datasetId"               element={<DatasetDetailPage />} />
          <Route path="experiments"                        element={<ExperimentsPage />} />
          <Route path="experiments/compare"               element={<ExperimentComparePage />} />
          <Route path="experiments/:expRunId"             element={<ExperimentDetailPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
