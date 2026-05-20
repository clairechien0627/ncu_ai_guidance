import api from './client'

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

export interface PromptInfo {
  name: string
  source_name?: string
  version: string
  size_chars: number
  last_modified: string | null
}

export interface TestRouteResult {
  agent_name: string
  prompt_name: string
  prompt_version: string
  path: 'keyword' | 'llm'
}

export interface EvalReport {
  total: number
  correct: number
  accuracy: number
  wrong_cases: Array<{ id: string; message: string; expected: string; got: string; note: string }>
  all_results: Array<{ id: string; message: string; expected: string; got: string; correct: boolean; note: string }>
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
export const getPromptList = async (): Promise<PromptInfo[]> => {
  const { data } = await api.get<PromptInfo[] | PromptSummary[] | { prompts: PromptInfo[] }>('/prompts')
  if (!Array.isArray(data)) return (data as { prompts: PromptInfo[] }).prompts
  if (data.length === 0 || 'word_count' in data[0]) {
    return (data as PromptSummary[]).map(p => ({
      name: p.name,
      version: p.current_hash ? `sha256:${p.current_hash}` : '',
      size_chars: p.word_count ?? 0,
      last_modified: p.synced_at ?? null,
    }))
  }
  return data as PromptInfo[]
}
export const testRoute = async (message: string, documentIds?: number[]): Promise<TestRouteResult> => {
  const { data } = await api.post<TestRouteResult>('/traces/test-route', {
    message,
    document_ids: documentIds ?? null,
  })
  return data
}
export const runEval = async (): Promise<EvalReport> => {
  const { data } = await api.post<EvalReport>('/eval/run')
  return data
}
