import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard,
  List,
  Users,
  FileText,
  Briefcase,
  BookOpen,
  MessageSquare,
  ChevronRight,
  Globe,
} from 'lucide-react'
import { getEnvironments } from '../api'
import { useAdminStore } from '../stores/adminStore'
import './AdminLayout.css'

const NAV_GROUPS = [
  {
    group: 'Overview',
    items: [
      { label: 'Dashboard', path: '/admin/dashboard', icon: <LayoutDashboard size={16} /> },
    ],
  },
  {
    group: 'Observability',
    items: [
      { label: 'Traces',   path: '/admin/traces',   icon: <List size={16} /> },
      { label: 'Sessions', path: '/admin/sessions', icon: <ChevronRight size={16} /> },
      { label: 'Users',    path: '/admin/users',    icon: <Users size={16} /> },
    ],
  },
  {
    group: 'Documents',
    items: [
      { label: 'Documents', path: '/admin/documents', icon: <FileText size={16} /> },
      { label: 'Jobs',      path: '/admin/jobs',      icon: <Briefcase size={16} /> },
    ],
  },
  {
    group: 'Prompt Management',
    items: [
      { label: 'Prompts', path: '/admin/prompts', icon: <BookOpen size={16} /> },
    ],
  },
]

// Page titles derived from pathname
function usePageTitle() {
  const { pathname } = useLocation()
  const segment = pathname.split('/').filter(Boolean)[1] ?? 'dashboard'
  const map: Record<string, string> = {
    dashboard: 'Dashboard', traces: 'Traces', sessions: 'Sessions',
    users: 'Users', documents: 'Documents', jobs: 'Jobs', prompts: 'Prompts',
  }
  return map[segment] ?? segment.charAt(0).toUpperCase() + segment.slice(1)
}

function EnvironmentSelector() {
  const { environment, setEnvironment } = useAdminStore()
  const { data: envs = [] } = useQuery({
    queryKey: ['environments'],
    queryFn: getEnvironments,
    staleTime: 60_000,
  })

  if (envs.length === 0) return null

  return (
    <div className="admin-env-selector">
      <Globe size={13} className="admin-env-icon" />
      <select
        className="admin-env-select"
        value={environment ?? ''}
        onChange={(e) => setEnvironment(e.target.value || null)}
      >
        <option value="">All environments</option>
        {envs.map((e) => (
          <option key={e} value={e}>{e}</option>
        ))}
      </select>
    </div>
  )
}

export default function AdminLayout() {
  const navigate = useNavigate()
  const pageTitle = usePageTitle()

  return (
    <div className="admin-layout">
      <aside className="admin-sidebar">
        <div className="admin-sidebar-header">
          <span className="admin-sidebar-logo">Report Agent</span>
          <span className="admin-sidebar-tag">Admin</span>
        </div>

        <nav className="admin-nav">
          {NAV_GROUPS.map((group) => (
            <div key={group.group} className="admin-nav-group">
              <span className="admin-nav-group-label">{group.group}</span>
              {group.items.map((item) => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={({ isActive }) =>
                    `admin-nav-item${isActive ? ' active' : ''}`
                  }
                >
                  {item.icon}
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="admin-sidebar-footer">
          <button className="admin-nav-item" onClick={() => navigate('/chat')}>
            <MessageSquare size={16} />
            <span>Chat</span>
          </button>
        </div>
      </aside>

      <div className="admin-content">
        <header className="admin-topbar">
          <span className="admin-page-title">{pageTitle}</span>
          <EnvironmentSelector />
        </header>
        <main className="admin-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
