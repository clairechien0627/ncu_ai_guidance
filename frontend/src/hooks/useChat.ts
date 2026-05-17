import { useState, useCallback } from 'react'
import { sendMessageStream } from '../api'
import type { ChatResponse, DocumentItem } from '../api'
import type { Message } from '../types'

interface UseChatOptions {
  model: string
  documents: DocumentItem[]
  onConversationUpdate: (convId: number, title?: string) => void
}

export function useChat({ model, documents, onConversationUpdate }: UseChatOptions) {
  const [messages, setMessages] = useState<Message[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [selectedDocIds, setSelectedDocIds] = useState<number[]>([])

  const handleSend = useCallback(
    async (text: string) => {
      if (loading) return
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
            next[next.length - 1] = { ...next[next.length - 1], content: next[next.length - 1].content + token }
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
          onConversationUpdate(newConvId, isFirst ? (title ?? undefined) : undefined)
        },
        (msg) => { setError(msg); setLoading(false) },
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
          setMessages((prev) => {
            const next = [...prev]
            next[next.length - 1] = { ...next[next.length - 1], content: '', stage: '切換文件檢索模式' }
            return next
          })
        },
      )
    },
    [loading, conversationId, model, selectedDocIds, documents, onConversationUpdate],
  )

  return {
    messages, setMessages,
    loading,
    error, setError,
    conversationId, setConversationId,
    selectedDocIds, setSelectedDocIds,
    handleSend,
  }
}
