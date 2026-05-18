import { useState, useCallback } from 'react'

type Dir = 'asc' | 'desc'
type SortState = { key: string; dir: Dir } | null
type Getter<T> = (item: T) => number | string | null | undefined

export function useTableSort<T>(getters: Record<string, Getter<T>>) {
  const [sort, setSort] = useState<SortState>(null)

  const toggle = useCallback((k: string) => {
    setSort(prev => {
      if (!prev || prev.key !== k) return { key: k, dir: 'asc' }
      if (prev.dir === 'asc') return { key: k, dir: 'desc' }
      return null
    })
  }, [])

  const apply = (data: T[]): T[] => {
    if (!sort) return data
    const get = getters[sort.key]
    if (!get) return data
    return [...data].sort((a, b) => {
      const av = get(a), bv = get(b)
      if (av == null && bv == null) return 0
      if (av == null) return 1
      if (bv == null) return -1
      const cmp = (typeof av === 'number' && typeof bv === 'number')
        ? av - bv
        : String(av).localeCompare(String(bv), 'zh-TW')
      return sort.dir === 'asc' ? cmp : -cmp
    })
  }

  // 排序指示符：未排序 '' | 升序 ' ↑' | 降序 ' ↓'
  const ind = (k: string) =>
    !sort || sort.key !== k ? '' : sort.dir === 'asc' ? ' ↑' : ' ↓'

  // 給 th 用的 props
  const th = (k: string) => ({
    onClick: () => toggle(k),
    style: { cursor: 'pointer' as const },
  })

  return { apply, ind, th, sort }
}
