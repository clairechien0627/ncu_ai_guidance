import { useState, useCallback } from 'react'

export function useColumnVisibility(
  storageKey: string,
  defaults: Record<string, boolean>,
) {
  const load = (): Record<string, boolean> => {
    try {
      const saved = localStorage.getItem(storageKey)
      if (saved) return { ...defaults, ...JSON.parse(saved) }
    } catch { /* ignore */ }
    return { ...defaults }
  }

  const [visible, setVisible] = useState<Record<string, boolean>>(load)

  const toggle = useCallback((key: string) => {
    setVisible(prev => {
      const next = { ...prev, [key]: !prev[key] }
      try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }, [storageKey])

  const reset = useCallback(() => {
    setVisible({ ...defaults })
    try { localStorage.removeItem(storageKey) } catch { /* ignore */ }
  }, [storageKey, defaults])

  return { visible, toggle, reset }
}
