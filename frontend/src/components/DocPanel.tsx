import { useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, Plus, ChevronDown, ChevronUp } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getSummaries, type DocumentItem } from '../api'
import './DocPanel.css'

function getTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

interface Props {
  documents: DocumentItem[]
  selectedDocIds: number[]
  onToggleDoc: (id: number) => void
  onUpload: (files: File[]) => void
}

function DocCard({
  doc,
  selected,
  onToggle,
}: {
  doc: DocumentItem
  selected: boolean
  onToggle: () => void
}) {
  const [expanded, setExpanded] = useState(false)

  const { data: summaries = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })
  const summary = summaries.find(s => s.id === doc.id)?.summary

  const statusCls = doc.status === 'ready' ? 'dp-card-status--ready'
    : doc.status === 'processing' ? 'dp-card-status--processing'
    : 'dp-card-status--error'

  const statusLabel = doc.status === 'ready' ? '就緒'
    : doc.status === 'processing' ? '處理中'
    : '錯誤'

  return (
    <div className={`dp-card${selected ? ' dp-card--selected' : ''}`}>
      <div className="dp-card-head">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          disabled={doc.status !== 'ready'}
          style={{ flexShrink: 0, cursor: doc.status === 'ready' ? 'pointer' : 'not-allowed' }}
        />
        <span className="dp-card-name" title={doc.filename}>{getTitle(doc.filename)}</span>
        <span className={`dp-card-status ${statusCls}`}>{statusLabel}</span>
        {summary && (
          <button className="dp-expand-btn" onClick={() => setExpanded(v => !v)}>
            {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
        )}
      </div>
      {expanded && summary && (
        <div className="dp-card-body">
          <div className="dp-card-summary">
            {summary.motivation.slice(0, 150)}{summary.motivation.length > 150 ? '…' : ''}
          </div>
          {selected && (
            <button className="dp-card-remove" onClick={onToggle}>✕ 從對話中移除</button>
          )}
        </div>
      )}
    </div>
  )
}

export default function DocPanel({ documents, selectedDocIds, onToggleDoc, onUpload }: Props) {
  const [open, setOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []).filter(f => f.type === 'application/pdf')
    if (files.length > 0) onUpload(files)
    e.target.value = ''
  }

  const selectedCount = selectedDocIds.length

  return (
    <>
      {/* Scrim (mobile only when open) */}
      {open && <div className="doc-panel-scrim" onClick={() => setOpen(false)} />}

      <div className={`doc-panel${open ? ' doc-panel--open' : ' doc-panel--collapsed'}`}>
        {!open ? (
          <div className="doc-panel-strip">
            <button className="doc-panel-toggle" onClick={() => setOpen(true)} title="開啟文件面板">
              <ChevronLeft size={16} />
            </button>
            {selectedCount > 0 && (
              <span className="doc-panel-count-badge">{selectedCount}</span>
            )}
          </div>
        ) : (
          <>
            <div className="doc-panel-header">
              <span className="doc-panel-title">
                參考文件{selectedCount > 0 ? ` (${selectedCount})` : ''}
              </span>
              <button className="doc-panel-toggle" onClick={() => setOpen(false)} title="收折">
                <ChevronRight size={16} />
              </button>
            </div>

            <div className="doc-panel-body">
              {documents.length === 0 ? (
                <div className="dp-empty">
                  尚未上傳文件<br />
                  點擊下方按鈕上傳 PDF
                </div>
              ) : (
                documents.map(doc => (
                  <DocCard
                    key={doc.id}
                    doc={doc}
                    selected={selectedDocIds.includes(doc.id)}
                    onToggle={() => onToggleDoc(doc.id)}
                  />
                ))
              )}
            </div>

            <div className="doc-panel-footer">
              <button className="dp-add-btn" onClick={() => fileRef.current?.click()}>
                <Plus size={13} /> 上傳 PDF
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                multiple
                style={{ display: 'none' }}
                onChange={handleFileChange}
              />
            </div>
          </>
        )}
      </div>
    </>
  )
}
