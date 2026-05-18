import { useState, useRef } from 'react'
import { ArrowLeft, Plus, FileText, X, Minus } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { getSummaries, type DocumentItem } from '../api'
import './DocInfoPanel.css'

function getTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

const DEPT_COLORS: Record<string, { bg: string; color: string }> = {
  '資訊工程': { bg: '#dbeafe', color: '#1d4ed8' },
  '電機工程': { bg: '#ede9fe', color: '#6d28d9' },
  '機械工程': { bg: '#fce7f3', color: '#9d174d' },
  '化學工程': { bg: '#fef3c7', color: '#92400e' },
  '土木工程': { bg: '#d1fae5', color: '#065f46' },
  '其他':     { bg: '#f3f4f6', color: '#6b7280' },
}

function deptColor(dept: string) {
  for (const [key, val] of Object.entries(DEPT_COLORS)) {
    if (dept.includes(key)) return val
  }
  return DEPT_COLORS['其他']
}

interface Props {
  documents: DocumentItem[]
  selectedDocIds: number[]
  onToggleDoc: (id: number) => void
  onUpload: (files: File[]) => void
  onOpenPdf: (id: number) => void
  onClose: () => void
}

function DocDetail({ doc, onOpenPdf, onBack }: {
  doc: DocumentItem
  onOpenPdf: (id: number) => void
  onBack: () => void
}) {
  const { data: summaries = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })

  const item = summaries.find(s => s.id === doc.id)
  const summary = item?.summary
  const dept = item?.department ?? ''
  const { bg, color } = deptColor(dept)

  return (
    <div className="dip-detail">
      <button className="dip-back-btn" onClick={onBack}>
        <ArrowLeft size={14} /> 返回
      </button>

      <h3 className="dip-detail-title">{getTitle(doc.filename)}</h3>

      {dept && (
        <span className="dip-dept-badge" style={{ background: bg, color }}>{dept}</span>
      )}

      {summary ? (
        <div className="dip-sections">
          {[
            { label: '研究動機與問題', text: summary.motivation },
            { label: '研究方法',       text: summary.method },
            { label: '研究成果',       text: summary.results },
          ].map(({ label, text }) => text ? (
            <div key={label} className="dip-section">
              <div className="dip-section-label">{label}</div>
              <div className="dip-section-text">{text}</div>
            </div>
          ) : null)}
        </div>
      ) : (
        <p className="dip-no-summary">尚未提取摘要</p>
      )}

      <button className="dip-pdf-btn" onClick={() => onOpenPdf(doc.id)}>
        <FileText size={14} /> 查看 PDF
      </button>
    </div>
  )
}

export default function DocInfoPanel({
  documents, selectedDocIds, onToggleDoc, onUpload, onOpenPdf, onClose,
}: Props) {
  const [detailDoc, setDetailDoc] = useState<DocumentItem | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: summaries = [] } = useQuery({
    queryKey: ['summaries-slim'],
    queryFn: () => getSummaries(true),
    staleTime: 60_000,
  })

  const hasSummary = (docId: number) =>
    summaries.find(s => s.id === docId)?.summary != null

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []).filter(f => f.type === 'application/pdf')
    if (files.length > 0) onUpload(files)
    e.target.value = ''
  }

  if (detailDoc) {
    return (
      <div className="doc-info-panel">
        <DocDetail
          doc={detailDoc}
          onOpenPdf={onOpenPdf}
          onBack={() => setDetailDoc(null)}
        />
      </div>
    )
  }

  const selectedCount = selectedDocIds.length

  return (
    <div className="doc-info-panel">
      <div className="dip-header">
        <span className="dip-title">
          參考文件{selectedCount > 0 ? ` (${selectedCount})` : ''}
        </span>
        <button className="dip-close-btn" onClick={onClose} title="關閉">
          <X size={16} />
        </button>
      </div>

      <div className="dip-list">
        {documents.length === 0 ? (
          <div className="dip-empty">
            尚未附加文件<br />從聊天框的迴紋針選取 PDF
          </div>
        ) : (
          documents.map(doc => {
            const selected = selectedDocIds.includes(doc.id)
            const statusCls = doc.status === 'ready'       ? 'dip-status--ready'
              : doc.status === 'processing' ? 'dip-status--processing'
              : 'dip-status--error'
            const statusLabel = doc.status === 'ready'       ? '就緒'
              : doc.status === 'processing' ? '處理中' : '錯誤'

            return (
              <div key={doc.id} className="dip-card dip-card--selected">
                <div className="dip-card-body" onClick={() => hasSummary(doc.id) ? setDetailDoc(doc) : onOpenPdf(doc.id)}>
                  <span className="dip-card-name" title={doc.filename}>
                    {getTitle(doc.filename)}
                  </span>
                  <span className={`dip-status ${statusCls}`}>{statusLabel}</span>
                </div>
                <button
                  className="dip-remove-btn"
                  onClick={e => { e.stopPropagation(); onToggleDoc(doc.id) }}
                  title="從聊天框移除"
                >
                  <Minus size={12} />
                </button>
              </div>
            )
          })
        )}
      </div>

      <div className="dip-footer">
        <button className="dip-upload-btn" onClick={() => fileRef.current?.click()}>
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
    </div>
  )
}
