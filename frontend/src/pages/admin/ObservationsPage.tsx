import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getObservations, getObservationStats, type ObservationItem } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { DrawerPanel } from '../../components/admin/DrawerPanel'
import { TraceDetailContent } from '../../components/admin/TraceDetailContent'
import { AlertCircle } from 'lucide-react'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
function fmtTokens(p: number | null, c: number | null) {
  if (p == null && c == null) return '—'
  return ((p ?? 0) + (c ?? 0)).toLocaleString()
}

const TYPE_OPTIONS = ['all', 'llm', 'tool', 'chain', 'retriever']

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 120, resizable: false },  // 0 Time
  { width: 80,  resizable: false },  // 1 Type
  { width: 140, resizable: true  },  // 2 Name
  { width: 140, resizable: true  },  // 3 Input
  { width: 160, resizable: true  },  // 4 Output/Error
  { width: 80,  resizable: false },  // 5 Tokens
  { width: 80,  resizable: false },  // 6 Latency
  { width: 100, resizable: false },  // 7 Status
  { width: 90,  resizable: false },  // 8 Trace →
]

export default function ObservationsPage() {
  const [runType, setRunType] = useState('all')
  const [drawerTraceId, setDrawerTraceId] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-observations-col-widths')
  const sort = useTableSort<ObservationItem>({
    start_time: o => o.start_time,
    tokens:     o => (o.prompt_tokens ?? 0) + (o.completion_tokens ?? 0),
    latency:    o => o.latency,
  })

  const { data: stats } = useQuery({ queryKey: ['obs-stats'], queryFn: getObservationStats })
  const { data: rows = [], isLoading } = useQuery({
    queryKey: ['observations', runType],
    queryFn: () => getObservations(200, runType),
  })

  const byType = stats?.by_type ?? {}

  return (
    <div>
      {/* Type filter */}
      <div className="adm-filters" style={{ marginBottom: 12 }}>
        {TYPE_OPTIONS.map(t => (
          <button key={t}
            className={`adm-btn adm-btn-sm ${runType === t ? 'adm-btn-primary' : 'adm-btn-secondary'}`}
            onClick={() => setRunType(t)}
          >
            {t === 'all' ? 'All' : t.toUpperCase()}
            {t !== 'all' && byType[t] ? ` (${byType[t].count})` : ''}
          </button>
        ))}
        <span className="adm-filter-spacer" />
        <span style={{ fontSize: 12, color: 'var(--adm-text-3)' }}>{rows.length} rows</span>
      </div>

      {/* Table */}
      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th {...sort.th('start_time')}>Time{sort.ind('start_time')}<span {...div(0)} /></th>
              <th>Type<span {...div(1)} /></th>
              <th>Name<span {...div(2)} /></th>
              <th>Input<span {...div(3)} /></th>
              <th>Output / Error<span {...div(4)} /></th>
              <th {...sort.th('tokens')}>Tokens{sort.ind('tokens')}<span {...div(5)} /></th>
              <th {...sort.th('latency')}>Latency{sort.ind('latency')}<span {...div(6)} /></th>
              <th>Status<span {...div(7)} /></th>
              <th>Trace →</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={9} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={9} className="adm-table-empty">No observations</td></tr>
            ) : sort.apply(rows).slice((page-1)*pageSize, page*pageSize).map((row: ObservationItem) => {
              const isErr = !!row.error
              return (
                <tr key={row.id} className={isErr ? 'adm-row--danger' : ''}>
                  <td className="adm-cell-mono">{fmtDate(row.start_time)}</td>
                  <td><span className="adm-badge adm-badge--info" style={{ textTransform: 'uppercase', fontSize: 10 }}>{row.run_type ?? '?'}</span></td>
                  <td style={{ fontSize: 12, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' }} title={row.name}>{row.name}</td>
                  <td style={{ fontSize: 11, maxWidth: 140, overflow: 'hidden', textOverflow: 'ellipsis', color: 'var(--adm-text-2)' }} title={row.input ?? undefined}>{row.input ?? '—'}</td>
                  <td style={{ fontSize: 11, maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', color: isErr ? 'var(--adm-red)' : 'var(--adm-text-2)' }} title={(row.error ?? row.output) ?? undefined}>
                    {row.error ?? row.output ?? '—'}
                  </td>
                  <td style={{ fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>{fmtTokens(row.prompt_tokens, row.completion_tokens)}</td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{row.latency != null ? `${row.latency}s` : '—'}</td>
                  <td>
                    {isErr
                      ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, fontWeight: 600, color: 'var(--adm-red)' }}>
                          <AlertCircle size={12} /> Error
                        </span>
                      : <span style={{ fontSize: 11, color: 'var(--adm-green)', fontWeight: 500 }}>✓</span>
                    }
                  </td>
                  {/* Trace link — 點擊開側欄，不跳轉 */}
                  <td>
                    {row.parent_run_id
                      ? <button
                          className="adm-cell-link"
                          style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, fontSize: 11, fontFamily: 'var(--adm-font-mono)' }}
                          onClick={() => setDrawerTraceId(row.parent_run_id!)}
                          title={row.parent_run_id}
                        >
                          {row.parent_run_id.slice(-8)} ↗
                        </button>
                      : <span style={{ color: 'var(--adm-text-3)' }}>—</span>
                    }
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <TablePagination
        page={page} pageCount={Math.max(1, Math.ceil(rows.length / pageSize))}
        pageSize={pageSize} total={rows.length}
        onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
      />
      {/* Trace detail drawer */}
      <DrawerPanel
        open={!!drawerTraceId}
        title={drawerTraceId ? `Trace · ${drawerTraceId.slice(-12)}…` : ''}
        onClose={() => setDrawerTraceId(null)}
        width={620}
      >
        {drawerTraceId && <TraceDetailContent traceId={drawerTraceId} compact />}
      </DrawerPanel>
    </div>
  )
}
