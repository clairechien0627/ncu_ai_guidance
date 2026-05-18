import { useCallback, useRef, useState } from 'react'

/**
 * Drag-to-resize table columns.
 * Pass initial widths (px) for each column index; use null for non-resizable columns.
 * Optionally provide a storageKey to persist widths in localStorage.
 */
export function useColumnResize(initial: (number | null)[], storageKey?: string) {
  const load = (): (number | null)[] => {
    if (!storageKey) return initial
    try {
      const saved = localStorage.getItem(storageKey)
      if (saved) {
        const parsed: (number | null)[] = JSON.parse(saved)
        if (Array.isArray(parsed) && parsed.length === initial.length) return parsed
      }
    } catch { /* ignore */ }
    return initial
  }

  const [widths, setWidths] = useState<(number | null)[]>(load)
  const drag = useRef<{ col: number; startX: number; startW: number } | null>(null)

  const save = useCallback((next: (number | null)[]) => {
    if (storageKey) {
      try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* ignore */ }
    }
  }, [storageKey])

  const onResizeStart = useCallback((col: number) => (e: React.MouseEvent) => {
    const startW = widths[col]
    if (startW === null) return
    e.preventDefault()
    e.stopPropagation()
    drag.current = { col, startX: e.clientX, startW }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const onMove = (ev: MouseEvent) => {
      if (!drag.current) return
      const delta = ev.clientX - drag.current.startX
      const newW = Math.max(40, drag.current.startW + delta)
      setWidths(prev => {
        const next = prev.map((w, i) => (i === drag.current!.col ? newW : w))
        save(next)
        return next
      })
    }
    const onUp = () => {
      drag.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [widths, save])

  const resetWidths = useCallback(() => {
    setWidths(initial)
    if (storageKey) { try { localStorage.removeItem(storageKey) } catch { /* ignore */ } }
  }, [initial, storageKey])

  return { widths, onResizeStart, resetWidths }
}
