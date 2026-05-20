import api, { API_BASE } from './client'

export interface ChatResponse {
  thread_id: string
  response: string
  sources: string[]
  mode?: string | null
  agent_name?: string | null
  prompt_name?: string | null
  prompt_version?: string | null
  base_prompt_name?: string | null
  task_prompt_name?: string | null
  quality_prompt_name?: string | null
  base_prompt_hash?: string | null
  task_prompt_hash?: string | null
  quality_prompt_hash?: string | null
  prompt_stack_name?: string | null
  prompt_stack_json?: Array<{ name: string; base_name?: string; source_name?: string; version: string }> | string | null
  prompt_stack_tokens?: number | null
  original_intent?: string | null
  resolved_intent?: string | null
  trace_id?: string | null
}

export interface ConversationItem {
  id: number
  thread_id: string
  created_at: string
  message_count: number
  model: string
  title: string | null
}

export const sendChat = async (body: { message: string; thread_id?: string; document_ids?: number[] }) => {
  const { data } = await api.post<any>('/chat', body)
  return data
}

export const generateConversationTitle = async (threadId: string): Promise<string | null> => {
  const { data } = await api.post<{ title: string | null }>(`/conversations/${threadId}/title`)
  return data.title
}
export const getConversations = async (): Promise<ConversationItem[]> => {
  const { data } = await api.get<ConversationItem[]>('/conversations')
  return data
}
export const deleteConversation = async (threadId: string): Promise<void> => {
  await api.delete(`/conversations/${threadId}`)
}
export const updateConversationTitle = async (threadId: string, title: string): Promise<string> => {
  const { data } = await api.patch<{ title: string }>(`/conversations/${threadId}/title`, { title })
  return data.title
}
export const getConversationMessages = async (
  threadId: string,
): Promise<{
  role: string
  content: string
  attached_docs?: { id: number; name: string }[]
  mode?: string | null
  agent_name?: string | null
  prompt_name?: string | null
  prompt_version?: string | null
  trace_id?: string | null
}[]> => {
  const { data } = await api.get(`/conversations/${threadId}/messages`)
  return data
}

export const sendMessageStream = async (
  message: string,
  threadId: string | null,
  model: string,
  documentIds: number[],
  onToken: (token: string) => void,
  onDone: (threadId: string, sources: string[], title: string | null, meta?: ChatResponse) => void,
  onError: (msg: string) => void,
  onMeta?: (meta: ChatResponse) => void,
  onStage?: (stage: string) => void,
  onClear?: () => void,
): Promise<void> => {
  const _authToken = (() => { try { const s = localStorage.getItem('auth-store'); return s ? JSON.parse(s)?.state?.token : null } catch { return null } })()
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(_authToken ? { 'Authorization': `Bearer ${_authToken}` } : {}),
    },
    body: JSON.stringify({
      message,
      thread_id: threadId,
      model,
      document_ids: documentIds.length > 0 ? documentIds : null,
    }),
  })

  if (!response.ok || !response.body) {
    onError('串流請求失敗')
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let currentThreadId = threadId

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    const lines = decoder.decode(value).split('\n')
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const data = JSON.parse(line.slice(6))
        if (data.error) { onError(data.error); return }
        if (data.thread_id) {
          currentThreadId = data.thread_id
          onMeta?.(data)
        }
        if (data.clear) { onClear?.(); if (data.stage) onStage?.(data.stage) }
        else if (data.stage) onStage?.(data.stage)
        if (data.token) onToken(data.token)
        if (data.done) onDone(currentThreadId!, data.sources ?? [], data.title ?? null, data)
      } catch { /* incomplete chunk */ }
    }
  }
}
