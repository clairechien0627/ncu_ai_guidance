import { Suspense, lazy, useState, useEffect, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import ChatWindow from './components/ChatWindow'
import {
  uploadDocument,
  sendMessageStream,
  getDocuments,
  deleteDocument,
  reindexDocument,
  getConversations,
  getConversationMessages,
  deleteConversation,
} from './api'
import type { ChatResponse, DocumentItem, ConversationItem, UploadResponse } from './api'
import './App.css'

const PdfPanel = lazy(() => import('./components/PdfPanel'))
const SummaryPage = lazy(() => import('./components/SummaryPage'))
const AdminTracesPage = lazy(() => import('./components/AdminTracesPage'))

export interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  attachedDocs?: { name: string; id: number }[]
  mode?: string | null
  agentName?: string | null
  promptName?: string | null
  promptVersion?: string | null
  traceRunId?: string | null
  stage?: string | null
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [conversations, setConversations] = useState<ConversationItem[]>([])
  const [selectedDocIds, setSelectedDocIds] = useState<number[]>([])
  const model = 'openai'
  const [loading, setLoading] = useState(false)
  const [uploadingFiles, setUploadingFiles] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [pdfOpen, setPdfOpen] = useState(false)
  const [pdfViewDocId, setPdfViewDocId] = useState<number | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [page, setPage] = useState<'chat' | 'summaries' | 'traces'>(
    () => (localStorage.getItem('activePage') as 'chat' | 'summaries' | 'traces') ?? 'chat'
  )

  const navigateTo = useCallback((p: 'chat' | 'summaries' | 'traces') => {
    localStorage.setItem('activePage', p)
    setPage(p)
  }, [])
  const handleTogglePdf = () => {
    setPdfOpen((v) => {
      if (!v) setSidebarCollapsed(true)   // opening PDF → collapse sidebar
      return !v
    })
  }

  const handleToggleSidebar = () => {
    setSidebarCollapsed((v) => {
      if (v) setPdfOpen(false)            // expanding sidebar → close PDF
      return !v
    })
  }

  const refreshDocuments = useCallback(async () => {
    try {
      setDocuments(await getDocuments())
    } catch {
      // silently ignore
    }
  }, [])

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await getConversations())
    } catch {
      // silently ignore
    }
  }, [])

  useEffect(() => {
    refreshDocuments()
    refreshConversations()
  }, [refreshDocuments, refreshConversations])

  // Auto-poll while any document is still processing
  useEffect(() => {
    if (!documents.some((d) => d.status === 'processing')) return
    const id = setInterval(refreshDocuments, 3000)
    return () => clearInterval(id)
  }, [documents, refreshDocuments])

  // Persist attachment badges to localStorage whenever messages change
  useEffect(() => {
    if (!conversationId) return
    const map: Record<number, { name: string; id: number }[]> = {}
    messages.forEach((msg, i) => {
      if (msg.role === 'user' && msg.attachedDocs?.length)
        map[i] = msg.attachedDocs
    })
    if (Object.keys(map).length > 0)
      localStorage.setItem(`att_${conversationId}`, JSON.stringify(map))
  }, [messages, conversationId])

  const handleSend = useCallback(
    async (text: string) => {
      if (loading) return
      // If empty input with docs selected → auto-summarize
      const actualText = text.trim() || (selectedDocIds.length > 0 ? '請幫我摘要這份文件' : '')
      if (!actualText) return
      setLoading(true)
      setError(null)
      setSelectedDocIds([])
      const attachedDocs = documents
        .filter((d) => selectedDocIds.includes(d.id))
        .map((d) => ({ name: d.filename, id: d.id }))
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: actualText, attachedDocs: attachedDocs.length ? attachedDocs : undefined },
        { role: 'assistant', content: '' },
      ])

      const isFirst = !conversationId

      await sendMessageStream(
        actualText,
        conversationId,
        model,
        selectedDocIds,
        (token) => {
          setMessages((prev) => {
            const next = [...prev]
            next[next.length - 1] = {
              ...next[next.length - 1],
              content: next[next.length - 1].content + token,
            }
            return next
          })
        },
        (newConvId, sources, title, meta) => {
          setMessages((prev) => {
            const next = [...prev]
            next[next.length - 1] = {
              ...next[next.length - 1],
              stage: null,
              ...(sources.length > 0 ? {
                sources,
                mode: meta?.mode,
                agentName: meta?.agent_name,
                promptName: meta?.prompt_name,
                promptVersion: meta?.prompt_version,
                traceRunId: meta?.trace_run_id,
              } : {}),
            }
            return next
          })
          setConversationId(newConvId)
          setLoading(false)
          if (isFirst && title) {
            setConversations((prev) =>
              prev.map((c) => c.id === newConvId ? { ...c, title } : c)
            )
          }
          refreshConversations()
        },
        (msg) => {
          setError(msg)
          setLoading(false)
        },
        (meta: ChatResponse) => {
          setMessages((prev) => {
            const next = [...prev]
            const last = next[next.length - 1]
            if (last?.role === 'assistant') {
              next[next.length - 1] = {
                ...last,
                mode: meta.mode,
                agentName: meta.agent_name,
                promptName: meta.prompt_name,
                promptVersion: meta.prompt_version,
                traceRunId: meta.trace_run_id,
              }
            }
            return next
          })
        },
        (stage: string) => {
          setMessages((prev) => {
            const next = [...prev]
            next[next.length - 1] = { ...next[next.length - 1], stage }
            return next
          })
        },
        () => {
          // clear: chat handoff to retrieval — reset the assistant message content
          setMessages((prev) => {
            const next = [...prev]
            next[next.length - 1] = { ...next[next.length - 1], content: '', stage: '切換文件檢索模式' }
            return next
          })
        },
      )
    },
    [loading, conversationId, model, selectedDocIds, documents, refreshConversations],
  )

  const handleUpload = useCallback(
    async (files: File[]) => {
      setError(null)
      setUploadingFiles(files.map((f) => f.name))
      const results = await Promise.allSettled(files.map((f) => uploadDocument(f)))
      setUploadingFiles([])
      await refreshDocuments()
      const failed = results.filter((r) => r.status === 'rejected')
      if (failed.length > 0) setError(`${failed.length} 個檔案上傳失敗`)
      // add all successfully uploaded docs to selection
      const newIds = results
        .filter((r): r is PromiseFulfilledResult<UploadResponse> => r.status === 'fulfilled')
        .map((r) => r.value.id)
      if (newIds.length > 0) setSelectedDocIds((prev) => [...new Set([...prev, ...newIds])])
    },
    [refreshDocuments],
  )

  const handleDeleteDoc = useCallback(
    async (id: number) => {
      setDocuments((prev) => prev.filter((d) => d.id !== id))
      setSelectedDocIds((prev) => prev.filter((d) => d !== id))
      try {
        await deleteDocument(id)
      } catch {
        setError('刪除失敗')
        await refreshDocuments()
      }
    },
    [refreshDocuments],
  )

  const handleReindexDoc = useCallback(
    async (id: number) => {
      setDocuments((prev) => prev.map((d) => d.id === id ? { ...d, status: 'processing' } : d))
      try {
        await reindexDocument(id)
        await refreshDocuments()
      } catch {
        setError('重新嵌入失敗')
        await refreshDocuments()
      }
    },
    [refreshDocuments],
  )

  const handleDeleteConversation = useCallback(
    async (id: number) => {
      setConversations((prev) => prev.filter((c) => c.id !== id))
      localStorage.removeItem(`att_${id}`)
      if (conversationId === id) {
        setMessages([])
        setConversationId(null)
        setSelectedDocIds([])
      }
      try {
        await deleteConversation(id)
      } catch {
        setError('刪除對話失敗')
        await refreshConversations()
      }
    },
    [conversationId, refreshConversations],
  )

  const handleOpenDoc = useCallback((docId: number) => {
    setPdfViewDocId(docId)
    setSidebarCollapsed(true)
    setPdfOpen(true)
  }, [])

  const handleNewChat = () => {
    setMessages([])
    setConversationId(null)
    setError(null)
    setSelectedDocIds([])
  }

  const handleLoadConversation = useCallback(
    async (id: number) => {
      if (loading) return
      setLoading(true)
      setError(null)
      try {
        const msgs = await getConversationMessages(id)
        const attMap: Record<number, { name: string; id: number }[]> = JSON.parse(
          localStorage.getItem(`att_${id}`) ?? '{}'
        )
        setMessages(msgs.map((m, i) => {
          const raw = m.attached_docs ?? attMap[i]
          const attachedDocs = Array.isArray(raw) ? raw : raw ? [raw] : undefined
          return {
            role: m.role as 'user' | 'assistant',
            content: m.content,
            attachedDocs,
            mode: m.mode,
            agentName: m.agent_name,
            promptName: m.prompt_name,
            promptVersion: m.prompt_version,
            traceRunId: m.trace_run_id,
          }
        }))
        setConversationId(id)
        setSelectedDocIds([])
      } catch {
        setError('無法載入對話')
      } finally {
        setLoading(false)
      }
    },
    [loading],
  )

  if (page === 'summaries') {
    return (
      <Suspense fallback={<div className="app-layout" style={{ display: 'grid', placeItems: 'center' }}>載入摘要頁中…</div>}>
        <SummaryPage onBack={() => navigateTo('chat')} />
      </Suspense>
    )
  }

  if (page === 'traces') {
    return (
      <Suspense fallback={<div className="app-layout" style={{ display: 'grid', placeItems: 'center' }}>Loading traces</div>}>
        <AdminTracesPage onBack={() => navigateTo('chat')} />
      </Suspense>
    )
  }

  return (
    <div className="app-layout">
      <Sidebar
        conversations={conversations}
        onNewChat={handleNewChat}
        onLoadConversation={handleLoadConversation}
        onDeleteConversation={handleDeleteConversation}
        onRenameConversation={(id, title) =>
          setConversations(prev => prev.map(c => c.id === id ? { ...c, title } : c))
        }
        activeConversationId={conversationId}
        loading={loading}
        collapsed={sidebarCollapsed}
        onToggleCollapse={handleToggleSidebar}
        onOpenSummaries={() => navigateTo('summaries')}
        onOpenTraces={() => navigateTo('traces')}
      />
      <ChatWindow
        messages={messages}
        loading={loading}
        uploadingFiles={uploadingFiles}
        error={error}
        onSend={handleSend}
        documents={documents}
        selectedDocIds={selectedDocIds}
        onToggleDoc={(id) => setSelectedDocIds((prev) =>
          prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]
        )}
        onUpload={handleUpload}
        onDeleteDoc={handleDeleteDoc}
        onReindexDoc={handleReindexDoc}
        pdfOpen={pdfOpen}
        onTogglePdf={handleTogglePdf}
        onOpenDoc={handleOpenDoc}
        conversationId={conversationId}
        conversationTitle={conversations.find(c => c.id === conversationId)?.title ?? null}
        onRenameConversation={(id, title) =>
          setConversations(prev => prev.map(c => c.id === id ? { ...c, title } : c))
        }
      />
      {pdfOpen && (
        <Suspense fallback={<div style={{ minWidth: 360, background: '#fff', borderLeft: '1px solid #e5e7eb', display: 'grid', placeItems: 'center' }}>載入 PDF 中…</div>}>
          <PdfPanel
            documents={documents}
            initialDocId={pdfViewDocId ?? selectedDocIds[0] ?? null}
            onClose={() => { setPdfOpen(false); setSidebarCollapsed(false) }}
          />
        </Suspense>
      )}
    </div>
  )
}
