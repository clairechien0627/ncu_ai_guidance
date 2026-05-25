import api from './client'

export interface TraceItem {
  id: string
  name: string
  status: string
  start_time: string | null
  end_time?: string | null
  latency: number | null
  error: string | null
  input?: string | null
  output?: string | null
  url: string | null
  tags?: string[] | null
  bookmarked?: boolean
  total_cost?: number | null
  input_cost?: number | null
  output_cost?: number | null
  thread_id?: string | null
  environment?: string | null
  prompt_tokens?: number | null
  completion_tokens?: number | null
  observation_count?: number | null
  total_tokens?: number | null
  obs_status_counts?: Record<string, number> | null
  metadata?: Record<string, unknown> | null
  user_id?: string | null
  mode?: string | null
  agent_name?: string | null
  tool_count?: number | null
  llm_call_count?: number | null
  quality_score?: number | null
  user_feedback?: string | null
  quality_detail?: {
    grounding?: number; task_fit?: number; completeness?: number
    specificity?: number; source_quality?: number; uncertainty_honesty?: number
    format_fit?: number; overall?: number; verdict?: string
    issues?: string[]; evidence_gaps?: string[]; suggested_fixes?: string[]
    should_rerun_retrieval?: boolean; should_rerun_research?: boolean
  } | null
}

export interface TraceDetail extends TraceItem {
  type?: string
  parent_observation_id?: string | null
  thread_id?: string | null
  document_ids?: number[] | null
  inputs_raw?: unknown
  outputs_raw?: unknown
  prompt_tokens?: number | null
  completion_tokens?: number | null
  children?: TraceDetail[]
}

export interface TraceStats {
  total_runs: number; period_runs: number; error_runs: number; success_runs: number
  error_rate: number; avg_latency: number | null; avg_quality: number | null
  runs_trend: number | null; error_rate_trend: number | null
  latency_trend: number | null; quality_trend: number | null
  avg_tool_count: number; avg_llm_call_count: number
}

export interface TraceGroupStats {
  key: string; runs: number; errors: number; tokens: number
  avg_latency: number | null; avg_quality_score?: number | null; feedback_count?: number
}

export interface TraceFilters {
  prompt_name?: string; prompt_version?: string; status?: string
  min_latency?: string; max_quality?: number; min_quality?: number; has_score?: boolean
  environment?: string
  bookmarked?: boolean
  date_from?: string; date_to?: string
  tags?: string; names?: string; user_ids?: string
  tag?: string; name?: string; user_id?: string
  min_tokens?: number; max_tokens?: number; min_input_tokens?: number; min_output_tokens?: number
  offset?: number
}

export interface SessionItem {
  thread_id: string; created_at: string | null; ended_at: string | null
  duration_seconds: number | null; trace_count: number; input_tokens: number
  output_tokens: number; total_tokens: number; avg_quality_score: number | null; user_ids: string[]
}

export interface SessionsResponse { sessions: SessionItem[]; total: number }

export interface SessionFilters {
  agent_name?: string; user_id?: string; environment?: string
  date_from?: string; date_to?: string; order_by?: string; order_dir?: string; offset?: number
}

export interface TimelinePoint {
  date: string; runs: number; errors: number; avg_latency: number | null; avg_quality: number | null
}

export interface VersionCompare {
  version: string; runs: number; errors: number; error_rate: number | null
  avg_latency: number | null; avg_quality: number | null
}

export interface VersionCompareResult { v1: VersionCompare; v2: VersionCompare }

export interface ObservationItem {
  id: string; trace_id?: string | null; type: string; name: string; parent_observation_id: string | null
  thread_id: string | null; start_time: string | null; latency: number | null
  status?: string | null
  prompt_tokens: number | null; completion_tokens: number | null
  environment?: string | null
  prompt_id?: string | null; prompt_name?: string | null; prompt_version?: string | null
  tool_calls?: unknown[] | null; tool_definitions?: Record<string, unknown> | unknown[] | null; tool_call_names?: string[] | null
  error: string | null; input: string | null; output: string | null
}

export interface ObservationStats {
  total: number; total_tokens: number
  by_type: Record<string, { count: number; prompt_tokens: number; completion_tokens: number }>
}

export interface ScoreStats {
  total: number; scored: number; unscored: number; avg_score: number | null
  low_quality_count: number; low_quality_pct: number | null
  distribution: { bucket: string; count: number }[]
  dimension_avgs: {
    grounding?: number | null; task_fit?: number | null; completeness?: number | null
    specificity?: number | null; source_quality?: number | null
    uncertainty_honesty?: number | null; format_fit?: number | null
  }
}

export interface UserItem {
  user_id: string; first_event: string | null; last_event: string | null
  session_count: number; trace_count: number; input_tokens: number
  output_tokens: number; total_tokens: number; avg_quality_score: number | null
}

export interface UsersResponse { users: UserItem[]; total: number }

export const getTraceStats = async (days = 7): Promise<TraceStats> => {
  const { data } = await api.get<TraceStats>(`/traces/stats?days=${days}`)
  return data
}
export const getTraceTags = async (): Promise<string[]> => {
  const { data } = await api.get<string[]>('/traces/tags')
  return data
}
export const getTraceNames = async (): Promise<{ name: string; count: number }[]> => {
  const { data } = await api.get<{ name: string; count: number }[]>('/traces/names')
  return data
}
export const getTraceUserIds = async (): Promise<{ user_id: string; count: number }[]> => {
  const { data } = await api.get<{ user_id: string; count: number }[]>('/traces/user-ids')
  return data
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
export const getTracesByAgent = async (): Promise<TraceGroupStats[]> => {
  const { data } = await api.get<TraceGroupStats[]>('/traces/by-agent')
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
export const updateTraceBookmark = async (runId: string, bookmarked: boolean): Promise<TraceDetail> => {
  const { data } = await api.patch<TraceDetail>(`/traces/${runId}/bookmark`, { bookmarked })
  return data
}
export const getSessions = async (limit = 50, filters: SessionFilters = {}): Promise<SessionsResponse> => {
  const params = new URLSearchParams({ limit: String(limit) })
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  })
  const { data } = await api.get<SessionsResponse>(`/traces/threads?${params.toString()}`)
  return data
}
export const getSessionDetail = async (threadId: string): Promise<SessionItem & { traces: TraceItem[] }> => {
  const { data } = await api.get(`/traces/threads/${encodeURIComponent(threadId)}`)
  return data
}
export const getDocumentTraces = async (docId: number): Promise<TraceItem[]> => {
  const { data } = await api.get<TraceItem[]>(`/documents/${docId}/traces`)
  return data
}
export const getTraceTimeline = async (promptName?: string, mode?: string, days = 14): Promise<TimelinePoint[]> => {
  const params = new URLSearchParams({ days: String(days) })
  if (promptName && promptName !== 'all') params.set('prompt_name', promptName)
  if (mode && mode !== 'all') params.set('mode', mode)
  const { data } = await api.get<TimelinePoint[]>(`/traces/timeline?${params}`)
  return data
}
export const compareTraceVersions = async (v1: string, v2: string): Promise<VersionCompareResult> => {
  const { data } = await api.get<VersionCompareResult>(`/traces/compare?v1=${encodeURIComponent(v1)}&v2=${encodeURIComponent(v2)}`)
  return data
}
export const getObservations = async (limit = 100, type?: string, offset = 0): Promise<ObservationItem[]> => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
  if (type && type !== 'all') params.set('type', type)
  const { data } = await api.get<ObservationItem[]>(`/traces/observations?${params}`)
  return data
}
export const getObservationStats = async (): Promise<ObservationStats> => {
  const { data } = await api.get<ObservationStats>('/traces/observations/stats')
  return data
}
export const deleteTraces = async (ids: string[]): Promise<{ deleted: number }> => {
  const { data } = await api.delete<{ deleted: number }>('/traces/batch', { data: { ids } })
  return data
}
export const batchScoreTraces = async (limit = 50): Promise<{ queued: number; message: string }> => {
  const { data } = await api.post<{ queued: number; message: string }>(`/traces/batch-score?limit=${limit}`)
  return data
}
export const getScoreStats = async (): Promise<ScoreStats> => {
  const { data } = await api.get<ScoreStats>('/traces/score-stats')
  return data
}
export const getEnvironments = async (): Promise<string[]> => {
  const { data } = await api.get<string[]>('/traces/environments')
  return data
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
