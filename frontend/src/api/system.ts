import axios from 'axios'
import api, { API_BASE } from './client'

export interface QueueStatus {
  outbox: { pending: number; processing: number; failed: number; processed: number }
  evaluation: { active_runs: number; items_pending: number; items_running: number; items_failed: number; items_completed: number; worker_running: boolean | null }
}

export const getHealth = async () => {
  const { data } = await axios.get<any>(`${API_BASE}/health`)
  return data
}
export const getQueueStatus = async (): Promise<QueueStatus> => {
  const { data } = await api.get<QueueStatus>('/system/queue-status')
  return data
}
