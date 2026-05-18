import { useCallback, useRef, useState } from 'react'
import type React from 'react'

const RESIZABLE_MIN_DEFAULT = 60
const RESIZABLE_MAX_DEFAULT = 600

export interface ColDef {
  width: number
  resizable: boolean
  minWidth?: number  // only for resizable cols; default RESIZABLE_MIN_DEFAULT
  maxWidth?: number  // only for resizable cols; default RESIZABLE_MAX_DEFAULT
}

/**
 * Linked column resize: dragging any divider propagates to the nearest
 * resizable column on each side (L grows, R shrinks), keeping total width constant.
 *
 * onDivider(i) = handler for divider after column i
 * isActive(i)  = whether that divider can be dragged
 */
export function useLinkedColumnResize(cols: ColDef[], storageKey?: string) {
  const load = (): number[] => {
    const initial = cols.map(c => c.width)
    if (!storageKey) return initial
    try {
      const saved = localStorage.getItem(storageKey)
      if (saved) {
        const parsed: number[] = JSON.parse(saved)
        if (Array.isArray(parsed) && parsed.length === cols.length) {
          // fixed 欄永遠用 COLS 的值（改程式碼立即生效）
          // resizable 欄才套用使用者存的偏好
          return cols.map((c, i) => c.resizable ? parsed[i] : c.width)
        }
      }
    } catch { /* ignore */ }
    return initial
  }

  const [widths, setWidths] = useState<number[]>(load)
  const widthsRef = useRef(widths)
  widthsRef.current = widths
  const colsRef = useRef(cols)
  colsRef.current = cols

  const findLeft = (fromIdx: number): number | null => {
    for (let i = fromIdx; i >= 0; i--) {
      if (colsRef.current[i]?.resizable) return i
    }
    return null
  }

  const findRight = (fromIdx: number): number | null => {
    for (let i = fromIdx; i < colsRef.current.length; i++) {
      if (colsRef.current[i]?.resizable) return i
    }
    return null
  }

  const isActive = (divAfter: number): boolean =>
    findLeft(divAfter) !== null || findRight(divAfter + 1) !== null

  const clamp = (i: number, v: number): number => {
    const c = colsRef.current[i]
    if (!c.resizable) return c.width
    return Math.max(
      c.minWidth ?? RESIZABLE_MIN_DEFAULT,
      Math.min(c.maxWidth ?? RESIZABLE_MAX_DEFAULT, v),
    )
  }

  const onDivider = useCallback(
    (divAfter: number) => (e: React.MouseEvent) => {
      const L = findLeft(divAfter)
      const R = findRight(divAfter + 1)
      if (L === null && R === null) return

      e.preventDefault()
      e.stopPropagation()

      const startX = e.clientX
      const startW = [...widthsRef.current]
      let dragged = false

      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'

      const onMove = (ev: MouseEvent) => {
        const delta = ev.clientX - startX
        if (Math.abs(delta) > 2) dragged = true
        setWidths(prev => {
          const next = [...prev]
          if (L !== null && R !== null) {
            const newL = clamp(L, startW[L] + delta)
            const actualDelta = newL - startW[L]
            next[L] = newL
            next[R] = clamp(R, startW[R] - actualDelta)
          } else if (L !== null) {
            next[L] = clamp(L, startW[L] + delta)
          } else if (R !== null) {
            next[R] = clamp(R, startW[R] - delta)
          }
          if (storageKey) {
            try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* ignore */ }
          }
          return next
        })
      }

      const onUp = () => {
        document.body.style.cursor = ''
        document.body.style.userSelect = ''
        document.removeEventListener('mousemove', onMove)
        document.removeEventListener('mouseup', onUp)
        // 若確實拖動過，在 capture 階段攔截並取消隨後觸發的 click（避免誤觸排序）
        if (dragged) {
          const cancelClick = (ev: MouseEvent) => {
            ev.stopPropagation()
            ev.preventDefault()
            document.removeEventListener('click', cancelClick, true)
          }
          document.addEventListener('click', cancelClick, true)
        }
      }

      document.addEventListener('mousemove', onMove)
      document.addEventListener('mouseup', onUp)
    },
    [storageKey], // eslint-disable-line react-hooks/exhaustive-deps
  )

  /** Spread onto a <span> to get the correct class + handler for divider after column i */
  const div = (i: number) => ({
    className: `adm-col-resize-handle${isActive(i) ? ' adm-col-resize-handle--live' : ''}` as string,
    onMouseDown: isActive(i) ? onDivider(i) : undefined,
  })

  return { widths, onDivider, isActive, div }
}
