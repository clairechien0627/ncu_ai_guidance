import { Suspense, lazy, useMemo, useState } from 'react'
import { ArrowLeft, Search, MessageSquare, User, GraduationCap } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import PdfPanel from '../components/viewer/PdfPanel'
import { getSummaries, type SummaryItem } from '../api'

const DocChat = lazy(() => import('../components/chat/DocChat'))
import { getTitle, getYear, getStudentName } from '../utils/filenameParse'
import { getCollegeLabel, getCollegeBadgeColors } from '../utils/collegeUtils'
import { SectionBlock } from '../components/SectionBlock'
import '../App.css'
import './ProjectsPage.css'

// ── Left card ─────────────────────────────────────────────────────────────────

function ProjectCard({ item, active, onClick }: {
  item: SummaryItem
  active: boolean
  onClick: () => void
}) {
  const title   = getTitle(item.filename)
  const year    = getYear(item.filename)
  const student = getStudentName(item.filename)
  const college = getCollegeLabel(item.department)
  const { bg, color } = getCollegeBadgeColors(college)

  return (
    <div className={`pp-card${active ? ' pp-card--active' : ''}`} onClick={onClick}>
      <div className="pp-card-top">
        <span className="pp-college-badge" style={{ background: bg, color }}>{college}</span>
        {year && <span className="pp-card-year">{year}</span>}
      </div>
      <div className="pp-card-title">{title}</div>
      <div className="pp-card-meta">
        {student && (
          <div className="pp-card-meta-row">
            <User size={11} className="pp-card-meta-icon" />
            <span>{student}</span>
          </div>
        )}
        <div className="pp-card-meta-row">
          <GraduationCap size={11} className="pp-card-meta-icon" />
          <span className="pp-card-dept-name">{item.department}</span>
        </div>
      </div>
    </div>
  )
}

// ── Right detail (outline mode) ───────────────────────────────────────────────

function ProjectDetail({ item, onOpenPdfChat }: { item: SummaryItem; onOpenPdfChat: () => void }) {
  const title   = getTitle(item.filename)
  const year    = getYear(item.filename)
  const student = getStudentName(item.filename)
  const college = getCollegeLabel(item.department)
  const { bg, color } = getCollegeBadgeColors(college)
  const summary = item.summary

  return (
    <div className="pp-detail-card">
      <div className="pp-detail-header">
        <div className="pp-detail-header-top">
          <span className="pp-college-badge" style={{ background: bg, color }}>{college}</span>
          {year && <span className="pp-detail-year">{year}</span>}
        </div>
        <h2 className="pp-detail-title">{title}</h2>
        <div className="pp-detail-meta-row">
          {student && (
            <span className="pp-detail-meta-item">
              <User size={13} /> {student}
            </span>
          )}
          <span className="pp-detail-meta-item">
            <GraduationCap size={13} /> {item.department}
          </span>
        </div>
      </div>

      {summary ? (
        <div className="pp-detail-sections">
          {summary.motivation && <SectionBlock title="研究動機與問題">{summary.motivation}</SectionBlock>}
          {summary.method     && <SectionBlock title="研究方法">{summary.method}</SectionBlock>}
          {summary.results    && <SectionBlock title="研究成果">{summary.results}</SectionBlock>}
        </div>
      ) : (
        <p className="pp-no-summary">尚未提取摘要</p>
      )}

      <div className="pp-detail-footer">
        <button className="pp-chat-btn" onClick={onOpenPdfChat} disabled={item.status !== 'ready'}>
          <MessageSquare size={15} /> 查看 PDF 與 AI 對話
        </button>
      </div>
    </div>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function ProjectsPage() {
  const [search,     setSearch]     = useState('')
  const [filterYear, setFilterYear] = useState('')
  const [filterDept, setFilterDept] = useState('')
  const [selected,   setSelected]   = useState<SummaryItem | null>(null)
  const [viewMode,   setViewMode]   = useState<'outline' | 'pdf-chat'>('outline')
  const [mobilePane, setMobilePane] = useState<'list' | 'detail'>('list')
  const [mobilePdfChatTab, setMobilePdfChatTab] = useState<'chat' | 'pdf'>('chat')
  const { data: items = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn:  () => getSummaries(true),
    staleTime: 60_000,
  })

  const years = useMemo(() => {
    const set = new Set(items.map(i => getYear(i.filename)).filter(Boolean))
    return Array.from(set).sort((a, b) => b.localeCompare(a))
  }, [items])

  const departments = useMemo(() => {
    const set = new Set(items.map(i => i.department))
    return Array.from(set).sort()
  }, [items])

  const filtered = useMemo(() => {
    return items.filter(item => {
      if (filterYear && getYear(item.filename) !== filterYear) return false
      if (filterDept && item.department !== filterDept) return false
      if (!search.trim()) return true
      const q = search.toLowerCase()
      return (
        getTitle(item.filename).toLowerCase().includes(q) ||
        getStudentName(item.filename).toLowerCase().includes(q) ||
        item.department.toLowerCase().includes(q) ||
        item.summary?.motivation?.toLowerCase().includes(q) ||
        item.summary?.tags?.some(t => t.toLowerCase().includes(q))
      )
    })
  }, [items, search, filterYear, filterDept])

  const handleSelectProject = (item: SummaryItem) => {
    setSelected(item)
    setViewMode('outline')
    setMobilePane('detail')
  }

  const handleOpenPdfChat = () => {
    if (!selected || selected.status !== 'ready') return
    setMobilePdfChatTab('chat')
    setViewMode('pdf-chat')
  }

  const handleBackToOutline = () => {
    setViewMode('outline')
  }

  // ── PDF+Chat Mode ──────────────────────────────────────────────────────────

  if (viewMode === 'pdf-chat' && selected) {
    return (
      <div className="pp-page pp-page--chat">
        {/* 簡潔返回列 */}
        <div className="pp-pdf-chat-bar">
          <button className="pp-mobile-back" style={{ display: 'inline-flex', padding: '4px 0' }} onClick={handleBackToOutline}>
            <ArrowLeft size={15} /> 返回計畫介紹
          </button>

          {/* 窄版 tab 切換 */}
          <div className="pp-mobile-tabs" role="tablist">
            {(['chat', 'pdf'] as const).map(tab => (
              <button
                key={tab}
                className={`pp-mobile-tab${mobilePdfChatTab === tab ? ' pp-mobile-tab--active' : ''}`}
                onClick={() => setMobilePdfChatTab(tab)}
              >
                {tab === 'chat' ? 'AI 問答' : 'PDF'}
              </button>
            ))}
          </div>
        </div>

        <div className={`pp-pdf-chat-grid pp-pdf-chat-grid--mobile-${mobilePdfChatTab}`}>
          <div className="pp-pdf-pane">
            <PdfPanel
              documents={[{ id: selected.id, filename: selected.filename, status: selected.status, created_at: selected.created_at }]}
              initialDocId={selected.id}
              onClose={handleBackToOutline}
            />
          </div>
          <div className="pp-chat-pane">
            <div className="pp-chat-card">
              <Suspense fallback={<div style={{ display: 'grid', placeItems: 'center', height: '100%', color: '#94a3b8' }}>載入中…</div>}>
                <DocChat docId={selected.id} filename={getTitle(selected.filename)} />
              </Suspense>
            </div>
          </div>
        </div>
      </div>
    )
  }

  // ── Outline Mode ───────────────────────────────────────────────────────────

  return (
    <div className="pp-page">
      <div className="pp-inner">
        {/* Top filter bar */}
        <div className="pp-topbar">
          <div className="pp-topbar-left">
            <span className="pp-topbar-title">專題成果</span>
            <span className="pp-topbar-sep">/</span>
            <span className="pp-topbar-sub">瀏覽歷年大專生研究計畫，並可開啟 PDF 與 AI 問答。</span>
          </div>
          <div className="pp-topbar-right">
            <div className="pp-search-wrap">
              <Search size={12} className="pp-search-icon" />
              <input
                className="pp-search"
                placeholder="搜尋計畫、姓名…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <select className="pp-filter-select" value={filterYear} onChange={e => setFilterYear(e.target.value)}>
              <option value="">全部年份</option>
              {years.map(y => <option key={y} value={y}>{y} 年</option>)}
            </select>
            <select className="pp-filter-select" value={filterDept} onChange={e => setFilterDept(e.target.value)}>
              <option value="">全部系所</option>
              {departments.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <span className="pp-count">共 <strong>{filtered.length}</strong> 筆</span>
          </div>
        </div>

        {/* 1:2 grid（窄螢幕切換 list/detail 單欄） */}
        <div className={`pp-grid pp-grid--${mobilePane}`}>
          <div className="pp-left">
            <div className="pp-card-list">
              {filtered.map(item => (
                <ProjectCard
                  key={item.id}
                  item={item}
                  active={selected?.id === item.id}
                  onClick={() => handleSelectProject(item)}
                />
              ))}
              {filtered.length === 0 && (
                <div className="pp-empty-list">沒有符合的計畫</div>
              )}
            </div>
          </div>

          <div className="pp-right">
            {selected ? (
              <>
                {/* Mobile back button */}
                <button
                  className="pp-mobile-back"
                  onClick={() => setMobilePane('list')}
                >
                  <ArrowLeft size={15} /> 返回計畫列表
                </button>
                <ProjectDetail item={selected} onOpenPdfChat={handleOpenPdfChat} />
              </>
            ) : (
              <div className="pp-empty-state">
                <MessageSquare size={56} color="#c6d0dd" strokeWidth={1.2} />
                <p className="pp-empty-state-title">選擇一筆研究計畫即可查看詳細介紹</p>
                <p className="pp-empty-state-sub">接著可進一步開啟 PDF 與 AI 問答模式</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
