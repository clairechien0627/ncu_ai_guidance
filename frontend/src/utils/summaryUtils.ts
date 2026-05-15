import type { SummaryItem } from '../api'

export function getDisplayTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

export function getProjectNumber(filename: string) {
  return filename.match(/^([^_]+)_/)?.[1] ?? ''
}

export function relativeTime(iso?: string) {
  if (!iso) return ''
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return '剛剛'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分鐘前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小時前`
  return `${Math.floor(seconds / 86400)} 天前`
}

export function getStatusMeta(status: SummaryItem['batch_status']) {
  if (status === 'summarized') return { label: '摘要完成', cls: 'spill spill-done' }
  if (status === 'processing') return { label: '摘要中', cls: 'spill spill-working' }
  if (status === 'error') return { label: '摘要失敗', cls: 'spill spill-error' }
  return { label: '未摘要', cls: 'spill spill-pending' }
}

export function getCollegeInfo(dept: string): { label: string; cls: string } {
  if (/資工|資訊|電機|通訊/.test(dept)) return { label: '資訊相關', cls: 'cbadge cbadge-cs' }
  if (/工程|機械|土木|化材|環工/.test(dept)) return { label: '工程學院', cls: 'cbadge cbadge-engineering' }
  if (/管理|企管|財金|經濟/.test(dept)) return { label: '管理學院', cls: 'cbadge cbadge-management' }
  if (/地科|地球|大氣/.test(dept)) return { label: '地球科學', cls: 'cbadge cbadge-earth' }
  if (/生醫|生命|醫/.test(dept)) return { label: '生醫相關', cls: 'cbadge cbadge-biomed' }
  if (/物理|化學|數學/.test(dept)) return { label: '理學院', cls: 'cbadge cbadge-science' }
  if (/文|歷史|哲學|英文/.test(dept)) return { label: '人文社科', cls: 'cbadge cbadge-liberal' }
  if (/客家/.test(dept)) return { label: '客家學院', cls: 'cbadge cbadge-hakka' }
  return { label: dept.slice(0, 4) || '其他', cls: 'cbadge cbadge-default' }
}
