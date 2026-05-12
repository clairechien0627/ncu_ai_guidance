import { Suspense, lazy, useState } from 'react'
import { GraduationCap } from 'lucide-react'

const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'))

interface Summary {
  motivation: string
  method: string
  results: string
  tags: string[]
  intro?: string
  questions?: string[]
}

interface Props {
  title: string
  department: string
  summary: Summary
}

const QUESTION_LABELS = ['情境想像', '實作技術', '學科本質']

export default function ResearchCard({ title, department, summary }: Props) {
  const [ratings, setRatings] = useState<(number | null)[]>([null, null, null])

  const rate = (qi: number, val: number) =>
    setRatings(prev => prev.map((r, i) => i === qi ? (r === val ? null : val) : r))

  return (
    <div style={{
      background: '#fff',
      borderRadius: 16,
      border: '1px solid #e8edf3',
      boxShadow: '0 2px 12px rgba(0,0,0,0.06)',
      padding: '24px 28px',
      display: 'flex',
      flexDirection: 'column',
      gap: 0,
    }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 20 }}>
        <GraduationCap size={22} color="#3b5bdb" strokeWidth={1.8} style={{ flexShrink: 0, marginTop: 2 }} />
        <span style={{ fontSize: 16, fontWeight: 700, color: '#1e293b', lineHeight: 1.5 }}>{title}</span>
      </div>

      {/* Intro */}
      {summary.intro && (
        <div style={{ marginBottom: 18 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#334155', marginBottom: 6 }}>一分鐘看研究</div>
          <Suspense fallback={<p style={{ fontSize: 13, color: '#475569', margin: 0 }}>{summary.intro}</p>}>
            <MarkdownRenderer content={summary.intro} />
          </Suspense>
        </div>
      )}

      {/* Motivation */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#334155', marginBottom: 5 }}>研究動機與問題</div>
        <Suspense fallback={<p style={{ fontSize: 13, color: '#475569', margin: 0 }}>{summary.motivation}</p>}>
          <MarkdownRenderer content={summary.motivation} />
        </Suspense>
      </div>

      {/* Method */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#334155', marginBottom: 5 }}>研究方法</div>
        <Suspense fallback={<p style={{ fontSize: 13, color: '#475569', margin: 0 }}>{summary.method}</p>}>
          <MarkdownRenderer content={summary.method} />
        </Suspense>
      </div>

      {/* Results */}
      <div style={{ marginBottom: 18 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#334155', marginBottom: 5 }}>研究成果</div>
        <Suspense fallback={<p style={{ fontSize: 13, color: '#475569', margin: 0 }}>{summary.results}</p>}>
          <MarkdownRenderer content={summary.results} />
        </Suspense>
      </div>

      {/* Tags */}
      {summary.tags?.length > 0 && (
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#334155', marginBottom: 8 }}>領域標籤</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {summary.tags.map(tag => (
              <span key={tag} style={{
                padding: '4px 12px', borderRadius: 999,
                background: '#f1f5f9', color: '#475569',
                fontSize: 12, fontWeight: 500,
              }}>{tag}</span>
            ))}
          </div>
        </div>
      )}

      {/* Divider + Questions */}
      {(summary.questions ?? []).length > 0 && (
        <>
          <div style={{ borderTop: '1px solid #e8edf3', margin: '4px 0 22px' }} />
          <div style={{ fontSize: 16, fontWeight: 700, color: '#1e293b', marginBottom: 20 }}>
            這個研究主題讓你感興趣嗎？
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
            {(summary.questions ?? []).map((q, qi) => (
              <div key={qi}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                  <span style={{
                    fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 6,
                    background: '#eff6ff', color: '#3b5bdb',
                  }}>{QUESTION_LABELS[qi] ?? `題 ${qi + 1}`}</span>
                </div>
                <p style={{ fontSize: 13, color: '#334155', lineHeight: 1.7, margin: '0 0 12px' }}>{q}</p>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#94a3b8', marginBottom: 6 }}>
                  <span>1 分・完全不感興趣</span>
                  <span>5 分・非常感興趣</span>
                </div>
                <div style={{ display: 'flex', gap: 6 }}>
                  {[1, 2, 3, 4, 5].map(v => (
                    <button
                      key={v}
                      onClick={() => rate(qi, v)}
                      style={{
                        flex: 1, padding: '12px 0', borderRadius: 10, border: 'none',
                        cursor: 'pointer', fontSize: 16, fontWeight: 700,
                        background: ratings[qi] === v ? '#3b5bdb' : '#f1f5f9',
                        color: ratings[qi] === v ? '#fff' : '#475569',
                        transition: 'background 0.15s, color 0.15s',
                      }}
                    >{v}</button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
