import { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { createPortal } from 'react-dom'

interface Props {
  open: boolean
  title: string
  onClose: () => void
  children: React.ReactNode
  width?: number
  minWidth?: number
  maxWidth?: number
}

export function DrawerPanel({
  open, title, onClose, children,
  width: initialWidth = 480,
  minWidth = 320,
  maxWidth = Math.round(window.innerWidth * 0.85),
}: Props) {
  const [width, setWidth] = useState(initialWidth)
  const dragging = useRef(false)
  const startX  = useRef(0)
  const startW  = useRef(0)

  // Sync if parent changes the initial width prop
  useEffect(() => { setWidth(initialWidth) }, [initialWidth])

  // Esc to close
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragging.current = true
    startX.current = e.clientX
    startW.current = width

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return
      // dragging leftward from the right edge increases width
      const delta = startX.current - ev.clientX
      const next  = Math.min(Math.max(startW.current + delta, minWidth), maxWidth)
      setWidth(next)
    }

    const onUp = () => {
      dragging.current = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.body.style.cursor    = 'col-resize'
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup',   onUp)
  }, [width, minWidth, maxWidth])

  if (!open) return null

  return createPortal(
    <>
      <div className="adm-drawer-backdrop" onClick={onClose} />
      <div className="adm-drawer" style={{ width }} role="dialog" aria-modal>
        {/* Resize handle */}
        <div
          onMouseDown={onMouseDown}
          style={{
            position: 'absolute',
            left: 0, top: 0, bottom: 0,
            width: 6,
            cursor: 'col-resize',
            zIndex: 10,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
          title="拖拉調整寬度"
        >
          {/* Visual indicator — faint vertical dots */}
          <div style={{
            width: 3,
            height: 32,
            borderRadius: 999,
            background: 'var(--adm-border-2)',
            opacity: 0.6,
            transition: 'opacity 150ms',
          }}
            onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
            onMouseLeave={e => (e.currentTarget.style.opacity = '0.6')}
          />
        </div>

        <div className="adm-drawer-header">
          <span className="adm-drawer-title">{title}</span>
          <button className="adm-btn-icon" onClick={onClose} aria-label="關閉">
            <X size={16} />
          </button>
        </div>
        <div className="adm-drawer-body">{children}</div>
      </div>
    </>,
    document.body
  )
}
