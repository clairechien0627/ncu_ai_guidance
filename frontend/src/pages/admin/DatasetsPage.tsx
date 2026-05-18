import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Database } from 'lucide-react'
import { getDatasets, createDataset, type DatasetData } from '../../api'
import { useLinkedColumnResize, type ColDef } from '../../hooks/useLinkedColumnResize'
import { useTableSort } from '../../hooks/useTableSort'
import { TablePagination } from '../../components/admin/TablePagination'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function NewDatasetModal({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleCreate = async () => {
    if (!name.trim()) return
    setLoading(true); setErr(null)
    try { await createDataset({ name: name.trim(), description: desc.trim() || undefined }); onDone() }
    catch (e: any) { setErr(e?.response?.data?.detail ?? '建立失敗'); setLoading(false) }
  }

  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" style={{ width: 400 }} onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">New Dataset</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <div className="adm-form-group">
            <label className="adm-form-label">名稱 *</label>
            <input className="adm-form-input" placeholder="e.g. research-qa" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div className="adm-form-group">
            <label className="adm-form-label">描述</label>
            <input className="adm-form-input" placeholder="選填" value={desc} onChange={e => setDesc(e.target.value)} />
          </div>
          {err && <div style={{ fontSize: 12, color: 'var(--adm-red)' }}>{err}</div>}
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-primary" disabled={!name.trim() || loading} onClick={handleCreate}>
            {loading ? '建立中…' : '建立'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Column layout ─────────────────────────────────────────────────────────────
const COLS: ColDef[] = [
  { width: 160, resizable: true  },  // 0 Name
  { width: 200, resizable: true  },  // 1 Description
  { width: 70,  resizable: false },  // 2 Items
  { width: 100, resizable: false },  // 3 Source
  { width: 120, resizable: false },  // 4 建立時間
  { width: 80,  resizable: false },  // 5 動作
]

export default function DatasetsPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [showModal, setShowModal] = useState(false)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(25)
  const { widths, div } = useLinkedColumnResize(COLS, 'adm-datasets-col-widths')
  const sort = useTableSort<DatasetData>({
    item_count: d => d.item_count,
    created_at: d => d.created_at,
  })

  const { data, isLoading } = useQuery({ queryKey: ['datasets-list'], queryFn: getDatasets })
  const datasets: DatasetData[] = data?.datasets ?? []

  const handleDone = () => {
    setShowModal(false)
    qc.invalidateQueries({ queryKey: ['datasets-list'] })
  }

  return (
    <div>
      <PageHeader
        title="Datasets"
        subtitle="管理回歸測試資料集"
        actions={
          <button className="adm-btn adm-btn-primary" onClick={() => setShowModal(true)}>
            <Database size={14} /> + New Dataset
          </button>
        }
      />

      <div className="adm-table-wrap">
        <table className="adm-table" style={{ tableLayout: 'fixed' }}>
          <colgroup>{widths.map((w, i) => <col key={i} style={{ width: w }} />)}</colgroup>
          <thead>
            <tr>
              <th>Name<span {...div(0)} /></th>
              <th>Description<span {...div(1)} /></th>
              <th {...sort.th('item_count')}>Items{sort.ind('item_count')}<span {...div(2)} /></th>
              <th>Source<span {...div(3)} /></th>
              <th {...sort.th('created_at')}>建立時間{sort.ind('created_at')}<span {...div(4)} /></th>
              <th>動作</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="adm-table-empty"><div className="adm-spinner" style={{ margin: '0 auto' }} /></td></tr>
            ) : datasets.length === 0 ? (
              <tr><td colSpan={6} className="adm-table-empty">尚無 dataset — 按「+ New Dataset」建立</td></tr>
            ) : sort.apply(datasets).slice((page-1)*pageSize, page*pageSize).map(d => (
              <tr key={d.dataset_id} className="adm-row--clickable" onClick={() => navigate(`/admin/datasets/${d.dataset_id}`)}>
                <td style={{ fontWeight: 500 }}>{d.name}</td>
                <td style={{ color: 'var(--adm-text-2)', fontSize: 12 }}>{d.description ?? '—'}</td>
                <td style={{ fontVariantNumeric: 'tabular-nums', fontSize: 12 }}>{d.item_count ?? '—'}</td>
                <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{d.source ?? '—'}</td>
                <td className="adm-cell-mono">{fmtDate(d.created_at)}</td>
                <td>
                  <div className="adm-action-row">
                    <button className="adm-btn adm-btn-secondary adm-btn-sm"
                      onClick={e => { e.stopPropagation(); navigate(`/admin/datasets/${d.dataset_id}`) }}>
                      管理
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <TablePagination
        page={page} pageCount={Math.max(1, Math.ceil(datasets.length / pageSize))}
        pageSize={pageSize} total={datasets.length}
        onPageChange={setPage} onPageSizeChange={n => { setPageSize(n); setPage(1) }}
      />
      {showModal && <NewDatasetModal onClose={() => setShowModal(false)} onDone={handleDone} />}
    </div>
  )
}
