import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getScoreStats, getTraces, batchScoreTraces, type TraceItem } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { ScoreBar } from '../../components/admin/ScoreBar'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

const DIMS = [
  { key: 'grounding',           label: '證據支撐',     weight: '25%' },
  { key: 'task_fit',            label: '任務符合',     weight: '18%' },
  { key: 'completeness',        label: '完整度',       weight: '17%' },
  { key: 'specificity',         label: '具體性',       weight: '12%' },
  { key: 'source_quality',      label: '來源品質',     weight: '10%' },
  { key: 'uncertainty_honesty', label: '不確定誠實',   weight: '10%' },
  { key: 'format_fit',          label: '格式適切',     weight: '8%' },
]

// ── Column layout (low-quality traces table) ───────────────────────────────────
const COLS: ColDef[] = [
  { width: 120, resizable: false },  // 0 時間
  { width: 120, resizable: true  },  // 1 Intent
  { width: 130, resizable: false },  // 2 Score
  { width: 160, resizable: true  },  // 4 Verdict
  { width: 80,  resizable: false },  // 5 Latency
]

export default function ScoresPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [batchLoading, setBatchLoading] = useState(false)
  const [batchMsg, setBatchMsg] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-scores-col-widths')
  const sort = useTableSort<TraceItem>({
    start_time:    t => t.start_time,
    quality_score: t => t.quality_score,
    latency:       t => t.latency,
  })

  const { data: stats, isLoading } = useQuery({ queryKey: ['score-stats'], queryFn: getScoreStats })
  const { data: lowTraces = [], isLoading: lowLoading } = useQuery({
    queryKey: ['low-quality-traces'],
    queryFn: () => getTraces(100, { max_quality: 3 }),
  })

  const handleBatchScore = async () => {
    setBatchLoading(true); setBatchMsg(null)
    try {
      const res = await batchScoreTraces(50)
      setBatchMsg(res.message)
      setTimeout(() => {
        qc.invalidateQueries({ queryKey: ['score-stats'] })
        qc.invalidateQueries({ queryKey: ['low-quality-traces'] })
      }, 3000)
    } catch { setBatchMsg('批次評分啟動失敗') }
    finally { setBatchLoading(false) }
  }

  if (isLoading) return <div style={{ padding: 32, color: 'var(--adm-text-3)' }}>載入中…</div>

  const dist = stats?.distribution ?? []
  const maxCount = Math.max(...dist.map((d: any) => d.count), 1)
  const dimAvgs: Record<string, number | null> = stats?.dimension_avgs ?? {}

  const bucketColor = (b: string) => b === '0-1' || b === '1-2' ? 'var(--adm-red)' : b === '2-3' ? 'var(--adm-amber)' : 'var(--adm-green)'

  return (
    <div>
      <PageHeader title="Scores" actions={
        <button className="adm-btn adm-btn-secondary adm-btn-sm" onClick={handleBatchScore} disabled={batchLoading || (stats?.unscored ?? 0) === 0}>
          {batchLoading ? '啟動中…' : '▶ Run Batch Score'}
        </button>
      } />
      {batchMsg && <div className="adm-toast-wrap"><div className="adm-toast">{batchMsg}</div></div>}


      {/* Distribution + Dimension */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        <div className="adm-card">
          <div className="adm-card-title">分數分布</div>
          {dist.map((d: any) => (
            <div key={d.bucket} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--adm-text-3)', width: 32, flexShrink: 0 }}>{d.bucket}</span>
              <div style={{ flex: 1, height: 8, background: 'var(--adm-border)', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${(d.count / maxCount) * 100}%`, background: bucketColor(d.bucket), borderRadius: 999, transition: 'width 300ms' }} />
              </div>
              <span style={{ fontSize: 11, color: 'var(--adm-text-2)', width: 24, fontVariantNumeric: 'tabular-nums' }}>{d.count}</span>
            </div>
          ))}
          {dist.length === 0 && <div style={{ color: 'var(--adm-text-3)', fontSize: 12 }}>尚無評分資料</div>}
        </div>
        <div className="adm-card">
          <div className="adm-card-title">7 維度平均</div>
          {DIMS.map(({ key, label, weight }) => (
            <div key={key} className="adm-dim-row" style={{ marginBottom: 8 }}>
              <span style={{ fontSize: 11, color: 'var(--adm-text-3)', width: 80, flexShrink: 0 }}>{label}</span>
              <div style={{ flex: 1 }}><ScoreBar value={dimAvgs[key]} /></div>
              <span style={{ fontSize: 10, color: 'var(--adm-text-3)', width: 28, flexShrink: 0 }}>{weight}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Low quality traces */}
      <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--adm-text)' }}>低品質 Traces（分數 &lt; 3）</span>
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{lowTraces.length} 筆</span>
      </div>
      {lowLoading ? (
        <div style={{ padding: 24, color: 'var(--adm-text-3)' }}>載入中…</div>
      ) : (
        <>
        <div className="adm-table-wrap">
          <table className="adm-table" style={{ tableLayout: 'fixed' }}>
            <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
            <thead>
              <tr>
                <th {...sort.th('start_time')}>時間{sort.ind('start_time')}<span {...div(0)} /></th>
                <th>Intent<span {...div(1)} /></th>
                <th {...sort.th('quality_score')}>Score{sort.ind('quality_score')}<span {...div(2)} /></th>
                <th>Verdict<span {...div(4)} /></th>
                <th {...sort.th('latency')}>Latency{sort.ind('latency')}</th>
              </tr>
            </thead>
            <tbody>
              {lowTraces.length === 0 ? (
                <tr><td colSpan={6} className="adm-table-empty">目前沒有低品質 trace</td></tr>
              ) : sort.apply(lowTraces as TraceItem[]).slice((page-1)*pageSize, page*pageSize).map((t: TraceItem) => (
                <tr key={t.id} className="adm-row--clickable" onClick={() => navigate('/admin/traces')}>
                  <td className="adm-cell-mono">{fmtDate(t.start_time)}</td>
                  <td>{t.original_intent ? <span className="adm-badge adm-badge--info">{t.original_intent}</span> : '—'}</td>
                  <td><ScoreBar value={t.quality_score} /></td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{(t.quality_detail as any)?.verdict ?? (t.user_feedback ? t.user_feedback.slice(0, 60) : '—')}</td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{t.latency != null ? `${t.latency}s` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <TablePagination
          page={page} pageCount={Math.max(1, Math.ceil((lowTraces as TraceItem[]).length / pageSize))}
          pageSize={pageSize} total={(lowTraces as TraceItem[]).length}
          onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
        />
        </>
      )}
    </div>
  )
}
