import { useState, useEffect } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { createPortal } from 'react-dom'
import { Menu, X } from 'lucide-react'
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
          <div className="unav-logo-icon">R</div>
          <div className="unav-logo-text">
            <span className="unav-logo-name">Report Agent</span>
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
            <div className="unav-logo-icon">R</div>
            <div className="unav-logo-text">
              <span className="unav-logo-name">Report Agent</span>
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
