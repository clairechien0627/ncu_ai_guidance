import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  List,
  Users,
  FileText,
  Briefcase,
  BookOpen,
  MessageSquare,
  ChevronRight,
} from 'lucide-react'
import './AdminLayout.css'

interface NavItem {
  label: string
  path: string
  icon: React.ReactNode
}

interface NavGroup {
  group: string
  items: NavItem[]
}

const NAV_GROUPS: NavGroup[] = [
  {
    group: 'Overview',
    items: [
      { label: 'Dashboard', path: '/admin/dashboard', icon: <LayoutDashboard size={16} /> },
    ],
  },
  {
    group: 'Observability',
    items: [
      { label: 'Traces', path: '/admin/traces', icon: <List size={16} /> },
      { label: 'Sessions', path: '/admin/sessions', icon: <ChevronRight size={16} /> },
      { label: 'Users', path: '/admin/users', icon: <Users size={16} /> },
    ],
  },
  {
    group: 'Documents',
    items: [
      { label: 'Documents', path: '/admin/documents', icon: <FileText size={16} /> },
      { label: 'Jobs', path: '/admin/jobs', icon: <Briefcase size={16} /> },
    ],
  },
  {
    group: 'Prompt Management',
    items: [
      { label: 'Prompts', path: '/admin/prompts', icon: <BookOpen size={16} /> },
    ],
  },
]

export default function AdminLayout() {
  const navigate = useNavigate()

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
          <button
            className="admin-nav-item"
            onClick={() => navigate('/chat')}
          >
            <MessageSquare size={16} />
            <span>Chat</span>
          </button>
        </div>
      </aside>

      <main className="admin-main">
        <Outlet />
      </main>
    </div>
  )
}
