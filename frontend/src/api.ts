import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL ?? ''
const api = axios.create({ baseURL: `${API_BASE}/api` })

export interface DocumentItem {
  id: number
  filename: string
  status: 'processing' | 'ready' | 'error'
  created_at: string
  needs_reindex?: boolean
}

export interface UploadResponse {
  id: number
  filename: string
  status: string
  chunks?: number
  queued?: boolean
  duplicate: boolean
  duplicate_reason?: 'same_file_hash' | 'same_filename_legacy' | string
}

export interface ChatResponse {
  conversation_id: number
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
  trace_run_id?: string | null
}

export const uploadDocument = async (file: File): Promise<UploadResponse> => {
  const formData = new FormData()
  formData.append('file', file)
  const { data } = await api.post<UploadResponse>('/upload', formData)
  return data
}

export const getDocuments = async (): Promise<DocumentItem[]> => {
  const { data } = await api.get<DocumentItem[]>('/documents')
  return data
}

export const deleteDocument = async (id: number): Promise<void> => {
  await api.delete(`/documents/${id}`)
}

export const reindexDocument = async (id: number, parser: string = 'auto'): Promise<{ chunks: number }> => {
  const { data } = await api.post<{ chunks: number }>(`/documents/${id}/reindex`, { parser })
  return data
}

export const cancelJob = async (docId: number): Promise<void> => {
  await api.post(`/jobs/${docId}/cancel`)
}

export const renameDocument = async (id: number, newTitle: string): Promise<{ filename: string }> => {
  const { data } = await api.patch<{ filename: string }>(`/documents/${id}/rename`, { new_title: newTitle })
  return data
}

export interface ConversationItem {
  id: number
  created_at: string
  message_count: number
  model: string
  title: string | null
}

export const generateConversationTitle = async (id: number): Promise<string | null> => {
  const { data } = await api.post<{ title: string | null }>(`/conversations/${id}/title`)
  return data.title
}

export const getConversations = async (): Promise<ConversationItem[]> => {
  const { data } = await api.get<ConversationItem[]>('/conversations')
  return data
}

export const deleteConversation = async (id: number): Promise<void> => {
  await api.delete(`/conversations/${id}`)
}

export const updateConversationTitle = async (id: number, title: string): Promise<string> => {
  const { data } = await api.patch<{ title: string }>(`/conversations/${id}/title`, { title })
  return data.title
}

export const getConversationMessages = async (
  convId: number,
): Promise<{
  role: string
  content: string
  attached_docs?: { id: number; name: string }[]
  mode?: string | null
  agent_name?: string | null
  prompt_name?: string | null
  prompt_version?: string | null
  trace_run_id?: string | null
}[]> => {
  const { data } = await api.get(`/conversations/${convId}/messages`)
  return data
}

export interface SummaryItem {
  id: number
  filename: string
  department: string
  status: 'ready' | 'processing' | 'error'
  batch_status: 'pending' | 'processing' | 'summarized' | 'error'
  summary: { motivation: string; method: string; results: string; tags: string[]; intro?: string; questions?: string[] } | null
  created_at: string
  langsmith_run_id: string | null
  raw_research_answer: string | null
  raw_research_sources: string[]
  raw_research_run_id: string | null
  abstract_text: string | null
  quality_issue: 'garbled' | 'scanned' | 'image_heavy' | null
  parser_used: 'auto' | 'pymupdf4llm' | 'azure_di' | 'llamaparse' | null
  needs_reindex?: boolean
  caches: { pymupdf4llm: boolean; azure_di: boolean; llamaparse: boolean }
}

export const getSummaries = async (): Promise<SummaryItem[]> => {
  const { data } = await api.get<SummaryItem[]>('/summaries')
  return data
}

export const extractSummary = async (docId: number): Promise<{ id: number; filename: string; queued: boolean }> => {
  const { data } = await api.post<{ id: number; filename: string; queued: boolean }>(`/documents/${docId}/extract`)
  return data
}

export const extractSummaryStep = async (docId: number, step: 'step1' | 'step2' | 'step3'): Promise<{ id: number; filename: string; queued: boolean }> => {
  const { data } = await api.post<{ id: number; filename: string; queued: boolean }>(`/documents/${docId}/extract-step/${step}`)
  return data
}

export const updateAbstractText = async (docId: number, abstractText: string): Promise<void> => {
  await api.patch(`/documents/${docId}/abstract`, { abstract_text: abstractText })
}

export const refreshAbstractText = async (docId: number): Promise<{ text: string | null; skipped: boolean }> => {
  const { data } = await api.post<{ abstract_text: string | null; skipped: boolean }>(`/documents/${docId}/refresh-abstract`)
  return { text: data.abstract_text ?? null, skipped: data.skipped ?? false }
}

export const batchImport = async (): Promise<{ queued: number; retried: number; already_imported: number; dept_updated: number }> => {
  const { data } = await api.post('/summaries/batch-import')
  return data
}

export const batchExtractAll = async (): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-extract')
  return data
}

export const batchReextract = async (): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-reextract')
  return data
}

export const batchReextractAll = async (): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-reextract-all')
  return data
}

export interface JobItem {
  doc_id: number
  filename: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  job_type?: string
  stage?: string
  stage_log?: string[]
  completed_at?: string
  updated_at?: string
  started_at?: string
  job_id?: string
  error?: string | null
}

export const getJobs = async (): Promise<JobItem[]> => {
  const { data } = await api.get<JobItem[]>('/jobs')
  return data
}

export const reorderJob = async (doc_id: number, index: number): Promise<void> => {
  await api.post('/jobs/reorder', { doc_id, index })
}

export const clearJobHistory = async (): Promise<void> => {
  await api.post('/jobs/clear-history')
}

export type TraceMessage =
  | { role: 'system'; content: string }
  | { role: 'human'; content: string }
  | { role: 'ai'; content: string }
  | { role: 'ai_tool_call'; tool_calls: Array<{ tool: string; call_id: string; args: Record<string, unknown> }> }
  | { role: 'tool'; tool: string; call_id: string; chunks: Array<{ filename: string; page: number | null; content: string }>; raw: string | null }

export interface TraceEvent {
  type: 'user_message' | 'planner_message' | 'tool_call_args' | 'tool_result' | 'reflector_message' | 'writer_message'
  round: number
  slot_id: string | null
  slot_label: string | null
  display_intent: string | null
  payload_summary: string | null
  payload_debug: Record<string, unknown> | null
}

export interface TraceDisplay {
  messages: TraceMessage[]
  events?: TraceEvent[]
  answer: string | null
  sources: string[]
}

export interface TraceItem {
  id: string
  name: string
  status: 'success' | 'error' | string
  start_time: string | null
  end_time?: string | null
  latency: number | null
  error: string | null
  input?: string | null
  output?: string | null
  url: string | null
  display: TraceDisplay | null
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
  tool_count?: number | null
  llm_call_count?: number | null
  quality_score?: number | null
  user_feedback?: string | null
  original_intent?: string | null
  resolved_intent?: string | null
  quality_detail?: {
    grounding?: number
    completeness?: number
    source_quality?: number
    format_fit?: number
    limitations_honesty?: number
    overall?: number
    issues?: string[]
  } | null
}

export interface TraceDetail extends TraceItem {
  run_type?: string        // 'llm' | 'tool' | 'chain' | 'retriever'
  parent_run_id?: string | null
  thread_id?: string | null
  document_ids?: number[] | null
  inputs_raw?: unknown
  outputs_raw?: unknown
  prompt_tokens?: number | null
  completion_tokens?: number | null
  children?: TraceDetail[]
}

export interface TraceStats {
  total_runs: number
  period_runs: number
  error_runs: number
  success_runs: number
  error_rate: number
  avg_latency: number | null
  avg_quality: number | null
  runs_trend: number | null
  error_rate_trend: number | null
  latency_trend: number | null
  quality_trend: number | null
  avg_tool_count: number
  avg_llm_call_count: number
}

export const getTraceStats = async (days = 7): Promise<TraceStats> => {
  const { data } = await api.get<TraceStats>(`/traces/stats?days=${days}`)
  return data
}

export interface TraceGroupStats {
  key: string
  runs: number
  errors: number
  tokens: number
  avg_latency: number | null
  avg_quality_score?: number | null
  feedback_count?: number
}

export interface TraceFilters {
  prompt_name?: string
  prompt_version?: string
  status?: string
  min_latency?: string
  original_intent?: string
  resolved_intent?: string
  environment?: string
  offset?: number
}

export const getTraces = async (limit = 40, filters: TraceFilters = {}): Promise<TraceItem[]> => {
  const params = new URLSearchParams({ limit: String(limit) })
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '' && value !== 'all') {
      params.set(key, String(value))
    }
  })
  const { data } = await api.get<TraceItem[]>(`/traces?${params.toString()}`)
  return data
}


export const getTracesByRouteIntent = async (): Promise<TraceGroupStats[]> => {
  const { data } = await api.get<TraceGroupStats[]>('/traces/by-route-intent')
  return data
}

export const getTracesByPrompt = async (): Promise<TraceGroupStats[]> => {
  const { data } = await api.get<TraceGroupStats[]>('/traces/by-prompt')
  return data
}

export const getTracesByPromptVersion = async (): Promise<TraceGroupStats[]> => {
  const { data } = await api.get<TraceGroupStats[]>('/traces/by-prompt-version')
  return data
}

export const getTraceErrors = async (limit = 20): Promise<TraceItem[]> => {
  const { data } = await api.get<TraceItem[]>(`/traces/errors?limit=${limit}`)
  return data
}

export const getSlowRuns = async (limit = 20, minLatency = 10): Promise<TraceItem[]> => {
  const { data } = await api.get<TraceItem[]>(`/traces/slow-runs?limit=${limit}&min_latency=${minLatency}`)
  return data
}

export const getTraceDetail = async (runId: string): Promise<TraceDetail> => {
  const { data } = await api.get<TraceDetail>(`/traces/${runId}`)
  return data
}

export const updateTraceFeedback = async (
  runId: string,
  payload: { quality_score: number | null; user_feedback: string | null },
): Promise<TraceDetail> => {
  const { data } = await api.patch<TraceDetail>(`/traces/${runId}/feedback`, payload)
  return data
}

// ── Sessions ──────────────────────────────────────────────────────────────────

export interface SessionItem {
  thread_id: string
  task_type: string | null
  created_at: string | null
  ended_at: string | null
  duration_seconds: number | null
  trace_count: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  avg_quality_score: number | null
  user_ids: string[]
}

export interface SessionsResponse {
  sessions: SessionItem[]
  total: number
}

export interface SessionFilters {
  route_intent?: string
  user_id?: string
  environment?: string
  date_from?: string
  date_to?: string
  order_by?: string
  order_dir?: string
  offset?: number
}

export const getSessions = async (
  limit = 50,
  filters: SessionFilters = {},
): Promise<SessionsResponse> => {
  const params = new URLSearchParams({ limit: String(limit) })
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      params.set(key, String(value))
    }
  })
  const { data } = await api.get<SessionsResponse>(`/traces/sessions?${params.toString()}`)
  return data
}

export const getSessionDetail = async (threadId: string): Promise<SessionItem & { traces: TraceItem[] }> => {
  const { data } = await api.get(`/traces/sessions/${encodeURIComponent(threadId)}`)
  return data
}

// ── Prompts ───────────────────────────────────────────────────────────────────

export interface PromptSummary {
  name: string
  current_hash: string | null
  word_count: number | null
  version_count: number
  synced_at: string | null
  avg_quality_score: number | null
}

export interface PromptVersion {
  hash: string
  full_hash: string
  synced_at: string
  word_count: number
}

export interface PromptDetail {
  name: string
  content: string
  versions: PromptVersion[]
}

export interface PromptVersionContent {
  name: string
  hash: string
  full_hash: string
  content: string
  synced_at: string
  word_count: number
}

export const getPromptList2 = async (): Promise<PromptSummary[]> => {
  const { data } = await api.get<PromptSummary[]>('/prompts')
  return data
}

export const getPromptDetail = async (name: string): Promise<PromptDetail> => {
  const { data } = await api.get<PromptDetail>(`/prompts/${encodeURIComponent(name)}`)
  return data
}

export const getPromptVersionContent = async (name: string, hash: string): Promise<PromptVersionContent> => {
  const { data } = await api.get<PromptVersionContent>(`/prompts/${encodeURIComponent(name)}/versions/${hash}`)
  return data
}

export const getEnvironments = async (): Promise<string[]> => {
  const { data } = await api.get<string[]>('/traces/environments')
  return data
}

// ── Users ─────────────────────────────────────────────────────────────────────

export interface UserItem {
  user_id: string
  first_event: string | null
  last_event: string | null
  session_count: number
  trace_count: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  avg_quality_score: number | null
}

export interface UsersResponse {
  users: UserItem[]
  total: number
}

export const getUsers = async (
  limit = 50,
  filters: { environment?: string; date_from?: string; date_to?: string; search?: string; offset?: number } = {},
): Promise<UsersResponse> => {
  const params = new URLSearchParams({ limit: String(limit) })
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  const { data } = await api.get<UsersResponse>(`/traces/users?${params.toString()}`)
  return data
}

export const getUserDetail = async (userId: string) => {
  const { data } = await api.get(`/traces/users/${encodeURIComponent(userId)}`)
  return data
}

export const getDocumentTraces = async (docId: number): Promise<TraceItem[]> => {
  const { data } = await api.get<TraceItem[]>(`/documents/${docId}/traces`)
  return data
}

// ── Trace Monitor 新增 API ────────────────────────────────────────────────────

export interface TimelinePoint {
  date: string
  runs: number
  errors: number
  avg_latency: number | null
  avg_quality: number | null
}

export interface VersionCompare {
  version: string
  runs: number
  errors: number
  error_rate: number | null
  avg_latency: number | null
  avg_quality: number | null
}

export interface VersionCompareResult {
  v1: VersionCompare
  v2: VersionCompare
}

export interface TestRouteResult {
  intent: string
  agent_name: string
  prompt_name: string
  prompt_version: string
  original_intent?: string | null
  resolved_intent?: string | null
  path: 'keyword' | 'llm'
}

export interface PromptInfo {
  name: string
  source_name?: string
  version: string
  size_chars: number
  last_modified: string | null
}

export interface EvalReport {
  total: number
  correct: number
  accuracy: number
  wrong_cases: Array<{ id: string; message: string; expected: string; got: string; note: string }>
  all_results: Array<{ id: string; message: string; expected: string; got: string; correct: boolean; note: string }>
}

export const getTraceTimeline = async (
  promptName?: string,
  mode?: string,
  days = 14,
): Promise<TimelinePoint[]> => {
  const params = new URLSearchParams({ days: String(days) })
  if (promptName && promptName !== 'all') params.set('prompt_name', promptName)
  if (mode && mode !== 'all') params.set('mode', mode)
  const { data } = await api.get<TimelinePoint[]>(`/traces/timeline?${params}`)
  return data
}

export const compareTraceVersions = async (v1: string, v2: string): Promise<VersionCompareResult> => {
  const { data } = await api.get<VersionCompareResult>(
    `/traces/compare?v1=${encodeURIComponent(v1)}&v2=${encodeURIComponent(v2)}`,
  )
  return data
}

export const batchScoreTraces = async (limit = 50): Promise<{ queued: number; message: string }> => {
  const { data } = await api.post<{ queued: number; message: string }>(`/traces/batch-score?limit=${limit}`)
  return data
}

export const testRoute = async (message: string, documentIds?: number[]): Promise<TestRouteResult> => {
  const { data } = await api.post<TestRouteResult>('/traces/test-route', {
    message,
    document_ids: documentIds ?? null,
  })
  return data
}

export const getPromptList = async (): Promise<PromptInfo[]> => {
  const { data } = await api.get<PromptInfo[] | { prompts: PromptInfo[] }>('/prompts')
  return Array.isArray(data) ? data : data.prompts
}

export const runEval = async (): Promise<EvalReport> => {
  const { data } = await api.post<EvalReport>('/eval/run')
  return data
}

export interface ParserCacheInfo {
  parser: 'pymupdf4llm' | 'azure_di' | 'llamaparse'
  available: boolean
  size: number
}

export interface ParserCacheContent {
  parser: string
  page_count: number
  pages: string[]
}

export const parseDocument = async (docId: number, parser: string): Promise<{ id: number; parser: string; queued: boolean; duplicate?: boolean; job_id?: string }> => {
  const { data } = await api.post(`/documents/${docId}/parse`, { parser })
  return data
}

export const batchReparse = async (parser: string, docIds?: number[]): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-reparse', { parser, doc_ids: docIds ?? null })
  return data
}

export const batchExtractSelected = async (docIds: number[], force = false): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-extract-selected', { doc_ids: docIds, force })
  return data
}

export const getParserCaches = async (docId: number): Promise<ParserCacheInfo[]> => {
  const { data } = await api.get<ParserCacheInfo[]>(`/documents/${docId}/parser-caches`)
  return data
}

export const getParserCacheContent = async (docId: number, parser: string): Promise<ParserCacheContent> => {
  const { data } = await api.get<ParserCacheContent>(`/documents/${docId}/parser-cache/${parser}`)
  return data
}

export const deleteChunk = async (docId: number, chunkId: string): Promise<void> => {
  await api.delete(`/documents/${docId}/chunks/${chunkId}`)
}

export const updateChunk = async (docId: number, chunkId: string, content: string): Promise<void> => {
  await api.patch(`/documents/${docId}/chunks/${chunkId}`, { content })
}

export const exportDocument = async (docId: number, filename: string): Promise<void> => {
  const res = await fetch(`${API_BASE}/api/documents/${docId}/export`)
  if (!res.ok) throw new Error('匯出失敗')
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.replace(/\.pdf$/i, '') + '_export.zip'
  a.click()
  URL.revokeObjectURL(url)
}

export const sendMessageStream = async (
  message: string,
  conversationId: number | null,
  model: string,
  documentIds: number[],
  onToken: (token: string) => void,
  onDone: (conversationId: number, sources: string[], title: string | null, meta?: ChatResponse) => void,
  onError: (msg: string) => void,
  onMeta?: (meta: ChatResponse) => void,
  onStage?: (stage: string) => void,
  onClear?: () => void,
): Promise<void> => {
  const response = await fetch(`${API_BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message,
      conversation_id: conversationId,
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
  let convId = conversationId

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    const lines = decoder.decode(value).split('\n')
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const data = JSON.parse(line.slice(6))
        if (data.error) { onError(data.error); return }
        if (data.conversation_id) {
          convId = data.conversation_id
          onMeta?.(data)
        }
        if (data.clear) { onClear?.(); if (data.stage) onStage?.(data.stage) }
        else if (data.stage) onStage?.(data.stage)
        if (data.token) onToken(data.token)
        if (data.done) onDone(convId!, data.sources ?? [], data.title ?? null, data)
      } catch { /* incomplete chunk */ }
    }
  }
}
