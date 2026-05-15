import { useState, useCallback } from 'react'
import { getParserCacheContent, type ParserCacheInfo } from '../api'

export interface UseParserCompareReturn {
  open: boolean
  activePanels: Set<string>
  panelContents: Record<string, { pages: string[]; loading: boolean }>
  page: number
  pageInputValue: string
  pageInputEditing: boolean
  openCompare: (docId: number, parserCaches: ParserCacheInfo[]) => void
  closeCompare: () => void
  togglePanel: (docId: number, key: string) => void
  handlePage: (page: number) => void
  setPageInputValue: React.Dispatch<React.SetStateAction<string>>
  setPageInputEditing: React.Dispatch<React.SetStateAction<boolean>>
}

export function useParserCompare(): UseParserCompareReturn {
  const [open, setOpen] = useState(false)
  const [activePanels, setActivePanels] = useState<Set<string>>(new Set())
  const [panelContents, setPanelContents] = useState<Record<string, { pages: string[]; loading: boolean }>>({})
  const [page, setPage] = useState(0)
  const [pageInputValue, setPageInputValue] = useState('1')
  const [pageInputEditing, setPageInputEditing] = useState(false)

  const loadPanelContent = useCallback((docId: number, parser: string) => {
    setPanelContents(prev => ({ ...prev, [parser]: { pages: [], loading: true } }))
    getParserCacheContent(docId, parser)
      .then(data => setPanelContents(prev => ({ ...prev, [parser]: { pages: data.pages, loading: false } })))
      .catch(() => setPanelContents(prev => ({ ...prev, [parser]: { pages: ['（快取讀取失敗）'], loading: false } })))
  }, [])

  const togglePanel = useCallback((docId: number, key: string) => {
    setActivePanels(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
        if (key !== 'pdf') loadPanelContent(docId, key)
      }
      return next
    })
  }, [loadPanelContent])

  const handlePage = useCallback((p: number) => {
    setPage(p)
    setPageInputValue(String(p + 1))
  }, [])

  const openCompare = useCallback((docId: number, parserCaches: ParserCacheInfo[]) => {
    setPanelContents({})
    setPage(0)
    setPageInputValue('1')
    setPageInputEditing(false)
    const available = parserCaches.filter(c => c.available).map(c => c.parser)
    setActivePanels(new Set(['pdf', ...available.slice(0, 2)]))
    available.slice(0, 2).forEach(p => loadPanelContent(docId, p))
    setOpen(true)
  }, [loadPanelContent])

  const closeCompare = useCallback(() => setOpen(false), [])

  return {
    open, activePanels, panelContents, page,
    pageInputValue, pageInputEditing,
    openCompare, closeCompare, togglePanel, handlePage,
    setPageInputValue, setPageInputEditing,
  }
}
