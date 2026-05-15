import { create } from 'zustand'
import type { JobItem } from '../api'

interface JobState {
  jobs: JobItem[]
  setJobs: (jobs: JobItem[]) => void
}

export const useJobStore = create<JobState>((set) => ({
  jobs: [],
  setJobs: (jobs) => set({ jobs }),
}))

// Convenience selectors — use these to subscribe to only what you need,
// preventing re-renders when other parts of the store change.
export const selectActiveJobCount = (s: JobState) =>
  s.jobs.filter(j => j.status !== 'done' && j.status !== 'error' && j.status !== 'cancelled').length

export const selectJobs = (s: JobState) => s.jobs
