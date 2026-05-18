import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getPromptList2, type PromptSummary } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { ScoreBar } from '../../components/admin/ScoreBar'

function fmtDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 200, resizable: true  },  // 0 Name
  { width: 100, resizable: false },  // 1 Version
  { width: 70,  resizable: false },  // 2 Words
  { width: 70,  resizable: false },  // 3 Versions
  { width: 120, resizable: false },  // 4 Last Synced
  { width: 130, resizable: false },  // 5 Avg Quality
]

export default function PromptsPage() {
  const navigate = useNavigate()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-prompts-col-widths')
  const sort = useTableSort<PromptSummary>({
    word_count:        p => p.word_count,
    version_count:     p => p.version_count,
    synced_at:         p => p.synced_at,
    avg_quality_score: p => p.avg_quality_score,
  })
  const { data, isLoading } = useQuery({ queryKey: ['prompts-list'], queryFn: getPromptList2 })
  const prompts: PromptSummary[] = Array.isArray(data) ? data : []

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{prompts.length} prompts</span>
      </div>

      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th>Name<span {...div(0)} /></th>
              <th>Version<span {...div(1)} /></th>
              <th {...sort.th('word_count')}>Words{sort.ind('word_count')}<span {...div(2)} /></th>
              <th {...sort.th('version_count')}>Versions{sort.ind('version_count')}<span {...div(3)} /></th>
              <th {...sort.th('synced_at')}>Last Synced{sort.ind('synced_at')}<span {...div(4)} /></th>
              <th {...sort.th('avg_quality_score')}>Avg Quality{sort.ind('avg_quality_score')}</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : prompts.length === 0 ? (
              <tr><td colSpan={6} className="adm-table-empty">No prompts. Run <code>python scripts/prompts/sync_prompts.py</code> to sync.</td></tr>
            ) : sort.apply(prompts).slice((page-1)*pageSize, page*pageSize).map((p: PromptSummary) => (
              <tr key={p.name} className="adm-row--clickable" onClick={() => navigate(`/admin/prompts/${encodeURIComponent(p.name)}`)}>
                <td style={{ fontWeight: 500 }}>{p.name}</td>
                <td className="adm-cell-mono">{p.current_hash ? `#${p.current_hash.slice(0, 7)}` : '—'}</td>
                <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{p.word_count ?? '—'}</td>
                <td>
                  {p.version_count > 0 ? <span className="adm-badge adm-badge--info">{p.version_count}</span> : '—'}
                </td>
                <td className="adm-cell-mono">{fmtDate(p.synced_at)}</td>
                <td><ScoreBar value={p.avg_quality_score} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePagination
        page={page} pageCount={Math.max(1, Math.ceil(prompts.length / pageSize))}
        pageSize={pageSize} total={prompts.length}
        onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
      />
    </div>
  )
}
