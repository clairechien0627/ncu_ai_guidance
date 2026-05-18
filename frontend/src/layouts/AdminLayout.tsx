import { useState } from 'react'
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  LayoutDashboard, List, Users, FileText, Briefcase, BookOpen,
  MessageSquare, Globe, Star, Layers, FlaskConical, Database, TestTube,
  ChevronRight, PanelLeftClose, PanelLeftOpen,
} from 'lucide-react'
import { getEnvironments } from '../api'
import { useAdminStore } from '../stores/adminStore'
import '../styles/admin.css'

const NAV_GROUPS = [
  {
    group: 'Overview',
    items: [
      { label: 'Dashboard',    path: '/admin/dashboard',    icon: <LayoutDashboard size={16} /> },
      { label: 'System Jobs',  path: '/admin/jobs',         icon: <Briefcase size={16} /> },
    ],
  },
  {
    group: 'Tracing',
    items: [
      { label: 'Sessions',     path: '/admin/sessions',     icon: <ChevronRight size={16} /> },
      { label: 'Traces',       path: '/admin/traces',       icon: <List size={16} /> },
      { label: 'Observations', path: '/admin/observations', icon: <Layers size={16} /> },
      { label: 'Users',        path: '/admin/users',        icon: <Users size={16} /> },
    ],
  },
  {
    group: 'Evaluation',
    items: [
      { label: 'Scores',       path: '/admin/scores',       icon: <Star size={16} /> },
      { label: 'Eval Runs',    path: '/admin/evaluations',  icon: <FlaskConical size={16} /> },
      { label: 'Datasets',     path: '/admin/datasets',     icon: <Database size={16} /> },
      { label: 'Experiments',  path: '/admin/experiments',  icon: <TestTube size={16} /> },
    ],
  },
  {
    group: 'Documents',
    items: [
      { label: 'Documents',    path: '/admin/documents',    icon: <FileText size={16} /> },
    ],
  },
  {
    group: 'Prompts',
    items: [
      { label: 'Prompts',      path: '/admin/prompts',      icon: <BookOpen size={16} /> },
    ],
  },
]

const PAGE_TITLES: Record<string, string> = {
  dashboard: 'Dashboard', traces: 'Traces', sessions: 'Sessions',
  users: 'Users', documents: 'Documents', jobs: 'Jobs', prompts: 'Prompts',
  scores: 'Scores', observations: 'Observations',
  evaluations: 'Eval Runs', datasets: 'Datasets', experiments: 'Experiments',
  compare: 'Compare',
}

function usePageTitle() {
  const { pathname } = useLocation()
  const key = pathname.split('/').filter(Boolean)[1] ?? 'dashboard'
  return PAGE_TITLES[key] ?? key.charAt(0).toUpperCase() + key.slice(1)
}

function EnvironmentSelector() {
  const { environment, setEnvironment } = useAdminStore()
  const { data: envs = [] } = useQuery({ queryKey: ['environments'], queryFn: getEnvironments, staleTime: 60_000 })
  if (envs.length === 0) return null
  return (
    <div className="admin-env-selector">
      <Globe size={13} className="admin-env-icon" />
      <select className="admin-env-select" value={environment ?? ''} onChange={e => setEnvironment(e.target.value || null)}>
        <option value="">All environments</option>
        {envs.map(e => <option key={e} value={e}>{e}</option>)}
      </select>
    </div>
  )
}

export default function AdminLayout() {
  const navigate   = useNavigate()
  const pageTitle  = usePageTitle()
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem('adm-sidebar-collapsed') !== 'false' }
    catch { return true }
  })

  const toggleCollapsed = () => setCollapsed(c => {
    const next = !c
    try { localStorage.setItem('adm-sidebar-collapsed', String(next)) } catch { /* ignore */ }
    return next
  })

  return (
    <div className={`admin-layout${collapsed ? ' admin-layout--collapsed' : ''}`}>
      <aside className={`admin-sidebar${collapsed ? ' admin-sidebar--collapsed' : ''}`}>

        {/* Header */}
        <div className="admin-sidebar-header">
          {!collapsed && <span className="admin-sidebar-logo">Report Agent</span>}
          {!collapsed && <span className="admin-sidebar-tag">Admin</span>}
          <button
            className="admin-sidebar-toggle"
            onClick={toggleCollapsed}
            title={collapsed ? '展開側欄' : '收合側欄'}
            aria-label={collapsed ? '展開側欄' : '收合側欄'}
          >
            {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
          </button>
        </div>

        {/* Nav */}
        <nav className="admin-nav">
          {NAV_GROUPS.map(group => (
            <div key={group.group} className="admin-nav-group">
              {!collapsed && <span className="admin-nav-group-label">{group.group}</span>}
              {group.items.map(item => (
                <NavLink
                  key={item.path}
                  to={item.path}
                  className={({ isActive }) => `admin-nav-item${isActive ? ' active' : ''}`}
                  title={collapsed ? item.label : undefined}
                >
                  {item.icon}
                  {!collapsed && <span>{item.label}</span>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Footer */}
        <div className="admin-sidebar-footer">
          <button
            className="admin-nav-item"
            onClick={() => navigate('/chat')}
            title={collapsed ? 'Chat' : undefined}
          >
            <MessageSquare size={16} />
            {!collapsed && <span>Chat</span>}
          </button>
        </div>
      </aside>

      <div className="admin-content">
        <header className="admin-topbar">
          <span className="admin-page-title">{pageTitle}</span>
          <div className="admin-topbar-right">
            <EnvironmentSelector />
          </div>
        </header>
        <main className="admin-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
