import { useState, useMemo } from 'react'
import { ChevronLeft, ChevronRight, Search, BookOpen } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getSummaries, type SummaryItem } from '../api'
import './ProjectsPage.css'

// ── Helpers ───────────────────────────────────────────────────────────────────

function getTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

const DEPT_COLORS: Record<string, { bg: string; color: string }> = {
  '資訊工程': { bg: '#dbeafe', color: '#1d4ed8' },
  '電機工程': { bg: '#ede9fe', color: '#6d28d9' },
  '機械工程': { bg: '#fce7f3', color: '#9d174d' },
  '化學工程': { bg: '#fef3c7', color: '#92400e' },
  '土木工程': { bg: '#d1fae5', color: '#065f46' },
  '其他':     { bg: '#f3f4f6', color: '#6b7280' },
}

function deptColor(dept: string) {
  for (const [key, val] of Object.entries(DEPT_COLORS)) {
    if (dept.includes(key)) return val
  }
  return DEPT_COLORS['其他']
}

// ── Left card ─────────────────────────────────────────────────────────────────

function ProjectCard({ item, active, onClick }: {
  item: SummaryItem
  active: boolean
  onClick: () => void
}) {
  const title = getTitle(item.filename)
  const { bg, color } = deptColor(item.department)
  const tags = item.summary?.tags?.slice(0, 3) ?? []
  const preview = item.summary?.motivation?.slice(0, 60) ?? null

  return (
    <div className={`pp-card${active ? ' pp-card--active' : ''}`} onClick={onClick}>
      <div className="pp-card-top">
        <span className="pp-dept-badge" style={{ background: bg, color }}>{item.department}</span>
      </div>
      <div className="pp-card-title">{title}</div>
      {tags.length > 0 && (
        <div className="pp-card-tags">
          {tags.map(t => <span key={t} className="pp-card-tag">{t}</span>)}
        </div>
      )}
      {preview && <div className="pp-card-preview">{preview}…</div>}
    </div>
  )
}

// ── Right detail ─────────────────────────────────────────────────────────────

function ProjectDetail({ item }: { item: SummaryItem }) {
  const title = getTitle(item.filename)
  const { bg, color } = deptColor(item.department)
  const summary = item.summary

  return (
    <div>
      <h1 className="pp-detail-title">{title}</h1>
      <div className="pp-detail-meta">
        <span className="pp-dept-badge" style={{ background: bg, color, fontSize: 12, padding: '3px 10px' }}>
          {item.department}
        </span>
        {item.batch_status === 'summarized' && (
          <span style={{ fontSize: 11, color: '#16a34a', fontWeight: 600 }}>✓ 已提取摘要</span>
        )}
      </div>

      {summary?.tags && summary.tags.length > 0 && (
        <div className="pp-detail-tags">
          {summary.tags.map(t => <span key={t} className="pp-detail-tag">{t}</span>)}
        </div>
      )}

      {summary ? (
        <>
          {[
            { label: '研究動機與問題', text: summary.motivation },
            { label: '研究方法',       text: summary.method },
            { label: '研究成果',       text: summary.results },
          ].map(({ label, text }) => text ? (
            <div key={label} className="pp-section">
              <div className="pp-section-label">{label}</div>
              <div className="pp-section-text">{text}</div>
            </div>
          ) : null)}

          {(summary.questions?.length ?? 0) > 0 && (
            <>
              <div className="pp-questions-title">
                導讀題目
                <span className="pp-questions-count">{summary.questions!.length} 題</span>
              </div>
              {summary.questions!.map((q, i) => (
                <div key={i} className="pp-question-card">
                  <span className="pp-question-num">Q{i + 1}</span>
                  <span className="pp-question-text">{q}</span>
                </div>
              ))}
            </>
          )}
        </>
      ) : (
        <div style={{ color: '#94a3b8', fontSize: 13, fontStyle: 'italic', marginTop: 20 }}>
          尚未提取摘要
        </div>
      )}
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ProjectsPage() {
  const [collapsed, setCollapsed] = useState(false)
  const [search, setSearch] = useState('')
  const [deptFilter, setDeptFilter] = useState('all')
  const [selected, setSelected] = useState<SummaryItem | null>(null)

  const { data: items = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })

  const departments = useMemo(() => {
    const set = new Set(items.map(i => i.department))
    return Array.from(set).sort()
  }, [items])

  const filtered = useMemo(() => {
    return items.filter(item => {
      if (deptFilter !== 'all' && item.department !== deptFilter) return false
      if (!search.trim()) return true
      const q = search.toLowerCase()
      return (
        item.filename.toLowerCase().includes(q) ||
        item.summary?.motivation?.toLowerCase().includes(q) ||
        item.summary?.tags?.some(t => t.toLowerCase().includes(q))
      )
    })
  }, [items, search, deptFilter])

  return (
    <div className="pp-root">
      {/* Left panel */}
      <div className={`pp-left${collapsed ? ' pp-left--collapsed' : ' pp-left--open'}`}>
        <div className="pp-left-head">
          {!collapsed && <span className="pp-left-title">計畫瀏覽</span>}
          <button className="pp-collapse-btn" onClick={() => setCollapsed(c => !c)} title={collapsed ? '展開' : '收折'}>
            {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
          </button>
        </div>

        {!collapsed && (
          <>
            <div className="pp-filters">
              <div className="pp-search-wrap">
                <Search size={12} className="pp-search-icon" />
                <input
                  className="pp-search"
                  placeholder="搜尋計畫、標籤…"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                />
              </div>
              <select
                className="pp-filter-select"
                value={deptFilter}
                onChange={e => setDeptFilter(e.target.value)}
              >
                <option value="all">所有系所</option>
                {departments.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>

            <div className="pp-card-list">
              {filtered.map(item => (
                <ProjectCard
                  key={item.id}
                  item={item}
                  active={selected?.id === item.id}
                  onClick={() => setSelected(item)}
                />
              ))}
              {filtered.length === 0 && (
                <div style={{ padding: '20px 10px', textAlign: 'center', color: '#94a3b8', fontSize: 12 }}>
                  沒有符合的計畫
                </div>
              )}
            </div>
            <div className="pp-list-count">{filtered.length} / {items.length} 份</div>
          </>
        )}
      </div>

      {/* Right detail */}
      <div className="pp-right">
        {selected ? (
          <ProjectDetail item={selected} />
        ) : (
          <div className="pp-empty-state">
            <BookOpen size={36} color="#c6d0dd" strokeWidth={1.5} />
            <div className="pp-empty-state-title">請從左側選擇一份計畫</div>
            <div className="pp-empty-state-sub">點擊計畫卡片即可查看完整摘要</div>
          </div>
        )}
      </div>
    </div>
  )
}
