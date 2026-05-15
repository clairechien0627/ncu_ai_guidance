import { Suspense, lazy, useState, useEffect, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Sidebar from './components/Sidebar'
import ChatWindow from './components/chat/ChatWindow'
import {
  uploadDocument,
  getDocuments,
  deleteDocument,
  reindexDocument,
  getConversations,
  getConversationMessages,
  deleteConversation,
} from './api'
import type { DocumentItem, ConversationItem, UploadResponse } from './api'
import { useChat } from './hooks/useChat'
import './App.css'

const PdfPanel = lazy(() => import('./components/viewer/PdfPanel'))
const SummaryPage = lazy(() => import('./pages/SummaryPage'))
const AdminTracesPage = lazy(() => import('./pages/AdminTracesPage'))

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
  const queryClient = useQueryClient()
  const [uploadingFiles, setUploadingFiles] = useState<string[]>([])
  const [convLoading, setConvLoading] = useState(false)
  const model = 'openai'
  const [pdfOpen, setPdfOpen] = useState(false)
  const [pdfViewDocId, setPdfViewDocId] = useState<number | null>(null)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [page, setPage] = useState<'chat' | 'summaries' | 'traces'>(
    () => (localStorage.getItem('activePage') as 'chat' | 'summaries' | 'traces') ?? 'chat'
  )

  // ── Server state ───────────────────────────────────────────────────────────

  const { data: documents = [] } = useQuery({
    queryKey: ['documents'],
    queryFn: getDocuments,
    // Auto-poll every 3 s while any document is processing.
    refetchInterval: (query) =>
      query.state.data?.some((d: DocumentItem) => d.status === 'processing') ? 3000 : false,
  })

  const { data: conversations = [] } = useQuery({
    queryKey: ['conversations'],
    queryFn: getConversations,
  })

  // ── Navigation ─────────────────────────────────────────────────────────────

  const navigateTo = useCallback((p: 'chat' | 'summaries' | 'traces') => {
    localStorage.setItem('activePage', p)
    setPage(p)
  }, [])

  const handleTogglePdf = () => {
    setPdfOpen((v) => {
      if (!v) setSidebarCollapsed(true)
      return !v
    })
  }

  const handleToggleSidebar = () => {
    setSidebarCollapsed((v) => {
      if (v) setPdfOpen(false)
      return !v
    })
  }

  // ── Chat ───────────────────────────────────────────────────────────────────

  const {
    messages, setMessages,
    loading,
    error, setError,
    conversationId, setConversationId,
    selectedDocIds, setSelectedDocIds,
    handleSend,
  } = useChat({
    model,
    documents,
    onConversationUpdate: useCallback((convId: number, title?: string) => {
      if (title) {
        queryClient.setQueryData(['conversations'], (prev: ConversationItem[] | undefined) =>
          prev?.map((c) => (c.id === convId ? { ...c, title } : c)) ?? []
        )
      }
      queryClient.invalidateQueries({ queryKey: ['conversations'] })
    }, [queryClient]),
  })

  // ── Persist attachment badges ───────────────────────────────────────────────

  useEffect(() => {
    if (!conversationId) return
    const map: Record<number, { name: string; id: number }[]> = {}
    messages.forEach((msg, i) => {
      if (msg.role === 'user' && msg.attachedDocs?.length) map[i] = msg.attachedDocs
    })
    if (Object.keys(map).length > 0)
      localStorage.setItem(`att_${conversationId}`, JSON.stringify(map))
  }, [messages, conversationId])

  // ── Document handlers ──────────────────────────────────────────────────────

  const handleUpload = useCallback(
    async (files: File[]) => {
      setError(null)
      setUploadingFiles(files.map((f) => f.name))
      const results = await Promise.allSettled(files.map((f) => uploadDocument(f)))
      setUploadingFiles([])
      queryClient.invalidateQueries({ queryKey: ['documents'] })
      const failed = results.filter((r) => r.status === 'rejected')
      if (failed.length > 0) setError(`${failed.length} 個檔案上傳失敗`)
      const newIds = results
        .filter((r): r is PromiseFulfilledResult<UploadResponse> => r.status === 'fulfilled')
        .map((r) => r.value.id)
      if (newIds.length > 0) setSelectedDocIds((prev) => [...new Set([...prev, ...newIds])])
    },
    [queryClient],
  )

  const handleDeleteDoc = useCallback(
    async (id: number) => {
      // Optimistic remove
      queryClient.setQueryData(['documents'], (prev: DocumentItem[] | undefined) =>
        prev?.filter((d) => d.id !== id) ?? []
      )
      setSelectedDocIds((prev) => prev.filter((d) => d !== id))
      try {
        await deleteDocument(id)
      } catch {
        setError('刪除失敗')
        queryClient.invalidateQueries({ queryKey: ['documents'] })
      }
    },
    [queryClient],
  )

  const handleReindexDoc = useCallback(
    async (id: number) => {
      // Optimistic status change
      queryClient.setQueryData(['documents'], (prev: DocumentItem[] | undefined) =>
        prev?.map((d) => (d.id === id ? { ...d, status: 'processing' } : d)) ?? []
      )
      try {
        await reindexDocument(id)
      } catch {
        setError('重新嵌入失敗')
      } finally {
        queryClient.invalidateQueries({ queryKey: ['documents'] })
      }
    },
    [queryClient],
  )

  // ── Conversation handlers ──────────────────────────────────────────────────

  const handleDeleteConversation = useCallback(
    async (id: number) => {
      // Optimistic remove
      queryClient.setQueryData(['conversations'], (prev: ConversationItem[] | undefined) =>
        prev?.filter((c) => c.id !== id) ?? []
      )
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
        queryClient.invalidateQueries({ queryKey: ['conversations'] })
      }
    },
    [conversationId, queryClient],
  )

  const handleLoadConversation = useCallback(
    async (id: number) => {
      if (convLoading) return
      setConvLoading(true)
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
        setConvLoading(false)
      }
    },
    [convLoading],
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

  // ── Pages ──────────────────────────────────────────────────────────────────

  if (page === 'summaries') {
    return (
      <Suspense fallback={<div className="app-layout loading-center">載入摘要頁中…</div>}>
        <SummaryPage onBack={() => navigateTo('chat')} />
      </Suspense>
    )
  }

  if (page === 'traces') {
    return (
      <Suspense fallback={<div className="app-layout loading-center">Loading traces</div>}>
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
          queryClient.setQueryData(['conversations'], (prev: ConversationItem[] | undefined) =>
            prev?.map((c) => (c.id === id ? { ...c, title } : c)) ?? []
          )
        }
        activeConversationId={conversationId}
        loading={loading || convLoading}
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
          queryClient.setQueryData(['conversations'], (prev: ConversationItem[] | undefined) =>
            prev?.map((c) => (c.id === id ? { ...c, title } : c)) ?? []
          )
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
