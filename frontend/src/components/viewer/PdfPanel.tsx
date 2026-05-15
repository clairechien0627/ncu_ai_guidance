import { useState, useEffect, useRef } from 'react'
import { X, ChevronDown, FileText } from 'lucide-react'
import type { DocumentItem } from '../../api'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

interface PdfPanelProps {
  documents: DocumentItem[]
  initialDocId: number | null
  onClose: () => void
}

export default function PdfPanel({ documents, initialDocId, onClose }: PdfPanelProps) {
  const readyDocs = documents.filter((d) => d.status === 'ready')
  const [viewDocId, setViewDocId] = useState<number | null>(
    initialDocId ?? (readyDocs[0]?.id ?? null)
  )
  const [dropOpen, setDropOpen] = useState(false)
  const dropRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (initialDocId !== null) setViewDocId(initialDocId)
  }, [initialDocId])

  useEffect(() => {
    if (!dropOpen) return
    const handler = (e: MouseEvent) => {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) setDropOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [dropOpen])

  const viewDoc = readyDocs.find((d) => d.id === viewDocId)

  return (
    <div className="pdf-panel">
      <div className="pdf-panel-header">
        <div className="pdf-doc-select-wrap" ref={dropRef}>
          <button
            className="pdf-doc-select-btn"
            onClick={() => setDropOpen((v) => !v)}
            title={viewDoc?.filename}
          >
            <FileText size={13} />
            <span>{viewDoc?.filename ?? '選擇文件'}</span>
            <ChevronDown size={13} className={`pdf-select-chevron ${dropOpen ? 'open' : ''}`} />
          </button>

          {dropOpen && (
            <div className="pdf-doc-dropdown">
              {readyDocs.length === 0 && (
                <div className="pdf-doc-dropdown-empty">無可用文件</div>
              )}
              {readyDocs.map((d) => (
                <button
                  key={d.id}
                  className={`pdf-doc-dropdown-item ${d.id === viewDocId ? 'active' : ''}`}
                  onClick={() => { setViewDocId(d.id); setDropOpen(false) }}
                  title={d.filename}
                >
                  {d.filename}
                </button>
              ))}
            </div>
          )}
        </div>

        <button className="pdf-close-btn" onClick={onClose} title="關閉">
          <X size={16} />
        </button>
      </div>

      <div className="pdf-viewer">
        {viewDoc ? (
          <iframe
            key={viewDoc.id}
            src={`${API_BASE}/api/documents/${viewDoc.id}/file#toolbar=1&view=FitH`}
            title={viewDoc.filename}
            className="pdf-iframe"
          />
        ) : (
          <div className="pdf-empty">尚無可預覽的文件</div>
        )}
      </div>
    </div>
  )
}
