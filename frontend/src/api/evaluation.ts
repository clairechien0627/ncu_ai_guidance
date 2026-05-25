import api from './client'

export interface EvalRunItem {
  eval_item_id: string; evaluation_item_id?: string; eval_run_id: string; evaluation_run_id?: string
  trace_id: string | null; target_trace_id?: string | null
  dataset_item_id: string | null; status: string; score_ids: string[] | null
  error: string | null; started_at: string | null; completed_at: string | null
  created_at: string | null; updated_at: string | null
  scores?: Record<string, number | null>
}

export interface EvalRun {
  eval_run_id: string; evaluation_run_id?: string; name: string; status: string; scope: string | null
  dataset_id: string | null; total_count: number; succeeded_count: number
  failed_count: number; last_error: string | null; prompt_name: string | null
  metadata: Record<string, unknown>; started_at: string | null
  completed_at: string | null; created_at: string | null; updated_at: string | null
  dimension_avgs?: Record<string, number | null>; avg_score?: number | null
  items?: EvalRunItem[]
}

export interface DatasetItemData {
  dataset_item_id: string; dataset_id: string; input: unknown; output: unknown
  reference_output?: unknown
  expected_output: unknown; context: unknown; source_trace_id: string | null
  tags: string[] | null; metadata: Record<string, unknown>
  is_archived: boolean; created_at: string | null; updated_at: string | null
}

export interface DatasetData {
  dataset_id: string; name: string; description: string | null; source: string | null
  metadata?: Record<string, unknown>; input_schema?: Record<string, unknown> | null
  expected_output_schema?: Record<string, unknown> | null
  item_count?: number; is_archived: boolean; created_at: string | null
  updated_at: string | null; items?: DatasetItemData[]
}

export interface ExperimentRunItemData {
  experiment_item_id: string; experiment_run_id: string; dataset_item_id: string
  status: string; generated_output: unknown; generated_context: unknown
  trace_id: string | null; output_trace_id?: string | null
  eval_run_id: string | null; evaluation_run_id?: string | null; error: string | null
  started_at: string | null; completed_at: string | null
  created_at: string | null; updated_at: string | null
}

export interface ExperimentRunData {
  experiment_run_id: string; dataset_id: string; name: string; status: string
  target_agent: string | null; model: string | null; prompt_name: string | null
  prompt_version: string | null; runtime_config: Record<string, unknown>
  metadata: Record<string, unknown>; total_count: number; succeeded_count: number
  failed_count: number; last_error: string | null; started_at: string | null
  completed_at: string | null; created_at: string | null; updated_at: string | null
  items?: ExperimentRunItemData[]
}

export interface ExperimentCompareResult {
  experiment_a: ExperimentRunData; experiment_b: ExperimentRunData
  compared_item_count: number; improved_count: number
  regressed_count: number; neutral_count: number
  dimension_deltas: Record<string, { a: number; b: number; delta: number }>
  items: Array<{
    dataset_item_id: string; a_score: number; b_score: number; delta: number
    status: 'improved' | 'regressed' | 'neutral'; a_output: string; b_output: string
    dimension_scores: Record<string, { a: number | null; b: number | null; delta: number | null }>
  }>
}

export const getEvalRuns = async (params?: { status?: string; limit?: number; offset?: number }) => {
  const { data } = await api.get('/evaluations/runs', { params })
  return data as { runs: EvalRun[]; total: number }
}
export const getEvalRunDetail = async (id: string) => {
  const { data } = await api.get(`/evaluations/runs/${id}`)
  return data as EvalRun
}
export const createEvalRun = async (body: { limit?: number; name?: string }) => {
  const { data } = await api.post('/traces/batch-score', null, { params: { limit: body.limit ?? 50 } })
  return data
}
export const addTraceToDataset = async (datasetId: string, traceId: string, tags?: string[]) => {
  const { data } = await api.post(`/datasets/${datasetId}/items/from-trace`, { trace_id: traceId, tags })
  return data
}
export const getDatasets = async () => {
  const { data } = await api.get('/datasets')
  return data as { datasets: DatasetData[]; total: number }
}
export const getDatasetDetail = async (id: string) => {
  const { data } = await api.get(`/datasets/${id}`)
  return data as DatasetData
}
export const createDataset = async (body: {
  name: string; description?: string; source?: string; metadata?: Record<string, unknown>
  input_schema?: Record<string, unknown>; expected_output_schema?: Record<string, unknown>
}) => {
  const { data } = await api.post('/datasets', body)
  return data as DatasetData
}
export const createDatasetItem = async (datasetId: string, body: { input?: unknown; expected_output?: unknown; context?: unknown; tags?: string[] }) => {
  const { data } = await api.post(`/datasets/${datasetId}/items`, body)
  return data as DatasetData
}
export const deleteDatasetItem = async (datasetId: string, itemId: string) => {
  await api.delete(`/datasets/${datasetId}/items/${itemId}`)
}
export const getExperimentRuns = async (params?: { status?: string; dataset_id?: string; limit?: number; offset?: number }) => {
  const { data } = await api.get('/experiments/runs', { params })
  return data as { runs: ExperimentRunData[]; total: number }
}
export const getExperimentRunDetail = async (id: string) => {
  const { data } = await api.get(`/experiments/runs/${id}`)
  return data as ExperimentRunData
}
export const createExperimentRun = async (body: { dataset_id: string; name: string; prompt_name?: string; prompt_version?: string; model?: string }) => {
  const { data } = await api.post('/experiments/dataset-replays', body)
  return data
}
export const compareExperimentRuns = async (a: string, b: string) => {
  const { data } = await api.get('/experiments/compare', { params: { a, b } })
  return data as ExperimentCompareResult
}
export const triggerExperimentEval = async (expRunId: string) => {
  const { data } = await api.post(`/experiments/runs/${expRunId}/eval`, {})
  return data
}
