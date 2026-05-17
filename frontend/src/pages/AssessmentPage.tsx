import { useState, useMemo } from 'react'
import { Search, Lightbulb, User, GraduationCap, CheckCircle, RotateCcw, ArrowRight, ArrowLeft } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getSummaries, type SummaryItem } from '../api'
import { getTitle, getYear, getStudentName } from '../utils/filenameParse'
import { getCollegeLabel, getCollegeBadgeColors } from '../utils/collegeUtils'
import './AssessmentPage.css'

// ── Left card ──────────────────────────────────────────────────────────────────

function DocCard({ item, active, onClick }: { item: SummaryItem; active: boolean; onClick: () => void }) {
  const title   = getTitle(item.filename)
  const year    = getYear(item.filename)
  const student = getStudentName(item.filename)
  const college = getCollegeLabel(item.department)
  const { bg, color } = getCollegeBadgeColors(college)
  const qCount  = item.summary!.questions!.length

  return (
    <div className={`ap-doc-card${active ? ' ap-doc-card--active' : ''}`} onClick={onClick}>
      <div className="ap-card-top">
        <span className="ap-college-badge" style={{ background: bg, color }}>{college}</span>
        {year && <span className="ap-card-year">{year}</span>}
      </div>
      <div className="ap-doc-title">{title}</div>
      <div className="ap-card-meta">
        {student && (
          <div className="ap-card-meta-row">
            <User size={11} className="ap-card-meta-icon" />
            <span>{student}</span>
          </div>
        )}
        <div className="ap-card-meta-row">
          <GraduationCap size={11} className="ap-card-meta-icon" />
          <span className="ap-card-dept">{item.department}</span>
          <span className="ap-card-q-count"> · {qCount} 題</span>
        </div>
      </div>
    </div>
  )
}

// ── Rating widget ──────────────────────────────────────────────────────────────

const SCORE_LABELS: Record<number, string> = {
  1: '完全不感興趣', 2: '不太感興趣', 3: '普通', 4: '有些感興趣', 5: '非常感興趣',
}

function ScoreButtons({ value, onChange }: { value: number | null; onChange: (v: number) => void }) {
  return (
    <div className="ap-score-row">
      <span className="ap-score-label-left">完全不感興趣</span>
      <div className="ap-score-btns">
        {[1, 2, 3, 4, 5].map(n => (
          <button
            key={n}
            className={`ap-score-btn${value === n ? ' ap-score-btn--active' : ''}`}
            onClick={() => onChange(n)}
            title={SCORE_LABELS[n]}
            type="button"
          >
            {n}
          </button>
        ))}
      </div>
      <span className="ap-score-label-right">非常感興趣</span>
    </div>
  )
}

// ── Quiz panel ─────────────────────────────────────────────────────────────────

function QuizPanel({ item, onBack, tab, onTabChange }: {
  item: SummaryItem
  onNavigate: (id: number) => void
  onBack: () => void
  tab: Tab
  onTabChange: (t: Tab) => void
}) {
  const navigate    = useNavigate()
  const title       = getTitle(item.filename)
  const year        = getYear(item.filename)
  const student     = getStudentName(item.filename)
  const college     = getCollegeLabel(item.department)
  const { bg, color } = getCollegeBadgeColors(college)
  const questions   = item.summary!.questions!
  const intro       = item.summary!.intro

  const [scores, setScores] = useState<(number | null)[]>(questions.map(() => null))
  const [submitted, setSubmitted] = useState(false)

  const allAnswered = scores.every(s => s !== null)
  const avg = allAnswered
    ? (scores.reduce((a, b) => a + (b ?? 0), 0) / scores.length)
    : 0

  const resultText = avg >= 4
    ? '你對這個研究主題有較高的興趣！這個方向很值得深入探索。'
    : avg >= 2.5
    ? '你對這個研究主題有一定程度的興趣，可以進一步了解看看。'
    : '這個研究主題目前可能不是你最感興趣的方向，可以繼續探索其他計畫。'

  const barColor = avg >= 4 ? 'var(--primary-700, #334e68)'
    : avg >= 2.5 ? '#78909c' : '#94a3b8'

  return (
    <div className="ap-quiz">
      {/* Header */}
      <div className="ap-quiz-header">
        <span className="ap-college-badge" style={{ background: bg, color }}>{college}</span>
        {year && <span className="ap-quiz-year">{year}</span>}
        {student && <span className="ap-quiz-student"><User size={12} />{student}</span>}
        <span className="ap-quiz-dept"><GraduationCap size={12} />{item.department}</span>
      </div>
      <h2 className="ap-quiz-title">{title}</h2>

      {/* 導讀：永遠顯示 */}
      {intro && (
        <div className="ap-intro-block">
          <div className="ap-intro-label"><Lightbulb size={13} /> 導讀</div>
          <p className="ap-intro-text">{intro}</p>
        </div>
      )}

      {/* Tab bar：導讀下方 */}
      <TabBar active={tab} onChange={onTabChange} />

      {tab === 'quiz' ? (
        !submitted ? (
          <>
            {/* Questions */}
            <div className="ap-scale-header">
              <h3 className="ap-scale-title">興趣量表</h3>
              <p className="ap-scale-sub">請根據以下題目，評估你對這個研究主題的興趣程度</p>
            </div>

            {questions.map((q, i) => (
              <div key={i} className="ap-question-block">
                <div className="ap-question-head">
                  <span className="ap-question-num">Q{i + 1}</span>
                  <p className="ap-question-text">{q}</p>
                </div>
                <ScoreButtons
                  value={scores[i]}
                  onChange={v => setScores(prev => { const next = [...prev]; next[i] = v; return next })}
                />
              </div>
            ))}

            <button
              className="ap-submit-btn"
              disabled={!allAnswered}
              onClick={() => setSubmitted(true)}
              type="button"
            >
              送出評量
            </button>
          </>
        ) : (
          /* Result */
          <div className="ap-result">
            <div className="ap-result-card">
              <div className="ap-result-card-top">
                <div className="ap-result-check">
                  <CheckCircle size={18} color="#4ade80" />
                  <span className="ap-result-done">評量完成</span>
                </div>
                <div className="ap-result-score-big">
                  {avg.toFixed(1)}<span className="ap-result-score-denom">&thinsp;/ 5</span>
                </div>
              </div>

              <div className="ap-result-bar-wrap">
                <div className="ap-result-bar">
                  <div className="ap-result-bar-fill" style={{ width: `${(avg / 5) * 100}%`, background: barColor }} />
                </div>
                <div className="ap-result-dots">
                  {[1,2,3,4,5].map(n => (
                    <span key={n} className="ap-result-dot-label">{n}</span>
                  ))}
                </div>
              </div>

              <p className="ap-result-text">{resultText}</p>
            </div>
            <div className="ap-result-actions">
              <button className="ap-reset-btn" onClick={() => { setScores(questions.map(() => null)); setSubmitted(false) }} type="button">
                <RotateCcw size={14} /> 重新評量
              </button>
              <button className="ap-goto-btn" onClick={() => navigate('/projects')} type="button">
                前往完整計畫 <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )
      ) : (
        /* 研究分析 tab */
        <div className="ap-analysis-placeholder">
          <Lightbulb size={40} color="#c6d0dd" strokeWidth={1.2} />
          <p className="ap-empty-title">研究分析功能開發中</p>
          <p className="ap-empty-sub">即將支援 AI 分析研究計畫的學術深度、創新性等維度</p>
        </div>
      )}
    </div>
  )
}

// ── Tab bar ────────────────────────────────────────────────────────────────────

type Tab = 'quiz' | 'analysis'

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="ap-tab-bar">
      <button className={`ap-tab${active === 'quiz' ? ' ap-tab--active' : ''}`} onClick={() => onChange('quiz')}>
        興趣量表
      </button>
      <button className={`ap-tab${active === 'analysis' ? ' ap-tab--active' : ''}`} onClick={() => onChange('analysis')}>
        研究分析
      </button>
    </div>
  )
}

// ── Page ───────────────────────────────────────────────────────────────────────

export default function AssessmentPage() {
  const [selected,   setSelected]   = useState<SummaryItem | null>(null)
  const [search,     setSearch]     = useState('')
  const [filterYear, setFilterYear] = useState('')
  const [filterDept, setFilterDept] = useState('')
  const [tab,        setTab]        = useState<Tab>('quiz')
  const [mobilePane, setMobilePane] = useState<'list' | 'detail'>('list')

  const { data: items = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn:  () => getSummaries(true),
    staleTime: 60_000,
  })

  const withStep3 = useMemo(
    () => items.filter(i => (i.summary?.questions?.length ?? 0) > 0 && i.summary?.intro),
    [items]
  )

  const years = useMemo(() => {
    const set = new Set(withStep3.map(i => getYear(i.filename)).filter(Boolean))
    return Array.from(set).sort((a, b) => b.localeCompare(a))
  }, [withStep3])

  const departments = useMemo(() => {
    const set = new Set(withStep3.map(i => i.department))
    return Array.from(set).sort()
  }, [withStep3])

  const filtered = useMemo(() => {
    return withStep3.filter(item => {
      if (filterYear && getYear(item.filename) !== filterYear) return false
      if (filterDept && item.department !== filterDept) return false
      if (!search.trim()) return true
      const q = search.toLowerCase()
      return (
        getTitle(item.filename).toLowerCase().includes(q) ||
        getStudentName(item.filename).toLowerCase().includes(q) ||
        item.department.toLowerCase().includes(q)
      )
    })
  }, [withStep3, search, filterYear, filterDept])

  const handleSelect = (item: SummaryItem) => {
    setSelected(item)
    setTab('quiz')
    setMobilePane('detail')
  }

  return (
    <div className="ap-page">
      <div className="ap-inner">
        {/* Top filter bar（同 Projects 結構） */}
        <div className="ap-topbar">
          <div className="ap-topbar-left">
            <span className="ap-topbar-title">適性探索</span>
            <span className="ap-topbar-sep">/</span>
            <span className="ap-topbar-sub">選擇研究計畫，體驗 AI 生成的興趣量表。</span>
          </div>
          <div className="ap-topbar-right">
            <div className="ap-search-wrap">
              <Search size={12} className="ap-search-icon" />
              <input
                className="ap-search"
                placeholder="搜尋計畫、姓名…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <select className="ap-filter-select" value={filterYear} onChange={e => setFilterYear(e.target.value)}>
              <option value="">全部年份</option>
              {years.map(y => <option key={y} value={y}>{y} 年</option>)}
            </select>
            <select className="ap-filter-select" value={filterDept} onChange={e => setFilterDept(e.target.value)}>
              <option value="">全部系所</option>
              {departments.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <span className="ap-count">共 <strong>{filtered.length}</strong> 份有題目</span>
          </div>
        </div>

        {/* 1:2 grid（同 Projects） */}
        <div className={`ap-grid ap-grid--${mobilePane}`}>
          {/* Left: card list */}
          <div className="ap-left">
            <div className="ap-doc-list">
              {filtered.map(item => (
                <DocCard
                  key={item.id}
                  item={item}
                  active={selected?.id === item.id}
                  onClick={() => handleSelect(item)}
                />
              ))}
              {filtered.length === 0 && (
                <div className="ap-list-empty">沒有符合的計畫</div>
              )}
            </div>
          </div>

      {/* Right panel */}
          <div className="ap-right">
            {selected ? (
              <>
                <div className="ap-right-top">
                  <button className="ap-mobile-back" onClick={() => setMobilePane('list')}>
                    <ArrowLeft size={15} /> 返回計畫列表
                  </button>
                </div>
                <div className="ap-right-scroll">
                  <QuizPanel
                    key={selected.id}
                    item={selected}
                    onNavigate={() => {}}
                    onBack={() => setMobilePane('list')}
                    tab={tab}
                    onTabChange={setTab}
                  />
                </div>
              </>
            ) : (
              <div className="ap-empty-state">
                <Lightbulb size={42} color="#c6d0dd" strokeWidth={1.2} />
                <div className="ap-empty-title">請從左側選擇一份計畫</div>
                <div className="ap-empty-sub">選擇後即可進行興趣量表評量</div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
