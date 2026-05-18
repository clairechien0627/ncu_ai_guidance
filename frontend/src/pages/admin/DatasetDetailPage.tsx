import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Trash2, Plus } from 'lucide-react'
import { getDatasetDetail, createDatasetItem, deleteDatasetItem, type DatasetItemData, type DatasetData } from '../../api'
import { PageHeader } from '../../components/admin/PageHeader'

function fmtDate(iso: string | null | undefined) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

function inputSummary(input: unknown): string {
  if (input == null) return '—'
  if (typeof input === 'string') return input.slice(0, 80)
  try {
    const s = JSON.stringify(input)
    return s.slice(0, 80) + (s.length > 80 ? '…' : '')
  } catch { return '—' }
}

function AddItemModal({ datasetId, onClose, onDone }: { datasetId: string; onClose: () => void; onDone: () => void }) {
  const [inputJson, setInputJson] = useState('')
  const [expectedJson, setExpectedJson] = useState('')
  const [tags, setTags] = useState('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const handleAdd = async () => {
    setLoading(true); setErr(null)
    try {
      let input: unknown = inputJson.trim() || undefined
      if (typeof input === 'string') { try { input = JSON.parse(input) } catch {} }
      let expected: unknown = expectedJson.trim() || undefined
      if (typeof expected === 'string') { try { expected = JSON.parse(expected) } catch {} }
      const tagArr = tags.trim() ? tags.split(',').map(t => t.trim()).filter(Boolean) : undefined
      await createDatasetItem(datasetId, { input, expected_output: expected, tags: tagArr })
      onDone()
    } catch (e: any) { setErr(e?.response?.data?.detail ?? '新增失敗'); setLoading(false) }
  }

  return (
    <div className="adm-modal-backdrop" onClick={onClose}>
      <div className="adm-modal" onClick={e => e.stopPropagation()}>
        <div className="adm-modal-header">
          <span className="adm-modal-title">Add Item</span>
          <button className="adm-btn-icon" onClick={onClose}>✕</button>
        </div>
        <div className="adm-modal-body">
          <div className="adm-form-group">
            <label className="adm-form-label">Input (JSON 或文字)</label>
            <textarea className="adm-form-textarea" rows={4} placeholder='{"messages": [...]}' value={inputJson} onChange={e => setInputJson(e.target.value)} />
          </div>
          <div className="adm-form-group">
            <label className="adm-form-label">Expected Output (選填)</label>
            <textarea className="adm-form-textarea" rows={3} value={expectedJson} onChange={e => setExpectedJson(e.target.value)} />
          </div>
          <div className="adm-form-group">
            <label className="adm-form-label">Tags（逗號分隔，選填）</label>
            <input className="adm-form-input" placeholder="hard, manual, edge-case" value={tags} onChange={e => setTags(e.target.value)} />
          </div>
          {err && <div style={{ fontSize: 12, color: 'var(--adm-red)', marginTop: 4 }}>{err}</div>}
        </div>
        <div className="adm-modal-footer">
          <button className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
          <button className="adm-btn adm-btn-primary" disabled={loading} onClick={handleAdd}>
            {loading ? '新增中…' : '新增'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function DatasetDetailPage() {
  const { datasetId } = useParams<{ datasetId: string }>()
  const qc = useQueryClient()
  const [showAddModal, setShowAddModal] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const { data: dataset, isLoading } = useQuery({
    queryKey: ['dataset-detail', datasetId],
    queryFn: () => getDatasetDetail(datasetId!),
    enabled: !!datasetId,
  })

  if (isLoading) return <div style={{ padding: 32, color: 'var(--adm-text-3)' }}>載入中…</div>
  if (!dataset) return <div style={{ padding: 32, color: 'var(--adm-red)' }}>找不到此 Dataset</div>

  const items: DatasetItemData[] = dataset.items ?? []

  const handleDelete = async (itemId: string) => {
    await deleteDatasetItem(datasetId!, itemId)
    setConfirmDeleteId(null)
    qc.invalidateQueries({ queryKey: ['dataset-detail', datasetId] })
  }

  const handleExport = () => {
    const blob = new Blob([JSON.stringify(items, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `${dataset.name}.json`; a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div>
      <PageHeader
        title={dataset.name}
        crumbs={[{ label: 'Datasets', to: '/admin/datasets' }, { label: dataset.name }]}
        subtitle={`${items.length} items${dataset.description ? '  ·  ' + dataset.description : ''}`}
        actions={
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="adm-btn adm-btn-secondary adm-btn-sm" onClick={handleExport}><Download size={13} /> 匯出 JSON</button>
            <button className="adm-btn adm-btn-primary adm-btn-sm" onClick={() => setShowAddModal(true)}><Plus size={13} /> Add Item</button>
          </div>
        }
      />

      <div className="adm-table-wrap">
        <table className="adm-table">
          <thead>
            <tr>
              <th style={{ width: 32 }}>#</th>
              <th>Input 摘要</th>
              <th>Expected Output</th>
              <th style={{ width: 120 }}>Source Trace</th>
              <th style={{ width: 120 }}>Tags</th>
              <th className="adm-col-time">建立時間</th>
              <th className="adm-col-action">動作</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 ? (
              <tr><td colSpan={7} className="adm-table-empty">尚無 items — 按「Add Item」或從 Traces 加入</td></tr>
            ) : items.map((item, idx) => (
              <>
                <tr key={item.dataset_item_id} className="adm-row--clickable"
                  onClick={() => setExpandedId(expandedId === item.dataset_item_id ? null : item.dataset_item_id)}>
                  <td style={{ color: 'var(--adm-text-3)', fontSize: 11 }}>{idx + 1}</td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{inputSummary(item.input)}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>{inputSummary(item.expected_output)}</td>
                  <td className="adm-cell-mono">{item.source_trace_id ? item.source_trace_id.slice(-10) : '—'}</td>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-2)' }}>{(item.tags as string[] | null)?.join(', ') ?? '—'}</td>
                  <td className="adm-cell-mono">{fmtDate(item.created_at)}</td>
                  <td>
                    <div className="adm-action-row">
                      {confirmDeleteId === item.dataset_item_id ? (
                        <>
                          <button className="adm-btn adm-btn-danger adm-btn-sm" onClick={e => { e.stopPropagation(); handleDelete(item.dataset_item_id) }}>確認刪除</button>
                          <button className="adm-btn adm-btn-ghost adm-btn-sm" onClick={e => { e.stopPropagation(); setConfirmDeleteId(null) }}>取消</button>
                        </>
                      ) : (
                        <button className="adm-btn-icon" onClick={e => { e.stopPropagation(); setConfirmDeleteId(item.dataset_item_id) }} title="刪除">
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {expandedId === item.dataset_item_id && (
                  <tr key={`${item.dataset_item_id}-expand`} className="adm-expand-row">
                    <td colSpan={7}>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                        <div>
                          <div className="adm-form-label" style={{ marginBottom: 6 }}>Input</div>
                          <pre style={{ fontSize: 11, background: 'var(--adm-bg)', padding: '8px 10px', borderRadius: 6, overflow: 'auto', maxHeight: 200, margin: 0 }}>
                            {JSON.stringify(item.input, null, 2)}
                          </pre>
                        </div>
                        <div>
                          <div className="adm-form-label" style={{ marginBottom: 6 }}>Expected Output</div>
                          <pre style={{ fontSize: 11, background: 'var(--adm-bg)', padding: '8px 10px', borderRadius: 6, overflow: 'auto', maxHeight: 200, margin: 0 }}>
                            {JSON.stringify(item.expected_output, null, 2)}
                          </pre>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {showAddModal && (
        <AddItemModal datasetId={datasetId!} onClose={() => setShowAddModal(false)}
          onDone={() => { setShowAddModal(false); qc.invalidateQueries({ queryKey: ['dataset-detail', datasetId] }) }} />
      )}
    </div>
  )
}
