import { useCallback, useState } from 'react'
import { getSummaries } from '../api'
import type { SummaryItem } from '../api'

export interface UseDocumentListReturn {
  items: SummaryItem[]
  setItems: React.Dispatch<React.SetStateAction<SummaryItem[]>>
  loading: boolean
  setLoading: React.Dispatch<React.SetStateAction<boolean>>
  search: string
  setSearch: React.Dispatch<React.SetStateAction<string>>
  deptFilter: string
  setDeptFilter: React.Dispatch<React.SetStateAction<string>>
  loadItems: () => Promise<SummaryItem[]>
}

export function useDocumentList(): UseDocumentListReturn {
  const [items, setItems] = useState<SummaryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [deptFilter, setDeptFilter] = useState('全部')

  const loadItems = useCallback(async (): Promise<SummaryItem[]> => {
    const data = await getSummaries()
    setItems(data)
    return data
  }, [])

  return { items, setItems, loading, setLoading, search, setSearch, deptFilter, setDeptFilter, loadItems }
}
