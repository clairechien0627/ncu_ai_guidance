import { useState, useEffect, useRef } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { Menu, X, LogIn, LogOut, User } from 'lucide-react'
import ncuLogo from '../assets/NCULogo.png'
import { useAuthStore } from '../stores/authStore'
import './UserNavbar.css'

const NAV_ITEMS = [
  { path: '/projects',   label: '計畫瀏覽' },
  { path: '/chat',       label: '問答助理' },
  { path: '/assessment', label: '適性探索' },
]

function MobileOverlay({ pathname, onClose }: { pathname: string; onClose: () => void }) {
  return createPortal(
    <div className="unav-overlay">
      <div className="unav-overlay-top">
        <Link to="/" className="unav-logo" onClick={onClose}>
          <img src={ncuLogo} alt="國立中央大學" className="unav-logo-img" />
          <div className="unav-logo-text">
            <span className="unav-logo-name">國立中央大學</span>
            <span className="unav-logo-sub">大專生研究計畫平台</span>
          </div>
        </Link>
        <button className="unav-overlay-close" onClick={onClose} aria-label="關閉選單">
          <X size={22} />
        </button>
      </div>
      <div className="unav-overlay-body">
        {NAV_ITEMS.map(item => (
          <Link
            key={item.path}
            to={item.path}
            className={`unav-overlay-item${pathname.startsWith(item.path) ? ' unav-overlay-item--active' : ''}`}
            onClick={onClose}
          >
            {item.label}
          </Link>
        ))}
      </div>
    </div>,
    document.body
  )
}

function UserMenu() {
  const navigate = useNavigate()
  const user = useAuthStore(s => s.user)
  const logout = useAuthStore(s => s.logout)
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (!user) {
    return (
      <Link to="/login" className="unav-login-btn">
        <LogIn size={15} />
        登入
      </Link>
    )
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="unav-user-btn" onClick={() => setOpen(o => !o)}>
        <User size={15} />
        {user.display_name || user.username}
      </button>
      {open && (
        <div className="unav-user-menu">
          <div className="unav-user-menu-name">{user.email}</div>
          <div className="unav-user-menu-role">{user.role === 'admin' ? '管理員' : '使用者'}</div>
          {user.role === 'admin' && (
            <button className="unav-user-menu-item" onClick={() => { setOpen(false); navigate('/admin') }}>
              後台管理
            </button>
          )}
          <button className="unav-user-menu-item unav-user-menu-item--danger" onClick={() => { logout(); setOpen(false) }}>
            <LogOut size={13} /> 登出
          </button>
        </div>
      )}
    </div>
  )
}

export default function UserNavbar() {
  const location = useLocation()
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => { setMobileOpen(false) }, [location.pathname])

  useEffect(() => {
    if (!mobileOpen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [mobileOpen])

  return (
    <>
      <nav className="unav">
        <div className="unav-inner">
          <Link to="/" className="unav-logo">
            <img src={ncuLogo} alt="國立中央大學" className="unav-logo-img" />
            <div className="unav-logo-text">
              <span className="unav-logo-name">國立中央大學</span>
              <span className="unav-logo-sub">大專生研究計畫平台</span>
            </div>
          </Link>

          <div className="unav-links">
            {NAV_ITEMS.map(item => (
              <Link
                key={item.path}
                to={item.path}
                className={`nav-chip${location.pathname.startsWith(item.path) ? ' nav-chip--active' : ''}`}
              >
                {item.label}
              </Link>
            ))}
            <UserMenu />
          </div>

          <button
            className="unav-hamburger"
            onClick={() => setMobileOpen(v => !v)}
            aria-label={mobileOpen ? '關閉選單' : '開啟選單'}
            aria-expanded={mobileOpen}
          >
            {mobileOpen ? <X size={24} /> : <Menu size={24} />}
          </button>
        </div>
      </nav>

      {mobileOpen && (
        <MobileOverlay pathname={location.pathname} onClose={() => setMobileOpen(false)} />
      )}
    </>
  )
}
