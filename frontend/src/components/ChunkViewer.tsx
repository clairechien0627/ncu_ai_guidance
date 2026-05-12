import { useEffect, useState } from 'react'
import axios from 'axios'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Pencil, Trash2 } from 'lucide-react'
import { deleteChunk, updateChunk } from '../api'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

interface Chunk {
  id: string
  section: string
  page: number | string
  page_end: number | string
  chunk_index: number | null
  parser: string
  chars: number
  preview: string
  content: string
}

interface ChunkData {
  doc_id: number
  filename: string
  total: number
  chunks: Chunk[]
}

const SECTION_COLORS: Record<string, string> = {
  introduction: '#3b82f6',
  methods: '#8b5cf6',
  results: '#10b981',
  discussion: '#f59e0b',
  conclusion: '#ef4444',
  references: '#64748b',
  background: '#06b6d4',
  related_work: '#ec4899',
  other: '#64748b',
  unknown: '#d1d5db',
}

function sectionColor(s: string) {
  return SECTION_COLORS[s] ?? '#64748b'
}

function parserColor(p: string) {
  if (p === 'llamaparse') return '#d97706'
  if (p === 'azure_di') return '#2563eb'
  return '#4b5563'
}

function parserLabel(p: string) {
  if (p === 'llamaparse') return 'Llama'
  if (p === 'azure_di') return 'Azure'
  if (p === 'pymupdf4llm') return 'local'
  return p
}

export default function ChunkViewer({ docId, onClose }: { docId: number; onClose: () => void }) {
  const [data, setData] = useState<ChunkData | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [deleting, setDeleting] = useState<Set<string>>(new Set())
  const [editing, setEditing] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const handleDelete = async (e: React.MouseEvent, chunkId: string) => {
    e.stopPropagation()
    if (!window.confirm('確定刪除這個 chunk？此操作無法復原。')) return
    setDeleting(prev => new Set(prev).add(chunkId))
    try {
      await deleteChunk(docId, chunkId)
      setData(prev => prev ? { ...prev, chunks: prev.chunks.filter(c => c.id !== chunkId), total: prev.total - 1 } : prev)
      if (expanded === chunkId) setExpanded(null)
      if (editing === chunkId) setEditing(null)
    } catch {
      alert('刪除失敗')
    } finally {
      setDeleting(prev => { const n = new Set(prev); n.delete(chunkId); return n })
    }
  }

  const startEdit = (e: React.MouseEvent, chunk: Chunk) => {
    e.stopPropagation()
    setEditing(chunk.id)
    setEditDraft(chunk.content)
    setExpanded(chunk.id)
  }

  const handleSave = async (chunkId: string) => {
    setSaving(true)
    try {
      await updateChunk(docId, chunkId, editDraft)
      setData(prev => prev ? {
        ...prev,
        chunks: prev.chunks.map(c => c.id === chunkId
          ? { ...c, content: editDraft, preview: editDraft.slice(0, 300), chars: editDraft.length }
          : c),
      } : prev)
      setEditing(null)
    } catch {
      alert('儲存失敗')
    } finally {
      setSaving(false)
    }
  }

  useEffect(() => {
    axios.get(`${API_BASE}/api/documents/${docId}/chunks`).then(r => setData(r.data))
  }, [docId])

  if (!data) return (
    <div style={overlay}>
      <div style={modal}>
        <p style={{ color: '#9ca3af', textAlign: 'center', marginTop: 80 }}>載入中…</p>
      </div>
    </div>
  )

  const sorted = [...data.chunks].sort((a, b) => {
    const ia = a.chunk_index ?? 9999, ib = b.chunk_index ?? 9999
    if (ia !== ib) return ia - ib
    const pa = Number(a.page), pb = Number(b.page)
    return pa - pb
  })
  const sections = [...new Set(sorted.map(c => c.section))]
  const visible = filter
    ? sorted.filter(c => c.section === filter)
    : sorted

  const maxChars = Math.max(...data.chunks.map(c => c.chars), 1)

  return (
    <div style={overlay} onClick={onClose}>
      <div style={modal} onClick={e => e.stopPropagation()}>
        {/* header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14.5, color: '#111827' }}>{data.filename}</div>
            <div style={{ fontSize: 12, color: '#9ca3af', marginTop: 2 }}>
              共 {data.total} 個 chunk
            </div>
          </div>
          <button onClick={onClose} style={closeBtn}>✕</button>
        </div>

        {/* section filter */}
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 14 }}>
          <button
            onClick={() => setFilter('')}
            style={{ ...tag, background: !filter ? '#eef2ff' : '#f3f4f6', color: !filter ? '#4338ca' : '#6b7280' }}
          >
            全部 ({data.total})
          </button>
          {sections.map(s => (
            <button
              key={s}
              onClick={() => setFilter(filter === s ? '' : s)}
              style={{
                ...tag,
                background: filter === s ? sectionColor(s) : '#f3f4f6',
                color: filter === s ? '#fff' : '#6b7280',
                borderLeft: `3px solid ${sectionColor(s)}`,
              }}
            >
              {s} ({data.chunks.filter(c => c.section === s).length})
            </button>
          ))}
        </div>

        {/* chunk list */}
        <div style={{ overflowY: 'auto', flex: 1 }}>
          {visible.map((chunk, i) => {
            const p1 = Number(chunk.page)
            const p2 = Number(chunk.page_end)
            const pageLabel = p2 !== p1 ? `p.${p1}-${p2}` : `p.${p1}`
            const barWidth = Math.round((chunk.chars / maxChars) * 100)
            const isOpen = expanded === chunk.id

            return (
              <div key={chunk.id} style={card} onClick={() => setExpanded(isOpen ? null : chunk.id)}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                  <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontSize: 11, color: '#9ca3af', minWidth: 28 }}>#{i + 1}</span>
                    <span style={{
                      fontSize: 11, padding: '1px 7px', borderRadius: 9,
                      background: sectionColor(chunk.section), color: '#fff',
                    }}>{chunk.section}</span>
                    <span style={{ fontSize: 12, color: '#6b7280' }}>{pageLabel}</span>
                    {chunk.parser && <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: parserColor(chunk.parser), color: '#fff' }}>{parserLabel(chunk.parser)}</span>}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 12, color: '#6b7280' }}>{chunk.chars} 字</span>
                    <button
                      onClick={e => startEdit(e, chunk)}
                      title="編輯此 chunk"
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#d1d5db', padding: '2px 4px', borderRadius: 6, display: 'flex', alignItems: 'center', transition: 'color 0.15s' }}
                      onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = '#3b82f6' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = '#d1d5db' }}
                    >
                      <Pencil size={12} />
                    </button>
                    <button
                      onClick={e => handleDelete(e, chunk.id)}
                      disabled={deleting.has(chunk.id)}
                      title="刪除此 chunk"
                      style={{ background: 'none', border: 'none', cursor: deleting.has(chunk.id) ? 'not-allowed' : 'pointer', color: '#d1d5db', padding: '2px 4px', borderRadius: 6, display: 'flex', alignItems: 'center', transition: 'color 0.15s' }}
                      onMouseEnter={e => { if (!deleting.has(chunk.id)) (e.currentTarget as HTMLButtonElement).style.color = '#ef4444' }}
                      onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = '#d1d5db' }}
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                </div>

                {/* bar */}
                <div style={{ height: 3, background: '#e5e7eb', borderRadius: 2, marginBottom: 8 }}>
                  <div style={{ height: '100%', width: `${barWidth}%`, background: sectionColor(chunk.section), borderRadius: 2 }} />
                </div>

                {editing === chunk.id ? (
                  <div onClick={e => e.stopPropagation()}>
                    <textarea
                      value={editDraft}
                      onChange={e => setEditDraft(e.target.value)}
                      style={{ width: '100%', minHeight: 180, fontSize: 12, lineHeight: 1.65, fontFamily: 'ui-monospace, Consolas, monospace', padding: '8px 10px', border: '1px solid #93c5fd', borderRadius: 6, outline: 'none', resize: 'vertical', boxSizing: 'border-box', color: '#1e293b' }}
                    />
                    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                      <button
                        onClick={() => handleSave(chunk.id)}
                        disabled={saving}
                        style={{ fontSize: 12, fontWeight: 700, padding: '4px 12px', borderRadius: 6, border: '1px solid #bfdbfe', background: '#eff6ff', color: '#1d4ed8', cursor: saving ? 'not-allowed' : 'pointer' }}
                      >
                        {saving ? '儲存中...' : '儲存'}
                      </button>
                      <button
                        onClick={e => { e.stopPropagation(); setEditing(null) }}
                        style={{ fontSize: 12, padding: '4px 12px', borderRadius: 6, border: '1px solid #e5e7eb', background: '#f9fafb', color: '#6b7280', cursor: 'pointer' }}
                      >
                        取消
                      </button>
                    </div>
                  </div>
                ) : isOpen ? (
                  <div style={{ fontSize: 12, color: '#374151', lineHeight: 1.65 }} className="chunk-md">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{chunk.content}</ReactMarkdown>
                  </div>
                ) : (
                  <div style={{ fontSize: 12, color: '#6b7280', lineHeight: 1.65 }}>
                    {chunk.preview}{chunk.content.length > 300 ? ' …' : ''}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
}
const modal: React.CSSProperties = {
  background: '#ffffff', borderRadius: 10, padding: 24,
  width: '80vw', maxWidth: 900, height: '85vh',
  display: 'flex', flexDirection: 'column', color: '#111827',
  boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
  border: '1px solid #e5e7eb',
}
const closeBtn: React.CSSProperties = {
  background: 'none', border: 'none', color: '#9ca3af',
  fontSize: 18, cursor: 'pointer', padding: '0 4px',
}
const tag: React.CSSProperties = {
  fontSize: 11, padding: '3px 9px', borderRadius: 20,
  border: '1px solid #e5e7eb', cursor: 'pointer',
}
const card: React.CSSProperties = {
  background: '#f9fafb', borderRadius: 8, padding: 12,
  marginBottom: 8, cursor: 'pointer', border: '1px solid #f3f4f6',
  transition: 'background 0.12s',
}
