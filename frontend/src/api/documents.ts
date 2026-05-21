import api, { API_BASE } from './client'

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

export interface SummaryItem {
  id: number
  filename: string
  department: string
  status: 'ready' | 'processing' | 'error'
  batch_status: 'pending' | 'processing' | 'summarized' | 'error'
  summary: { motivation: string; method: string; results: string; tags: string[]; intro?: string; questions?: string[] } | null
  created_at: string
  raw_research_answer: string | null
  raw_research_sources: string[]
  abstract_text: string | null
  quality_issue: 'garbled' | 'scanned' | 'image_heavy' | null
  parser_used: 'auto' | 'pymupdf4llm' | 'azure_di' | 'llamaparse' | null
  needs_reindex?: boolean
  caches: { pymupdf4llm: boolean; azure_di: boolean; llamaparse: boolean }
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

// Documents
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
export const renameDocument = async (id: number, newTitle: string): Promise<{ filename: string }> => {
  const { data } = await api.patch<{ filename: string }>(`/documents/${id}/rename`, { new_title: newTitle })
  return data
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

// Summaries / extraction
export const getSummaries = async (slim = false): Promise<SummaryItem[]> => {
  const { data } = await api.get<SummaryItem[]>(`/summaries${slim ? '?slim=true' : ''}`)
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
export const batchReparse = async (parser: string, docIds?: number[]): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-reparse', { parser, doc_ids: docIds ?? null })
  return data
}
export const batchExtractSelected = async (docIds: number[], force = false): Promise<{ queued: number }> => {
  const { data } = await api.post('/summaries/batch-extract-selected', { doc_ids: docIds, force })
  return data
}

// Jobs
export const cancelJob = async (docId: number): Promise<void> => {
  await api.post(`/jobs/${docId}/cancel`)
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

// Parser / chunks
export const parseDocument = async (docId: number, parser: string): Promise<{ id: number; parser: string; queued: boolean; duplicate?: boolean; job_id?: string }> => {
  const { data } = await api.post(`/documents/${docId}/parse`, { parser })
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
export const getDocumentChunks = async (docId: number): Promise<{ chunks: any[]; total: number }> => {
  const { data } = await api.get(`/documents/${docId}/chunks`)
  return data
}
export const deleteChunk = async (docId: number, chunkId: string): Promise<void> => {
  await api.delete(`/documents/${docId}/chunks/${chunkId}`)
}
export const updateChunk = async (docId: number, chunkId: string, content: string): Promise<void> => {
  await api.patch(`/documents/${docId}/chunks/${chunkId}`, { content })
}
