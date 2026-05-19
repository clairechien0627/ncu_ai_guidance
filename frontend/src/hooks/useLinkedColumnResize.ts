import { useCallback, useEffect, useRef, useState } from 'react'
import type React from 'react'

const RESIZABLE_MIN_DEFAULT = 60
const RESIZABLE_MAX_DEFAULT = 600

export interface ColDef {
  width: number
  resizable: boolean
  minWidth?: number
  maxWidth?: number
}

export function useLinkedColumnResize(cols: ColDef[], storageKey?: string) {
  const load = (): number[] => {
    const defaults = cols.map(c => c.width)
    if (!storageKey) return defaults
    try {
      const saved = localStorage.getItem(storageKey)
      if (saved) {
        const parsed: number[] = JSON.parse(saved)
        if (Array.isArray(parsed) && parsed.length === cols.length) {
          // Fixed columns always use ColDef width; only resizable cols get user preference
          return cols.map((c, i) => c.resizable ? parsed[i] : c.width)
        }
      }
    } catch { /* ignore */ }
    return defaults
  }

  // Internal raw widths (only resizable cols are meaningful here)
  const [widths, setWidths] = useState<number[]>(load)

  const colsRef = useRef(cols)
  colsRef.current = cols

  // ── safeWidths ────────────────────────────────────────────────────────────
  // Derived every render:
  //   - Length mismatch → reset to ColDef defaults (handles column add/remove)
  //   - Fixed columns → always locked to c.width (can never drift)
  //   - Resizable columns → use stored user preference
  const safeWidths: number[] =
    widths.length !== cols.length
      ? cols.map(c => c.width)
      : cols.map((c, i) => (c.resizable ? widths[i] : c.width))

  const safeWidthsRef = useRef(safeWidths)
  safeWidthsRef.current = safeWidths

  // ── Sync internal state when column count changes ────────────────────────
  // Runs after every render but only calls setWidths when length actually changed.
  // We intentionally omit deps to run every render (cheap check).
  const prevLengthRef = useRef(cols.length)
  useEffect(() => {
    if (prevLengthRef.current !== cols.length) {
      prevLengthRef.current = cols.length
      setWidths(cols.map(c => c.width))
      if (storageKey) {
        try { localStorage.removeItem(storageKey) } catch { /* ignore */ }
      }
    }
  })

  // ── Resize logic ──────────────────────────────────────────────────────────

  const isActive = (divAfter: number): boolean =>
    !!(colsRef.current[divAfter]?.resizable || colsRef.current[divAfter + 1]?.resizable)

  const clamp = (i: number, v: number): number => {
    const c = colsRef.current[i]
    if (!c?.resizable) return c?.width ?? v
    return Math.max(
      c.minWidth ?? RESIZABLE_MIN_DEFAULT,
      Math.min(c.maxWidth ?? RESIZABLE_MAX_DEFAULT, v),
    )
  }

  const onDivider = useCallback(
    (divAfter: number) => (e: React.MouseEvent) => {
      // Only the two columns directly flanking the divider participate;
      // fixed columns are never selected — no jumping over them.
      const L = colsRef.current[divAfter]?.resizable ? divAfter : null
      const R = colsRef.current[divAfter + 1]?.resizable ? divAfter + 1 : null
      if (L === null && R === null) return

      e.preventDefault()
      e.stopPropagation()

      const startX = e.clientX
      // Snapshot from safeWidths so baseline is always correct
      const startW = [...safeWidthsRef.current]
      let dragged = false

      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'

      const onMove = (ev: MouseEvent) => {
        const delta = ev.clientX - startX
        if (Math.abs(delta) > 2) dragged = true

        setWidths(() => {
          // Reconstruct from ColDef to prevent fixed cols from drifting
          const next = colsRef.current.map((c, i) =>
            c.resizable ? (safeWidthsRef.current[i] ?? c.width) : c.width
          )
          if (L !== null && R !== null) {
            const newL = clamp(L, startW[L] + delta)
            next[L] = newL
            next[R] = clamp(R, startW[R] - (newL - startW[L]))
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

  const div = (i: number) => ({
    className: `adm-col-resize-handle${isActive(i) ? ' adm-col-resize-handle--live' : ''}` as string,
    onMouseDown: isActive(i) ? onDivider(i) : undefined,
  })

  return { widths: safeWidths, onDivider, isActive, div }
}
