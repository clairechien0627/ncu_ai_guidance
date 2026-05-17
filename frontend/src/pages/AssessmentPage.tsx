import { useState, useMemo } from 'react'
import { Search, Lightbulb } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getSummaries, type SummaryItem } from '../api'
import './AssessmentPage.css'

function getTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

export default function AssessmentPage() {
  const [selected, setSelected] = useState<SummaryItem | null>(null)
  const [search, setSearch] = useState('')

  const { data: items = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })

  const withQuestions = useMemo(
    () => items.filter(i => (i.summary?.questions?.length ?? 0) > 0),
    [items]
  )

  const filtered = useMemo(() => {
    if (!search.trim()) return withQuestions
    const q = search.toLowerCase()
    return withQuestions.filter(i =>
      i.filename.toLowerCase().includes(q) ||
      i.summary?.tags?.some(t => t.toLowerCase().includes(q))
    )
  }, [withQuestions, search])

  return (
    <div className="ap-root">
      {/* Left: doc selector */}
      <div className="ap-left">
        <div className="ap-left-head">
          <p className="ap-left-title">選擇計畫</p>
          <div className="ap-search-wrap">
            <Search size={12} className="ap-search-icon" />
            <input
              className="ap-search"
              placeholder="搜尋計畫、標籤…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
          </div>
        </div>

        <div className="ap-doc-list">
          {filtered.map(item => (
            <div
              key={item.id}
              className={`ap-doc-card${selected?.id === item.id ? ' ap-doc-card--active' : ''}`}
              onClick={() => setSelected(item)}
            >
              <div className="ap-doc-title">{getTitle(item.filename)}</div>
              <div className="ap-doc-meta">{item.department}</div>
              <span className="ap-doc-count">{item.summary!.questions!.length} 道題目</span>
            </div>
          ))}
          {filtered.length === 0 && (
            <div style={{ padding: 20, textAlign: 'center', color: '#94a3b8', fontSize: 12 }}>
              沒有符合的計畫
            </div>
          )}
        </div>
        <div className="ap-list-count">{filtered.length} / {withQuestions.length} 份有題目</div>
      </div>

      {/* Right: questions */}
      <div className="ap-right">
        {selected ? (
          <>
            <h1 className="ap-detail-title">{getTitle(selected.filename)}</h1>
            <p className="ap-detail-sub">
              {selected.department}・共 {selected.summary!.questions!.length} 道導讀題目
            </p>

            {selected.summary!.questions!.map((q, i) => (
              <div key={i} className="ap-question-card">
                <span className="ap-question-num">Q{i + 1}</span>
                <span className="ap-question-text">{q}</span>
              </div>
            ))}

            <div className="ap-dev-notice">
              <Lightbulb size={14} />
              題目分析與量表解讀功能開發中，敬請期待。
            </div>
          </>
        ) : (
          <div className="ap-empty-state">
            <Lightbulb size={36} color="#c6d0dd" strokeWidth={1.5} />
            <div className="ap-empty-title">請從左側選擇一份計畫</div>
            <div className="ap-empty-sub">選擇後即可查看 AI 生成的導讀題目</div>
          </div>
        )}
      </div>
    </div>
  )
}
