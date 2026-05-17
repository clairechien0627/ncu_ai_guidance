import { useEffect, useRef, useState, useCallback } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { BookOpen, MessageSquare, Lightbulb, ArrowRight, ChevronLeft, ChevronRight } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getSummaries } from '../api'
import './HomePage.css'

// ── CountUp ───────────────────────────────────────────────────────────────────

function CountUp({ target, duration = 1200 }: { target: number; duration?: number }) {
  const [value, setValue] = useState(0)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    if (target === 0) return
    const start = performance.now()
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1)
      const ease = 1 - Math.pow(1 - t, 3)
      setValue(Math.round(ease * target))
      if (t < 1) rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current) }
  }, [target, duration])

  return <>{value.toLocaleString()}</>
}

// ── Coverflow ─────────────────────────────────────────────────────────────────

const FEATURES = [
  {
    path: '/projects',
    icon: <BookOpen size={22} color="#334e68" />,
    title: '計畫瀏覽',
    desc: '瀏覽歷年大專生研究計畫摘要，快速掌握研究動機、方法與成果，支援標籤與系所篩選。',
    tag: '資料瀏覽',
  },
  {
    path: '/chat',
    icon: <MessageSquare size={22} color="#334e68" />,
    title: '問答助理',
    desc: '上傳 PDF 報告，透過多智能體 AI 深度問答分析研究內容，支援 RAG 精確檢索。',
    tag: 'AI 問答',
  },
  {
    path: '/assessment',
    icon: <Lightbulb size={22} color="#334e68" />,
    title: '適性探索',
    desc: '瀏覽由 AI 為各研究計畫生成的導讀題目，探索研究興趣，題目分析功能持續開發中。',
    tag: '興趣探索',
  },
]

function FeatureCoverflow() {
  const [active, setActive] = useState(0)
  const navigate = useNavigate()
  const dragRef = useRef({ isDown: false, startX: 0, moved: false })
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const n = FEATURES.length

  const startAuto = useCallback(() => {
    timerRef.current = setInterval(() => {
      if (!dragRef.current.isDown) setActive(p => (p + 1) % n)
    }, 4500)
  }, [n])

  useEffect(() => {
    startAuto()
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [startAuto])

  const goTo = (i: number) => {
    if (timerRef.current) clearInterval(timerRef.current)
    setActive(((i % n) + n) % n)
    startAuto()
  }

  const onPointerDown = (e: React.PointerEvent) => {
    dragRef.current = { isDown: true, startX: e.clientX, moved: false }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    if (!dragRef.current.isDown) return
    if (Math.abs(e.clientX - dragRef.current.startX) > 20) dragRef.current.moved = true
  }
  const onPointerUp = (e: React.PointerEvent) => {
    if (!dragRef.current.isDown) return
    const dx = e.clientX - dragRef.current.startX
    if (dragRef.current.moved) {
      if (dx > 40) goTo(active - 1)
      else if (dx < -40) goTo(active + 1)
    }
    dragRef.current = { isDown: false, startX: 0, moved: false }
  }

  const posClass = (i: number) => {
    const offset = ((i - active) % n + n) % n
    const wrapped = offset > n / 2 ? offset - n : offset
    if (wrapped === 0) return 'home-coverflow-card--center'
    if (wrapped === -1) return 'home-coverflow-card--left'
    if (wrapped === 1) return 'home-coverflow-card--right'
    return 'home-coverflow-card--hidden'
  }

  return (
    <>
      <div
        className="home-coverflow"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <div className="home-coverflow-stage">
          <button
            className="home-coverflow-nav home-coverflow-nav--left"
            onPointerDown={e => e.stopPropagation()}
            onClick={() => goTo(active - 1)}
            aria-label="上一張"
          >
            <ChevronLeft size={18} />
          </button>
          <button
            className="home-coverflow-nav home-coverflow-nav--right"
            onPointerDown={e => e.stopPropagation()}
            onClick={() => goTo(active + 1)}
            aria-label="下一張"
          >
            <ChevronRight size={18} />
          </button>

          {FEATURES.map((f, i) => {
            const cls = posClass(i)
            const isCenter = cls === 'home-coverflow-card--center'
            return (
              <button
                key={f.path}
                className={`home-coverflow-card ${cls}`}
                onClick={() => isCenter ? navigate(f.path) : goTo(i)}
                style={{ cursor: isCenter ? 'pointer' : 'default' }}
              >
                <div className="home-coverflow-inner">
                  <div className="home-coverflow-icon">{f.icon}</div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 8 }}>
                    <span className="home-coverflow-title">{f.title}</span>
                    <span style={{ fontSize: 11, fontWeight: 600, background: '#f1f5f9', color: '#475569', padding: '2px 10px', borderRadius: 999, flexShrink: 0 }}>{f.tag}</span>
                  </div>
                  <p className="home-coverflow-desc">{f.desc}</p>
                  {isCenter && (
                    <span className="home-coverflow-link">
                      前往功能 <ArrowRight size={13} />
                    </span>
                  )}
                </div>
              </button>
            )
          })}
        </div>
      </div>

      <div className="home-coverflow-dots">
        {FEATURES.map((_, i) => (
          <button
            key={i}
            className={`home-coverflow-dot${i === active ? ' home-coverflow-dot--active' : ''}`}
            onClick={() => goTo(i)}
            aria-label={`Go to feature ${i + 1}`}
          />
        ))}
      </div>
    </>
  )
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function HomePage() {
  const { data: items = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })

  const totalDocs      = items.length
  const summarized     = items.filter(i => i.summary).length
  const totalQuestions = items.reduce((acc, i) => acc + (i.summary?.questions?.length ?? 0), 0)

  return (
    <div className="home-root">
      <div className="home-container">
        {/* Hero */}
        <section className="home-hero-section">
          <div className="home-badge">
            <BookOpen size={14} />
            大專生研究計畫平台
          </div>
          <h1 className="home-hero-title">
            用更清楚的資訊，<br />
            找到更適合的研究方向。
          </h1>
          <p className="home-hero-sub">
            結合 RAG 與多智能體技術，讓你快速理解、分析與探索大專生研究計畫，
            從摘要瀏覽到 AI 深度問答一站完成。
          </p>
          <div className="home-hero-btns">
            <Link to="/projects" className="home-btn-primary">
              開始瀏覽 <ArrowRight size={16} />
            </Link>
            <Link to="/chat" className="home-btn-secondary">
              AI 問答助理
            </Link>
          </div>
        </section>

        {/* Feature Coverflow */}
        <section className="home-features-section">
          <p className="home-section-label">Core Modules</p>
          <h2 className="home-section-title">平台功能</h2>
          <p className="home-section-sub">
            拖曳或點擊切換卡片，點擊中間卡片即可前往對應功能。
          </p>
          <FeatureCoverflow />
        </section>

        {/* Stats */}
        <section className="home-stats-section">
          <div className="home-stats-card">
            <div className="home-stats-header">
              <p className="home-section-label">Platform Data</p>
              <h2 className="home-section-title" style={{ marginBottom: 0 }}>平台數據</h2>
            </div>
            <div className="home-stats-grid">
              {[
                { value: totalDocs,      label: '研究計畫',   desc: '收錄大專生研究計畫文件' },
                { value: summarized,     label: '已提取摘要', desc: '動機、方法、成果完整分析' },
                { value: totalQuestions, label: '導讀題目',   desc: 'AI 自動生成的探索題目' },
              ].map(({ value, label, desc }) => (
                <div key={label} className="home-stat-inner">
                  <div className="home-stat-value"><CountUp target={value} /></div>
                  <div className="home-stat-label">{label}</div>
                  <div className="home-stat-desc">{desc}</div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
