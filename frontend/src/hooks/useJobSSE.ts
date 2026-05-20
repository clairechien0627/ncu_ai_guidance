import { useEffect, useRef } from 'react'
import type { JobItem } from '../api'
import { useJobStore } from '../stores/jobStore'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

interface UseJobSSECallbacks {
  onItemsDone: () => void
  onParseDone: (docId: number) => void
  onExtractDone: (docId: number) => void
  selectedIdRef: React.MutableRefObject<number | null>
}

/**
 * Opens a single SSE connection to /api/jobs/stream and keeps the Zustand
 * jobStore in sync.  Side-effect callbacks are called when specific job
 * transitions occur so the parent component can refresh caches / traces.
 */
export function useJobSSE({
  onItemsDone,
  onParseDone,
  onExtractDone,
  selectedIdRef,
}: UseJobSSECallbacks) {
  const setJobs = useJobStore(s => s.setJobs)
  const prevJobsRef = useRef<JobItem[]>([])

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/jobs/stream`)

    source.onmessage = event => {
      try {
        const data = JSON.parse(event.data) as JobItem[]
        const prev = prevJobsRef.current

        const newlyDone = data.some(j =>
          j.status === 'done' && prev.some(p => p.doc_id === j.doc_id && p.status !== 'done')
        )
        if (newlyDone) onItemsDone()

        const selectedDoneParse = data.find(j =>
          j.status === 'done' &&
          j.doc_id === selectedIdRef.current &&
          j.job_type?.startsWith('parse_') &&
          prev.some(p => p.doc_id === j.doc_id && p.status !== 'done' && p.job_type === j.job_type)
        )
        if (selectedDoneParse?.doc_id != null) onParseDone(selectedDoneParse.doc_id)

        const selectedDoneExtract = data.find(j =>
          j.status === 'done' &&
          j.doc_id === selectedIdRef.current &&
          j.job_type?.startsWith('extract') &&
          prev.some(p => p.doc_id === j.doc_id && p.status !== 'done' && p.job_type?.startsWith('extract'))
        )
        if (selectedDoneExtract?.doc_id != null) onExtractDone(selectedDoneExtract.doc_id)

        prevJobsRef.current = data
        setJobs(data)
      } catch {}
    }

    return () => source.close()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
}
