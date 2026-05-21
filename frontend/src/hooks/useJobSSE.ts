import { useEffect, useRef, useState } from 'react'
import type { JobItem, ChatJobItem } from '../api'
import { useJobStore } from '../stores/jobStore'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

function getAuthToken(): string | null {
  try {
    const s = localStorage.getItem('auth-store')
    return s ? JSON.parse(s)?.state?.token : null
  } catch {
    return null
  }
}

interface UseJobSSECallbacks {
  onItemsDone: () => void
  onParseDone: (docId: number) => void
  onExtractDone: (docId: number) => void
  selectedIdRef: React.MutableRefObject<number | null>
}

/**
 * Opens a single SSE connection to /api/jobs/stream and keeps the Zustand
 * jobStore in sync.  Uses fetch instead of EventSource to support the
 * Authorization header required by the admin endpoint.
 */
export function useJobSSE({
  onItemsDone,
  onParseDone,
  onExtractDone,
  selectedIdRef,
}: UseJobSSECallbacks) {
  const setJobs = useJobStore(s => s.setJobs)
  const prevJobsRef = useRef<JobItem[]>([])
  const [chatJobs, setChatJobs] = useState<ChatJobItem[]>([])

  useEffect(() => {
    let aborted = false
    const controller = new AbortController()

    async function connect() {
      while (!aborted) {
        try {
          const token = getAuthToken()
          const resp = await fetch(`${API_BASE}/api/jobs/stream`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            signal: controller.signal,
          })
          if (!resp.ok || !resp.body) {
            await new Promise(r => setTimeout(r, 3000))
            continue
          }
          const reader = resp.body.getReader()
          const decoder = new TextDecoder()
          let buf = ''
          while (!aborted) {
            const { done, value } = await reader.read()
            if (done) break
            buf += decoder.decode(value, { stream: true })
            const parts = buf.split('\n\n')
            buf = parts.pop() ?? ''
            for (const part of parts) {
              const line = part.trim()
              if (!line || line.startsWith(':')) continue
              const dataLine = line.startsWith('data: ') ? line.slice(6) : line
              try {
                const parsed = JSON.parse(dataLine)
                // Support both legacy array format and new {jobs, chat_jobs} format
                const data: JobItem[] = Array.isArray(parsed) ? parsed : (parsed.jobs ?? [])
                const newChatJobs: ChatJobItem[] = Array.isArray(parsed) ? [] : (parsed.chat_jobs ?? [])
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
                setChatJobs(newChatJobs)
              } catch {}
            }
          }
        } catch (err: any) {
          if (err?.name === 'AbortError') return
        }
        if (!aborted) await new Promise(r => setTimeout(r, 3000))
      }
    }

    connect()
    return () => {
      aborted = true
      controller.abort()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return { chatJobs }
}
