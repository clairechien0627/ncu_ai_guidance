import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { Paperclip, ArrowUp, X, File, FileText, Trash2, PanelRight, Pencil } from 'lucide-react'
import ChunkViewer from '../viewer/ChunkViewer'
import type { Message } from '../../types'
import type { DocumentItem } from '../../api'

const MarkdownRenderer = lazy(() => import('../MarkdownRenderer'))

const MODE_LABELS: Record<string, string> = {
  chat: '聊天',
  retrieval: '文件問答',
  research: '深度研究',
  summary: '摘要',
  question: '導讀',
}

function modeClass(mode?: string | null) {
  return `mode-badge mode-${mode || 'unknown'}`
}

function StageIndicator({ stage }: { stage: string }) {
  return (
    <div className="flex items-center gap-2 mb-2 mt-0.5">
      <span className="inline-block w-3 h-3 rounded-full border border-slate-300 border-t-blue-400 animate-spin flex-shrink-0" />
      <span className="text-[11.5px] text-slate-400 tracking-wide">{stage}</span>
    </div>
  )
}

interface ChatWindowProps {
  messages: Message[]
  loading: boolean
  uploadingFiles: string[]
  error: string | null
  onSend: (text: string) => void
  documents: DocumentItem[]
  selectedDocIds: number[]
  onToggleDoc: (id: number) => void
  onUpload: (files: File[]) => void
  onDeleteDoc: (id: number) => void
  onReindexDoc: (id: number) => void
  docPanelOpen: boolean
  onToggleDocPanel: () => void
  onOpenDoc: (id: number) => void
  conversationTitle?: string | null
  threadId?: string | null
  onRenameConversation?: (threadId: string, title: string) => void
}

export default function ChatWindow({
  messages,
  loading,
  uploadingFiles,
  error,
  onSend,
  documents,
  selectedDocIds,
  onToggleDoc,
  onUpload,
  onDeleteDoc,
  onReindexDoc,
  docPanelOpen,
  onToggleDocPanel,
  onOpenDoc,
  conversationTitle,
  threadId,
  onRenameConversation,
}: ChatWindowProps) {
  const [input, setInput] = useState('')
  const [editingConvTitle, setEditingConvTitle] = useState(false)
  const [convTitleDraft, setConvTitleDraft] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [confirmDocId, setConfirmDocId] = useState<number | null>(null)
  const [chunkDocId, setChunkDocId] = useState<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const dragCounter = useRef(0)

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current++
    if (e.dataTransfer.types.includes('Files')) setDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current--
    if (dragCounter.current === 0) setDragging(false)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    dragCounter.current = 0
    setDragging(false)
    const files = Array.from(e.dataTransfer.files).filter(f => f.type === 'application/pdf')
    if (files.length > 0) onUpload(files)
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  const handleSend = () => {
    if ((!input.trim() && selectedDocIds.length === 0) || loading) return
    onSend(input.trim())
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 160) + 'px'
  }

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []).filter(f => f.type === 'application/pdf')
    if (files.length > 0) {
      onUpload(files)
      e.target.value = ''
      setMenuOpen(false)
    }
  }

  const selectedDocs = documents.filter((d) => selectedDocIds.includes(d.id))

  return (
    <div
      className="chat-area"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {dragging && (
        <div className="drop-overlay">
          <div className="drop-overlay-inner">
            <FileText size={36} strokeWidth={1.5} />
            <span>放開以上傳 PDF</span>
          </div>
        </div>
      )}
      <div className="chat-topbar">
        {/* Title + pencil */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0, flex: 1 }}>
          {editingConvTitle && threadId ? (
            <>
              <input
                autoFocus
                value={convTitleDraft}
                onChange={e => setConvTitleDraft(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    const t = convTitleDraft.trim()
                    if (t && threadId) onRenameConversation?.(threadId, t)
                    setEditingConvTitle(false)
                  }
                  if (e.key === 'Escape') setEditingConvTitle(false)
                }}
                onBlur={() => {
                  const t = convTitleDraft.trim()
                  if (t && threadId) onRenameConversation?.(threadId, t)
                  setEditingConvTitle(false)
                }}
                style={{
                  fontSize: 13, fontWeight: 600, border: '1px solid #60a5fa',
                  borderRadius: 6, padding: '2px 8px', outline: 'none',
                  minWidth: 0, width: 220, background: '#f8fafc',
                }}
              />
            </>
          ) : (
            <>
              {threadId && (
                <span style={{
                  fontSize: 13, fontWeight: 600, color: '#1e293b',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 280,
                }}>
                  {conversationTitle ?? '新對話'}
                </span>
              )}
              {threadId && (
                <button
                  className="topbar-icon-btn"
                  title="重新命名"
                  onClick={() => { setConvTitleDraft(conversationTitle ?? ''); setEditingConvTitle(true) }}
                  style={{ padding: 3 }}
                >
                  <Pencil size={13} />
                </button>
              )}
            </>
          )}
        </div>
        <button
          className={`topbar-icon-btn ${docPanelOpen ? 'active-toggle' : ''}`}
          onClick={onToggleDocPanel}
          title={docPanelOpen ? '關閉文件面板' : '開啟文件面板'}
        >
          <PanelRight size={16} />
        </button>
      </div>

      {/* Messages */}
      <div className="messages-container">
        {messages.length === 0 && !loading && (
          <div className="empty-state">
            <div className="empty-title">有什麼我可以幫你的？</div>
            <p className="empty-sub">上傳 PDF 報告，或直接提問開始分析</p>
          </div>
        )}

        {messages.map((msg, i) => {
          const isStreaming = loading && i === messages.length - 1 && msg.role === 'assistant'
          const isWaiting = isStreaming && msg.content === ''
          return (
            <div key={i} className={`msg-row ${msg.role}`}>
              {msg.role === 'user' ? (
                <>
                  {Array.isArray(msg.attachedDocs) && msg.attachedDocs.map((doc) => (
                    <div
                      key={doc.id}
                      className="user-attachment-badge clickable"
                      onClick={() => onOpenDoc(doc.id)}
                      title="點擊預覽 PDF"
                    >
                      <File size={12} style={{ flexShrink: 0 }} />
                      <span>{doc.name}</span>
                    </div>
                  ))}
                  <div className="user-bubble">{msg.content}</div>
                </>
              ) : (
                <div className="ai-content">
                  {msg.mode && (
                    <div className="agent-meta-row">
                      <span className={modeClass(msg.mode)}>
                        {MODE_LABELS[msg.mode] ?? msg.mode}
                      </span>
                      {msg.agentName && <span>{msg.agentName}</span>}
                      {msg.promptName && <span>{msg.promptName}{msg.promptVersion ? ` ${msg.promptVersion}` : ''}</span>}
                    </div>
                  )}

                  {/* waiting: no content yet */}
                  {isWaiting && (
                    <div className="pt-1">
                      {msg.stage ? (
                        <StageIndicator stage={msg.stage} />
                      ) : (
                        <div className="thinking-pulse">
                          <span /><span /><span />
                        </div>
                      )}
                    </div>
                  )}

                  {/* streaming or done: show content */}
                  {!isWaiting && (
                    <>
                      {isStreaming && msg.stage && <StageIndicator stage={msg.stage} />}
                      <div className={isStreaming ? 'is-streaming' : ''}>
                        <Suspense fallback={<div className="markdown-body">{msg.content}</div>}>
                          <MarkdownRenderer content={msg.content} />
                        </Suspense>
                      </div>
                      {msg.sources && msg.sources.length > 0 && (
                        <div className="msg-sources">
                          {msg.sources.map((s, si) => (
                            <span key={si} className="source-chip">
                              <FileText size={10} className="flex-shrink-0 opacity-60" />
                              {s}
                            </span>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          )
        })}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="input-dock">
        {error && (
          <div className="error-banner">
            <X size={13} /> {error}
          </div>
        )}

        <div className="input-box">
          {(uploadingFiles.length > 0 || selectedDocs.length > 0) && (
            <div className="input-attachments-row">
              {uploadingFiles.length > 0 && (
                <div className="input-attachment uploading">
                  <div className="upload-spinner" />
                  <span>上傳中 ({uploadingFiles.length} 個檔案)</span>
                </div>
              )}
              {selectedDocs.map((doc) => (
                <div key={doc.id} className="input-attachment">
                  <File size={13} style={{ flexShrink: 0 }} />
                  <span>{doc.filename}</span>
                  <button className="input-attachment-remove" onClick={() => onToggleDoc(doc.id)} title="移除">
                    <X size={11} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <textarea
            ref={textareaRef}
            className="chat-input"
            placeholder="傳訊息給 AI 分析師…"
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            disabled={loading}
            rows={1}
          />

          <div className="input-actions">
            <div className="attach-wrap" ref={menuRef}>
              <button
                className="input-icon-btn"
                onClick={() => setMenuOpen((v) => !v)}
                disabled={loading}
                title="附加文件"
              >
                <Paperclip size={16} />
              </button>

              {menuOpen && (
                <div className="attach-menu">
                  <button className="attach-menu-item" onClick={() => fileRef.current?.click()}>
                    <FileText size={14} />
                    上傳 PDF 報告
                  </button>
                  {documents.length > 0 && (
                    <>
                      <div className="attach-menu-sep">已上傳文件</div>
                      {documents.map((doc) => (
                        <div
                          key={doc.id}
                          className={`attach-menu-doc ${selectedDocIds.includes(doc.id) ? 'active' : ''}`}
                        >
                          <span
                            className="attach-menu-doc-name"
                            onClick={() => {
                              if (doc.status === 'ready') {
                                onToggleDoc(doc.id)
                              }
                            }}
                            title={doc.filename}
                          >
                            <File size={13} style={{ flexShrink: 0 }} />
                            <span>{doc.filename}</span>
                          </span>
                          {doc.status !== 'ready' && (
                            <span className={`doc-status ${doc.status}`}>{doc.status}</span>
                          )}
                          {confirmDocId === doc.id ? (
                            <div className="doc-confirm-row">
                              <button className="doc-confirm-yes" onClick={() => { onDeleteDoc(doc.id); setConfirmDocId(null) }}>刪除</button>
                              <button className="doc-confirm-no" onClick={() => setConfirmDocId(null)}>取消</button>
                            </div>
                          ) : (
                            <div className="doc-action-btns">
                              <button
                                className="doc-delete-btn"
                                onClick={() => setConfirmDocId(doc.id)}
                                disabled={loading}
                                title="刪除"
                              >
                                <Trash2 size={12} />
                              </button>
                            </div>
                          )}
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}

              <input
                ref={fileRef}
                type="file"
                accept=".pdf"
                multiple
                style={{ display: 'none' }}
                onChange={handleFileChange}
              />
            </div>

            <button
              className={`send-btn ${(input.trim() || selectedDocIds.length > 0) && !loading ? 'active' : ''}`}
              onClick={handleSend}
              disabled={loading || (!input.trim() && selectedDocIds.length === 0)}
              title="送出"
            >
              <ArrowUp size={16} strokeWidth={2.5} />
            </button>
          </div>
        </div>

        <p className="input-hint"> </p>
      </div>

      {chunkDocId && (
        <ChunkViewer docId={chunkDocId} onClose={() => setChunkDocId(null)} />
      )}
    </div>
  )
}
