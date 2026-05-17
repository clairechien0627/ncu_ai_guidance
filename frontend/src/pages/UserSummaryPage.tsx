import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Search } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getSummaries, type SummaryItem } from '../api'
import './UserSummaryPage.css'

function getTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    summarized: { label: '已完成',  cls: 'sp-status--summarized' },
    processing:  { label: '處理中',  cls: 'sp-status--processing' },
    error:       { label: '錯誤',    cls: 'sp-status--error' },
    pending:     { label: '待處理',  cls: 'sp-status--pending' },
  }
  const { label, cls } = map[status] ?? map.pending
  return <span className={`sp-status ${cls}`}>{label}</span>
}

function SummaryCard({ item }: { item: SummaryItem }) {
  const [open, setOpen] = useState(false)
  const hasSummary = !!item.summary

  return (
    <div className="sp-card">
      <div className="sp-card-header">
        <span className="sp-filename" title={item.filename}>{getTitle(item.filename)}</span>
        <StatusBadge status={item.batch_status} />
        {hasSummary && (
          <button className="sp-toggle-btn" onClick={() => setOpen(o => !o)}>
            {open ? '收起' : '展開'}
          </button>
        )}
      </div>

      {hasSummary && item.summary!.tags?.length > 0 && (
        <div className="sp-tags">
          {item.summary!.tags.map((tag: string) => (
            <span key={tag} className="sp-tag">{tag}</span>
          ))}
        </div>
      )}

      {hasSummary && !open && (
        <div className="sp-preview">
          {item.summary!.motivation.slice(0, 120)}{item.summary!.motivation.length > 120 ? '…' : ''}
        </div>
      )}

      {hasSummary && open && (
        <div className="sp-sections">
          {([
            { label: '研究動機與問題', text: item.summary!.motivation },
            { label: '研究方法',       text: item.summary!.method },
            { label: '研究成果',       text: item.summary!.results },
          ] as const).map(({ label, text }) => text ? (
            <div key={label}>
              <div className="sp-section-label">{label}</div>
              <div className="sp-section-text">{text}</div>
            </div>
          ) : null)}
        </div>
      )}

      {!hasSummary && (
        <div className="sp-no-summary">尚未提取摘要</div>
      )}
    </div>
  )
}

export default function UserSummaryPage() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')

  const { data: items = [], isLoading: loading } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })

  const filtered = search.trim()
    ? items.filter(it =>
        it.filename.toLowerCase().includes(search.toLowerCase()) ||
        it.summary?.motivation?.includes(search) ||
        it.summary?.method?.includes(search) ||
        it.summary?.results?.includes(search) ||
        (it.summary?.tags as string[] | undefined)?.some(t => t.includes(search))
      )
    : items

  const summarized = items.filter(i => i.summary).length

  return (
    <div className="sp-root">
      <div className="sp-header">
        <button className="sp-back-btn" onClick={() => navigate('/chat')}>
          <ArrowLeft size={14} />
          返回
        </button>
        <h1 className="sp-title">大專生計畫摘要</h1>
        <span className="sp-stats">{summarized} / {items.length} 份已完成</span>
      </div>

      <div className="sp-search-wrap">
        <Search size={14} className="sp-search-icon" />
        <input
          className="sp-search"
          placeholder="搜尋檔名、標籤、內容…"
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>

      <div className="sp-list">
        {loading ? (
          <div className="sp-empty">載入中…</div>
        ) : filtered.length === 0 ? (
          <div className="sp-empty">{search ? '沒有符合的文件' : '尚無文件'}</div>
        ) : (
          filtered.map((item, i) => (
            <SummaryCard key={`${item.id}-${i}`} item={item} />
          ))
        )}
      </div>
    </div>
  )
}
