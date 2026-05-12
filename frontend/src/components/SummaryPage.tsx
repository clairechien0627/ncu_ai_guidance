import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, ChevronRight, Clock3, Download, ExternalLink, FileText, FolderDown, MessageCircle, MessageSquareMore, Pencil, RefreshCw, Trash2, X } from 'lucide-react'
import {
  batchImport,
  cancelJob,
  clearJobHistory,
  exportDocument,
  extractSummary,
  extractSummaryStep,
  getDocumentTraces,
  getParserCacheContent,
  getJobs,
  getParserCaches,
  getSummaries,
  parseDocument,
  refreshAbstractText,
  reindexDocument,
  renameDocument,
  updateAbstractText,
  type ParserCacheContent,
  type ParserCacheInfo,
  type JobItem,
  type SummaryItem,
  type TraceItem,
} from '../api'

const BatchPage = lazy(() => import('./BatchPage'))
const ChunkViewer = lazy(() => import('./ChunkViewer'))
const DocChat = lazy(() => import('./DocChat'))
const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'))
const PdfPageViewer = lazy(() => import('./PdfPageViewer'))
const ResearchCard = lazy(() => import('./ResearchCard'))

interface Props {
  onBack: () => void
}

const API_BASE = import.meta.env.VITE_API_URL ?? ''

function getDisplayTitle(filename: string) {
  return filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')
}

function getProjectNumber(filename: string) {
  return filename.match(/^([^_]+)_/)?.[1] ?? ''
}

function relativeTime(iso?: string) {
  if (!iso) return ''
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return '剛剛'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分鐘前`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小時前`
  return `${Math.floor(seconds / 86400)} 天前`
}

function getStatusMeta(status: SummaryItem['batch_status']) {
  if (status === 'summarized') return { label: '摘要完成', cls: 'spill spill-done' }
  if (status === 'processing') return { label: '摘要中', cls: 'spill spill-working' }
  if (status === 'error') return { label: '摘要失敗', cls: 'spill spill-error' }
  return { label: '未摘要', cls: 'spill spill-pending' }
}

function getCollegeInfo(dept: string): { label: string; cls: string } {
  if (/資工|資訊|電機|通訊/.test(dept)) return { label: '資訊相關', cls: 'cbadge cbadge-cs' }
  if (/工程|機械|土木|化材|環工/.test(dept)) return { label: '工程學院', cls: 'cbadge cbadge-engineering' }
  if (/管理|企管|財金|經濟/.test(dept)) return { label: '管理學院', cls: 'cbadge cbadge-management' }
  if (/地科|地球|大氣/.test(dept)) return { label: '地球科學', cls: 'cbadge cbadge-earth' }
  if (/生醫|生命|醫/.test(dept)) return { label: '生醫相關', cls: 'cbadge cbadge-biomed' }
  if (/物理|化學|數學/.test(dept)) return { label: '理學院', cls: 'cbadge cbadge-science' }
  if (/文|歷史|哲學|英文/.test(dept)) return { label: '人文社科', cls: 'cbadge cbadge-liberal' }
  if (/客家/.test(dept)) return { label: '客家學院', cls: 'cbadge cbadge-hakka' }
  return { label: dept.slice(0, 4) || '其他', cls: 'cbadge cbadge-default' }
}

function StatusPill({ status }: { status: SummaryItem['batch_status'] }) {
  const meta = getStatusMeta(status)
  return <span className={meta.cls}>{meta.label}</span>
}

function getJobActionLabel(jobType?: string | null) {
  if (jobType === 'extract_step1') return 'Step 1'
  if (jobType === 'extract_step2') return 'Step 2'
  if (jobType === 'extract_step3') return 'Step 3'
  if (jobType === 'extract') return '摘要'
  if (jobType?.startsWith('parse_')) return '解析'
  return '嵌入'
}

function shouldShowStageInLog(stage: string) {
  return stage !== 'RAG 分析中' && stage !== 'Step 1 研究檢索中'
}

function shortStage(stage: string, maxLength = 72) {
  return stage.length > maxLength ? `${stage.slice(0, maxLength)}...` : stage
}

export default function SummaryPage({ onBack }: Props) {
  const [items, setItems] = useState<SummaryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [rightView, setRightView] = useState<'pdf' | 'chat'>('pdf')
  const [search, setSearch] = useState('')
  const [deptFilter, setDeptFilter] = useState('全部')
  const [flash, setFlash] = useState<string | null>(null)
  const [batchPageOpen, setBatchPageOpen] = useState(false)
  const [jobDrawerOpen, setJobDrawerOpen] = useState(false)
  const [jobs, setJobs] = useState<JobItem[]>([])
  const [chunkDocId, setChunkDocId] = useState<number | null>(null)
  const [extracting, setExtracting] = useState<Set<number>>(new Set())
  const [extractingSteps, setExtractingSteps] = useState<Set<string>>(new Set())
  const [reindexing, setReindexing] = useState<Set<number>>(new Set())
  const [editingAbstract, setEditingAbstract] = useState(false)
  const [abstractDraft, setAbstractDraft] = useState('')
  const [savingAbstract, setSavingAbstract] = useState(false)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const [savingTitle, setSavingTitle] = useState(false)
  const [parserChoice, setParserChoice] = useState<Record<number, string>>({})
  const [parserCaches, setParserCaches] = useState<ParserCacheInfo[]>([])
  const [parserCachesLoaded, setParserCachesLoaded] = useState(false)
  const [parsingTabs, setParsingTabs] = useState<Set<string>>(new Set())
  const [docTraces, setDocTraces] = useState<TraceItem[]>([])
  const [tracesLoading, setTracesLoading] = useState(false)
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null)
  const [parserCompareOpen, setParserCompareOpen] = useState(false)
  const [activePanels, setActivePanels] = useState<Set<string>>(new Set())
  const [panelContents, setPanelContents] = useState<Record<string, { pages: string[]; loading: boolean }>>({})
  const [parserComparePage, setParserComparePage] = useState(0)
  const [pageInputValue, setPageInputValue] = useState('1')
  const [pageInputEditing, setPageInputEditing] = useState(false)
  const [dismissedJobKeys, setDismissedJobKeys] = useState<Set<string>>(new Set())
  const [expandedJobLogs, setExpandedJobLogs] = useState<Set<string>>(new Set())
  const [narrowShowDetail, setNarrowShowDetail] = useState(false)
  const pollRef = useRef<number | null>(null)
  const selectedIdRef = useRef<number | null>(null)

  const flashMessage = (message: string) => {
    setFlash(message)
    window.setTimeout(() => setFlash(null), 3500)
  }

  const loadItems = useCallback(async () => {
    const data = await getSummaries()
    setItems(data)
    setSelectedId(prev => (prev == null && data.length > 0 ? data[0].id : prev))
  }, [])

  const loadJobs = useCallback(async () => {
    const data = await getJobs()
    setJobs(data)
  }, [])

  const refreshAll = useCallback(async () => {
    setLoading(true)
    try {
      await Promise.all([loadItems(), loadJobs()])
    } finally {
      setLoading(false)
    }
  }, [loadItems, loadJobs])

  // Refresh only detail-panel data: parser caches + traces
  // Summary content is driven by SSE job events, not manual refresh
  const refreshDetail = useCallback(async () => {
    const capturedId = selectedIdRef.current
    if (capturedId == null) return
    setLoading(true)
    try {
      const [caches, traces] = await Promise.all([
        getParserCaches(capturedId),
        getDocumentTraces(capturedId),
      ])
      if (selectedIdRef.current === capturedId) {
        setParserCaches(caches)
        setParserCachesLoaded(true)
        setDocTraces(traces)
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refreshAll().catch(() => setLoading(false))
  }, [refreshAll])

  useEffect(() => {
    const shouldPoll =
      items.some(item => item.status === 'processing' || item.batch_status === 'processing') ||
      jobs.some(job => job.status !== 'done')

    if (shouldPoll && pollRef.current == null) {
      pollRef.current = window.setInterval(() => {
        Promise.all([loadItems(), loadJobs()]).catch(() => {})
      }, 4000)
    }

    if (!shouldPoll && pollRef.current != null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }

    return () => {
      if (pollRef.current != null) {
        window.clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [items, jobs, loadItems, loadJobs])

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/jobs/stream`)
    source.onmessage = event => {
      try {
        const data = JSON.parse(event.data) as JobItem[]
        setJobs(prev => {
          const newlyDone = data.some(j => j.status === 'done' &&
            prev.some(p => p.doc_id === j.doc_id && p.status !== 'done'))
          if (newlyDone) loadItems().catch(() => {})
          const selectedDoneParse = data.find(j =>
            j.status === 'done' &&
            j.doc_id === selectedIdRef.current &&
            j.job_type?.startsWith('parse_') &&
            prev.some(p => p.doc_id === j.doc_id && p.status !== 'done' && p.job_type === j.job_type)
          )
          if (selectedDoneParse && selectedIdRef.current != null) {
            getParserCaches(selectedIdRef.current)
              .then(caches => setParserCaches(caches))
              .catch(() => {})
          }
          // Reload traces when the selected document's extract job completes
          const selectedDoneExtract = data.find(j =>
            j.status === 'done' &&
            j.doc_id === selectedIdRef.current &&
            j.job_type?.startsWith('extract') &&
            prev.some(p => p.doc_id === j.doc_id && p.status !== 'done' && p.job_type?.startsWith('extract'))
          )
          if (selectedDoneExtract && selectedIdRef.current != null) {
            const capturedId = selectedIdRef.current
            setTracesLoading(true)
            getDocumentTraces(capturedId)
              .then(data => { if (selectedIdRef.current === capturedId) setDocTraces(data) })
              .catch(() => { if (selectedIdRef.current === capturedId) setDocTraces([]) })
              .finally(() => { if (selectedIdRef.current === capturedId) setTracesLoading(false) })
          }
          return data
        })
      } catch {}
    }
    return () => source.close()
  }, [])

  const departments = useMemo(
    () => ['全部', ...Array.from(new Set(items.map(item => item.department))).sort((a, b) => a.localeCompare(b, 'zh-TW'))],
    [items],
  )

  const filtered = useMemo(() => {
    return items.filter(item => {
      if (deptFilter !== '全部' && item.department !== deptFilter) return false
      const query = search.trim().toLowerCase()
      if (!query) return true
      return [
        getDisplayTitle(item.filename),
        item.filename,
        item.department,
        String(item.id),
        item.summary?.motivation ?? '',
        item.summary?.method ?? '',
        item.summary?.results ?? '',
        item.abstract_text ?? '',
      ].some(value => value.toLowerCase().includes(query))
    })
  }, [items, deptFilter, search])

  const selected = useMemo(() => filtered.find(item => item.id === selectedId) ?? items.find(item => item.id === selectedId) ?? null, [filtered, items, selectedId])
  const isParseQueuedOrRunning = useCallback((docId: number, parser: string) => {
    return jobs.some(job =>
      job.doc_id === docId &&
      job.status !== 'done' &&
      job.job_type === `parse_${parser}`
    )
  }, [jobs])

  // Smart parser default: explicit user choice > parser_used > best available cache > disabled
  const CACHE_PRIORITY = ['llamaparse', 'azure_di', 'pymupdf4llm'] as const
  const effectiveParser = useMemo<string | null>(() => {
    if (!selected) return null
    if (parserChoice[selected.id] != null) return parserChoice[selected.id]
    if (selected.parser_used && selected.parser_used !== 'auto') return selected.parser_used
    for (const p of CACHE_PRIORITY) {
      if (parserCaches.find(c => c.parser === p && c.available)) return p
    }
    return parserCachesLoaded ? null : null  // null = no caches → disabled
  }, [selected, parserChoice, parserCaches, parserCachesLoaded])

  useEffect(() => {
    selectedIdRef.current = selectedId
    setEditingAbstract(false)
    setAbstractDraft('')
    setParserCaches([])
    setParserCachesLoaded(false)
    setDocTraces([])
    setExpandedTraceId(null)
    if (selectedId != null) {
      const capturedId = selectedId
      const item = items.find(i => i.id === capturedId)
      if (item) setAbstractDraft(item.abstract_text ?? '')
      getParserCaches(capturedId)
        .then(data => {
          if (selectedIdRef.current !== capturedId) return
          setParserCaches(data)
          setParserCachesLoaded(true)
        })
        .catch(() => {
          if (selectedIdRef.current !== capturedId) return
          setParserCaches([])
          setParserCachesLoaded(true)
        })
      setTracesLoading(true)
      getDocumentTraces(capturedId)
        .then(data => { if (selectedIdRef.current === capturedId) setDocTraces(data) })
        .catch(() => { if (selectedIdRef.current === capturedId) setDocTraces([]) })
        .finally(() => { if (selectedIdRef.current === capturedId) setTracesLoading(false) })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId])

  const summaryCount = items.filter(item => item.summary).length
  const jobKey = (job: JobItem, index = 0) => job.job_id ?? `${job.doc_id}_${job.status}_${job.completed_at ?? index}`
  const visibleJobs = jobs.filter((job, index) => !dismissedJobKeys.has(jobKey(job, index)))
  const activeJobs = visibleJobs.filter(job => job.status === 'queued' || job.status === 'running')
  const doneJobs = visibleJobs
    .filter(job => job.status === 'done' || job.status === 'error' || job.status === 'cancelled')
    .slice()
    .sort((a, b) => (b.completed_at ?? '').localeCompare(a.completed_at ?? ''))

  const handleBatchImport = async () => {
    try {
      const result = await batchImport()
      flashMessage(
        result.queued > 0
          ? `已加入 ${result.queued} 筆匯入工作。`
          : result.already_imported > 0
          ? `沒有新檔案，現有 ${result.already_imported} 筆已存在。`
          : '批次匯入完成。',
      )
      await refreshAll()
    } catch (error: any) {
      flashMessage(`批次匯入失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    }
  }

  const handleReindex = async (item: SummaryItem) => {
    setReindexing(prev => new Set(prev).add(item.id))
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, status: 'processing' } : i))
    try {
      await reindexDocument(item.id, parserChoice[item.id] ?? 'auto')
      flashMessage(`已將 ${getDisplayTitle(item.filename)} 加入重新解析佇列。`)
      loadJobs().catch(() => {})
    } catch (error: any) {
      flashMessage(`重新解析失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    } finally {
      setReindexing(prev => {
        const next = new Set(prev)
        next.delete(item.id)
        return next
      })
    }
  }

  const handleParseWith = async (item: SummaryItem, parser: string) => {
    const key = `${item.id}_${parser}`
    setParsingTabs(prev => new Set(prev).add(key))
    try {
      const result = await parseDocument(item.id, parser)
      flashMessage(result.duplicate ? `${parser} 解析已在佇列中。` : `已加入 ${parser} 解析佇列。`)
      loadJobs().catch(() => {})
    } catch (error: any) {
      flashMessage(`建立 ${parser} 解析失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    } finally {
      setParsingTabs(prev => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    }
  }

  const loadPanelContent = (docId: number, parser: string) => {
    setPanelContents(prev => ({ ...prev, [parser]: { pages: [], loading: true } }))
    getParserCacheContent(docId, parser)
      .then(data => setPanelContents(prev => ({ ...prev, [parser]: { pages: data.pages, loading: false } })))
      .catch(() => setPanelContents(prev => ({ ...prev, [parser]: { pages: ['（快取讀取失敗）'], loading: false } })))
  }

  const togglePanel = (docId: number, key: string) => {
    setActivePanels(prev => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
        if (key !== 'pdf') loadPanelContent(docId, key)
      }
      return next
    })
  }

  const handleComparePage = (page: number) => {
    setParserComparePage(page)
    setPageInputValue(String(page + 1))
  }

  const openParserCompare = (item: SummaryItem) => {
    setPanelContents({})
    setParserComparePage(0)
    setPageInputValue('1')
    setPageInputEditing(false)
    const available = parserCaches.filter(c => c.available).map(c => c.parser)
    const defaultPanels = new Set<string>(['pdf', ...available.slice(0, 2)])
    setActivePanels(defaultPanels)
    available.slice(0, 2).forEach(p => loadPanelContent(item.id, p))
    setParserCompareOpen(true)
  }

  const handleExtract = async (item: SummaryItem) => {
    setExtracting(prev => new Set(prev).add(item.id))
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, batch_status: 'processing' } : i))
    try {
      const result = await extractSummary(item.id)
      if (result.queued) {
        flashMessage(`已將 ${getDisplayTitle(item.filename)} 加入摘要佇列。`)
        loadJobs().catch(() => {})
      } else {
        flashMessage('這份文件已在佇列中。')
      }
    } catch (error: any) {
      flashMessage(`摘要失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
      setItems(prev => prev.map(i => i.id === item.id ? { ...i, batch_status: item.batch_status } : i))
    } finally {
      setExtracting(prev => {
        const next = new Set(prev)
        next.delete(item.id)
        return next
      })
    }
  }

  const handleExtractStep = async (item: SummaryItem, step: 'step1' | 'step2' | 'step3') => {
    const key = `${item.id}_${step}`
    setExtractingSteps(prev => new Set(prev).add(key))
    setItems(prev => prev.map(i => i.id === item.id ? { ...i, batch_status: 'processing' } : i))
    try {
      const result = await extractSummaryStep(item.id, step)
      if (result.queued) {
        flashMessage(`已將 ${getDisplayTitle(item.filename)} 加入 ${step.toUpperCase()} 佇列。`)
        loadJobs().catch(() => {})
      } else {
        flashMessage(`${step.toUpperCase()} 已在佇列中。`)
      }
    } catch (error: any) {
      flashMessage(`${step.toUpperCase()} 失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
      setItems(prev => prev.map(i => i.id === item.id ? { ...i, batch_status: item.batch_status } : i))
    } finally {
      setExtractingSteps(prev => {
        const next = new Set(prev)
        next.delete(key)
        return next
      })
    }
  }

  const handleRefreshAbstract = async (item: SummaryItem) => {
    try {
      const result = await refreshAbstractText(item.id)
      setItems(prev => prev.map(entry => entry.id === item.id ? { ...entry, abstract_text: result.text } : entry))
      setAbstractDraft(result.text ?? '')
      flashMessage(result.skipped ? '找不到可更新的摘要。' : '已重新整理 PDF 摘要。')
    } catch (error: any) {
      flashMessage(`更新摘要失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    }
  }

  const handleSaveTitle = async (item: SummaryItem) => {
    const trimmed = titleDraft.trim()
    if (!trimmed) return
    setSavingTitle(true)
    try {
      const { filename } = await renameDocument(item.id, trimmed)
      setItems(prev => prev.map(e => e.id === item.id ? { ...e, filename } : e))
      setEditingTitle(false)
    } catch (err: any) {
      alert(err?.response?.data?.detail ?? '重新命名失敗')
    } finally {
      setSavingTitle(false)
    }
  }

  const handleSaveAbstract = async (item: SummaryItem) => {
    setSavingAbstract(true)
    try {
      await updateAbstractText(item.id, abstractDraft)
      setItems(prev => prev.map(entry => entry.id === item.id ? { ...entry, abstract_text: abstractDraft } : entry))
      setEditingAbstract(false)
      flashMessage('摘要已儲存。')
    } catch (error: any) {
      flashMessage(`儲存摘要失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    } finally {
      setSavingAbstract(false)
    }
  }

  const renderJobRow = (job: JobItem, index: number) => {
    const logKey = `${job.doc_id}_${job.job_type ?? 'job'}`
    const stageLog = (job.stage_log ?? []).filter(shouldShowStageInLog)
    const compactLog = stageLog.filter((stage, stageIndex) => stageIndex === 0 || stage !== stageLog[stageIndex - 1])
    const isLogExpanded = expandedJobLogs.has(logKey)
    const statusText =
      job.status === 'error' || (job.status === 'done' && job.error)
        ? `失敗${job.completed_at ? ` · ${relativeTime(job.completed_at)}` : ''}`
        : job.status === 'done'
        ? `已完成${getJobActionLabel(job.job_type)}${job.completed_at ? ` · ${relativeTime(job.completed_at)}` : ''}`
        : job.status === 'cancelled'
        ? `已取消${getJobActionLabel(job.job_type)}`
        : job.status === 'running'
        ? shortStage(job.stage ?? '處理中')
        : `等待${getJobActionLabel(job.job_type)}`

    return (
      <div
        key={jobKey(job, index)}
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 10,
          padding: '11px 16px',
          borderBottom: '1px solid rgba(236,241,247,0.95)',
          opacity: activeJobs.includes(job) ? 1 : 0.66,
          background: activeJobs.includes(job) ? 'rgba(255,255,255,0.72)' : 'rgba(248,250,252,0.72)',
        }}
      >
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            flexShrink: 0,
            marginTop: 8,
            boxShadow: '0 0 0 4px rgba(255,255,255,0.92)',
            backgroundColor:
              job.status === 'error' || (job.status === 'done' && job.error)
                ? '#ef4444'
                : job.status === 'done'
                ? '#22c55e'
                : job.status === 'cancelled'
                ? '#94a3b8'
                : job.status === 'running'
                ? job.job_type?.startsWith('extract')
                  ? '#7c3aed'
                  : '#0ea5e9'
                : '#f59e0b',
          }}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, overflow: 'hidden' }}>
            <span style={{ fontSize: 13, color: '#334155', fontWeight: 700, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
              {getDisplayTitle(job.filename)}
            </span>
            <span style={{ fontSize: 10, color: '#94a3b8', flexShrink: 0, fontFamily: 'monospace' }}>#{job.doc_id}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, minWidth: 0, marginTop: 1 }}>
            <span style={{ fontSize: 11, color: '#94a3b8', whiteSpace: 'normal', overflowWrap: 'anywhere', lineHeight: 1.45, flex: 1 }}>
              {statusText}
            </span>
            {compactLog.length > 0 ? (
              <button
                type="button"
                onClick={() => {
                  setExpandedJobLogs(prev => {
                    const next = new Set(prev)
                    if (next.has(logKey)) next.delete(logKey)
                    else next.add(logKey)
                    return next
                  })
                }}
                style={{
                  border: 'none',
                  background: 'rgba(226,232,240,0.75)',
                  color: '#64748b',
                  borderRadius: 999,
                  fontSize: 10,
                  padding: '2px 7px',
                  cursor: 'pointer',
                  flexShrink: 0,
                }}
              >
                {isLogExpanded ? '收合' : `紀錄 ${compactLog.length}`}
              </button>
            ) : null}
          </div>
          {isLogExpanded && compactLog.length > 0 ? (
            <div style={{
              marginTop: 7,
              display: 'flex',
              flexDirection: 'column',
              gap: 3,
              fontSize: 10,
              color: '#64748b',
              lineHeight: 1.45,
            }}>
              {compactLog.map((stage, stageIndex) => (
                <span key={`${stage}_${stageIndex}`} style={{
                  whiteSpace: 'normal',
                  overflowWrap: 'anywhere',
                }}>
                  {stageIndex === compactLog.length - 1 ? '• ' : '  '}
                  {stage}
                </span>
              ))}
            </div>
          ) : null}
        </div>
        <button
          style={{
            background: 'transparent',
            border: 'none',
            borderRadius: 8,
            color: '#cbd5e1',
            cursor: 'pointer',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 30,
            height: 30,
            flexShrink: 0,
            transition: 'color 0.15s, background 0.15s',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.color = '#ef4444'; (e.currentTarget as HTMLButtonElement).style.background = '#fee2e2' }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.color = '#cbd5e1'; (e.currentTarget as HTMLButtonElement).style.background = 'transparent' }}
          onClick={() => {
            if (job.status === 'done' || job.status === 'error' || job.status === 'cancelled') {
              setDismissedJobKeys(prev => new Set(prev).add(jobKey(job, index)))
              return
            }
            cancelJob(job.doc_id).then(() => {
              setParsingTabs(prev => {
                const next = new Set(prev)
                for (const key of next) {
                  if (key.startsWith(`${job.doc_id}_`)) next.delete(key)
                }
                return next
              })
              loadJobs()
              loadItems().catch(() => {})
            }).catch(() => { loadJobs(); loadItems().catch(() => {}) })
          }}
          title={(job.status === 'done' || job.status === 'error' || job.status === 'cancelled') ? '刪除此紀錄' : '取消工作'}
        >
          <Trash2 size={13} />
        </button>
      </div>
    )
  }

  return (
    <>
      {batchPageOpen && (
        <Suspense fallback={<div style={{ position: 'fixed', inset: 0, display: 'grid', placeItems: 'center', background: 'rgba(15,23,42,0.18)', zIndex: 200 }}>載入批次工具中...</div>}>
          <BatchPage items={items} onClose={() => setBatchPageOpen(false)} onJobsChanged={() => { refreshAll().catch(() => {}) }} />
        </Suspense>
      )}

      <div className="ncupage">
        <nav className="ncupage-nav">
          <div className="ncupage-nav-inner">
            <div className="ncupage-nav-left">
              <button className="ncu-back-btn ncu-back-btn-icon" onClick={onBack} title="返回聊天" aria-label="返回聊天">
                <ArrowLeft size={16} />
              </button>
              <div>
                <div className="ncupage-nav-title">大專生計畫成果</div>
              </div>
              <span style={{ fontSize: 12, color: '#94a3b8' }}>已提取 {summaryCount} / {items.length}</span>
            </div>
            <div className="ncupage-nav-right">
              {flash && <span className="ncu-status-flash">{flash}</span>}
              <button className="ncu-nav-btn" onClick={handleBatchImport} title="匯入新檔">
                <FolderDown size={13} /> 匯入新檔
              </button>
              <button className="ncu-nav-btn" onClick={() => setBatchPageOpen(true)} title="批次選取">
                批次選取
              </button>
              <button className="ncu-icon-btn" onClick={() => refreshDetail().catch(() => {})} title="重新整理詳情" disabled={loading}>
                <RefreshCw size={14} style={loading ? { animation: 'spin 0.8s linear infinite' } : undefined} />
              </button>
              <button className={`ncu-icon-btn${jobDrawerOpen ? ' active' : ''}`} onClick={() => setJobDrawerOpen(open => !open)} title="工作佇列">
                <Clock3 size={14} />
                {activeJobs.length > 0 && <span className="nav-job-badge">{activeJobs.length}</span>}
              </button>
            </div>
          </div>
        </nav>

        <main className="ncupage-main">
          <div className="ncupage-container" data-narrow-detail={narrowShowDetail ? 'true' : 'false'}>
            <div className="ncupage-left-rail">
              <div className="ncupage-filters">
                <div className="ncupage-filters-left" />
                <div className="ncupage-filters-right">
                  <input className="ncu-search" placeholder="搜尋標題、摘要、計畫編號…" value={search} onChange={event => setSearch(event.target.value)} />
                  <select className="ncu-select" value={deptFilter} onChange={event => setDeptFilter(event.target.value)}>
                    {departments.map(dept => <option key={dept} value={dept}>{dept}</option>)}
                  </select>
                  <span className="results-count">共 <strong>{filtered.length}</strong> 筆</span>
                </div>
              </div>

              <div className="ncupage-card-list">
                {loading ? (
                  <div className="ncupage-list-empty">載入中...</div>
                ) : filtered.length === 0 ? (
                  <div className="ncupage-list-empty">找不到符合條件的文件</div>
                ) : filtered.map(item => {
                  const college = getCollegeInfo(item.department)
                  const summaryMeta = getStatusMeta(item.batch_status)
                  return (
                    <div key={item.id} className={`project-card${selectedId === item.id ? ' pc-active' : ''}`} onClick={() => { setSelectedId(item.id); setRightView('pdf'); setEditingTitle(false); setNarrowShowDetail(true) }}>
                      <div className="pc-top-row">
                        <span className={college.cls}>{college.label}</span>
                        <div style={{ display: 'flex', gap: 4, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                          {item.status === 'processing' && <span className="spill spill-working">處理中</span>}
                          {item.status !== 'processing' && Array.from(parsingTabs).some(k => k.startsWith(`${item.id}_`)) && (
                            <span className="spill spill-working">解析中</span>
                          )}
                          {item.status === 'error' && <span className="spill spill-error">失敗</span>}
                          {item.status === 'ready' && item.parser_used && item.parser_used !== 'auto' && (
                            <span className="spill spill-embed">
                              {{ pymupdf4llm: 'PyMuPDF', azure_di: 'Azure DI', llamaparse: 'Llama' }[item.parser_used] ?? item.parser_used}
                            </span>
                          )}
                          {item.status === 'ready' && <span className={summaryMeta.cls}>{summaryMeta.label}</span>}
                          {item.needs_reindex && <span className="spill spill-error">需重嵌</span>}
                          {item.quality_issue && <span className="quality-pill">品質提醒</span>}
                        </div>
                      </div>
                      <h3 className="pc-title">{getDisplayTitle(item.filename)}</h3>
                      <div className="pc-meta">
                        <span className="pc-dept">{item.department}</span>
                        {getProjectNumber(item.filename) && (
                          <span style={{ fontSize: 10, color: '#94a3b8', fontFamily: 'monospace', marginLeft: 4 }}>
                            {getProjectNumber(item.filename)}
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="ncupage-split">
              <button
                className="narrow-back-btn"
                onClick={() => { setNarrowShowDetail(false); setSelectedId(null) }}
              >
                <ArrowLeft size={15} /> 返回列表
              </button>
              <div className="ncupage-detail">
                {!selected ? (
                  <div className="ncupage-placeholder">
                    <div className="ncupage-placeholder-card">
                      <div className="ncupage-placeholder-icon">
                        <MessageSquareMore size={34} strokeWidth={1.8} />
                      </div>
                      <div className="ncupage-placeholder-title">選擇一筆研究計畫即可查看詳細介紹。</div>
                      <div className="ncupage-placeholder-subtitle">接著可進一步檢視 PDF、嵌入流程與摘要內容。</div>
                    </div>
                  </div>
                ) : (
                  <div className="detail-scroll">
                    <div className="detail-summary-col">
                      <div className="detail-sec">
                        <div style={{ marginBottom: 12 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <span className={getCollegeInfo(selected.department).cls}>{getCollegeInfo(selected.department).label}</span>
                            <span style={{ fontSize: 11, color: '#94a3b8' }}>#{selected.id}</span>
                          </div>
                          {editingTitle ? (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 8 }}>
                              <input
                                autoFocus
                                className="ncu-search"
                                style={{ flex: 1, fontSize: 14, fontWeight: 600, height: 34 }}
                                value={titleDraft}
                                onChange={e => setTitleDraft(e.target.value)}
                                onKeyDown={e => {
                                  if (e.key === 'Enter') handleSaveTitle(selected)
                                  if (e.key === 'Escape') setEditingTitle(false)
                                }}
                              />
                              <button className="nbtn nbtn-blue nbtn-sm" onClick={() => handleSaveTitle(selected)} disabled={savingTitle}>
                                {savingTitle ? '…' : '儲存'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => setEditingTitle(false)}>取消</button>
                            </div>
                          ) : (
                            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, marginBottom: 6 }}>
                              <h2 className="detail-title-text" style={{ flex: 1, margin: 0 }}>{getDisplayTitle(selected.filename)}</h2>
                              <button
                                className="nbtn-icon"
                                style={{ marginTop: 3, flexShrink: 0 }}
                                title="重新命名"
                                onClick={() => { setTitleDraft(getDisplayTitle(selected.filename)); setEditingTitle(true) }}
                              >
                                <Pencil size={13} />
                              </button>
                            </div>
                          )}
                          <div className="detail-doc-meta">
                            <span className="detail-dept-chip">{selected.department}</span>
                            <StatusPill status={selected.batch_status} />
                          </div>
                        </div>

                        <div className="action-area">
                          <span className="action-area-lbl">文件流程</span>

                          <div className="action-step">
                            <div className="action-step-head">
                              <span className="action-step-no">1</span>
                              <span className="action-step-title">先解析</span>
                            </div>
                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                              {(['pymupdf4llm', 'azure_di', 'llamaparse'] as const).map(parser => {
                                const cache = parserCaches.find(entry => entry.parser === parser)
                                const busy = parsingTabs.has(`${selected.id}_${parser}`) || isParseQueuedOrRunning(selected.id, parser)
                                const label = parser === 'pymupdf4llm' ? 'PyMuPDF' : parser === 'azure_di' ? 'Azure DI' : 'Llama'
                                const sizeKb = cache?.available && cache.size > 0 ? `${(cache.size / 1024).toFixed(0)} KB` : null
                                return (
                                  <button
                                    key={parser}
                                    className={`pcache-pill${!busy && cache?.available ? ' ready' : ''}`}
                                    onClick={() => handleParseWith(selected, parser)}
                                    disabled={busy}
                                    title={cache?.available ? `${label} 已可用（${sizeKb}）` : `建立 ${label} 解析`}
                                  >
                                    {busy ? '處理中...' : label}
                                    {!busy && sizeKb && <span className="pcache-size">{sizeKb}</span>}
                                  </button>
                                )
                              })}
                            </div>
                          </div>

                          <div className="action-step">
                            <div className="action-step-head">
                              <span className="action-step-no">2</span>
                              <span className="action-step-title">選嵌入來源</span>
                              {selected.status === 'ready' && selected.parser_used && (
                                <span style={{
                                  fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999,
                                  background: '#ecfdf5', color: '#065f46', border: '1px solid #a7f3d0',
                                  marginLeft: 6,
                                }}>
                                  ✓ {{ pymupdf4llm: 'PyMuPDF', azure_di: 'Azure DI', llamaparse: 'Llama', auto: '自動' }[selected.parser_used] ?? selected.parser_used}
                                </span>
                              )}
                              {selected.needs_reindex && (
                                <span style={{
                                  fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 999,
                                  background: '#fff7ed', color: '#9a3412', border: '1px solid #fed7aa',
                                  marginLeft: 6,
                                }}>
                                  內容已手動修改，需重新嵌入
                                </span>
                              )}
                            </div>
                            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                              <select
                                className="ncu-select"
                                style={{ maxWidth: 180, minWidth: 150, height: 34, fontSize: 12 }}
                                value={effectiveParser ?? ''}
                                onChange={event => setParserChoice(prev => ({ ...prev, [selected.id]: event.target.value }))}
                                disabled={reindexing.has(selected.id) || selected.status === 'processing' || (parserCachesLoaded && effectiveParser === null)}
                              >
                                {effectiveParser === null && <option value="">（無快取可選）</option>}
                                <option value="auto">自動選擇</option>
                                <option value="pymupdf4llm">PyMuPDF4LLM</option>
                                <option value="azure_di">Azure DI</option>
                                <option value="llamaparse">LlamaParse</option>
                              </select>
                              <button className="nbtn nbtn-blue nbtn-sm" onClick={() => handleReindex(selected)} disabled={reindexing.has(selected.id) || selected.status === 'processing'}>
                                {reindexing.has(selected.id) ? '排入中...' : '嵌入'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => openParserCompare(selected)}>
                                解析對比
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => setChunkDocId(selected.id)}>嵌入結果</button>
                            </div>
                          </div>

                          <div className="action-step">
                            <div className="action-step-head">
                              <span className="action-step-no">3</span>
                              <span className="action-step-title">摘要與匯出</span>
                            </div>
                            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                              <button className="nbtn nbtn-purple nbtn-sm" onClick={() => handleExtract(selected)} disabled={extracting.has(selected.id) || selected.batch_status === 'processing' || (selected.status !== 'ready' && selected.status !== 'error')}>
                                {extracting.has(selected.id) ? '排入中...' : '摘要'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => handleExtractStep(selected, 'step1')} disabled={extractingSteps.has(`${selected.id}_step1`) || selected.batch_status === 'processing' || (selected.status !== 'ready' && selected.status !== 'error')}>
                                {extractingSteps.has(`${selected.id}_step1`) ? '排入中...' : 'Step 1'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => handleExtractStep(selected, 'step2')} disabled={extractingSteps.has(`${selected.id}_step2`) || !selected.raw_research_answer || selected.batch_status === 'processing' || (selected.status !== 'ready' && selected.status !== 'error')}>
                                {extractingSteps.has(`${selected.id}_step2`) ? '排入中...' : 'Step 2'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => handleExtractStep(selected, 'step3')} disabled={extractingSteps.has(`${selected.id}_step3`) || !selected.raw_research_answer || selected.batch_status === 'processing' || (selected.status !== 'ready' && selected.status !== 'error')}>
                                {extractingSteps.has(`${selected.id}_step3`) ? '排入中...' : 'Step 3'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => exportDocument(selected.id, selected.filename).catch(() => flashMessage('匯出失敗。'))}>
                                <Download size={11} /> 匯出
                              </button>
                            </div>
                          </div>
                        </div>
                      </div>

                      <div className="detail-sec">
                        <div className="detail-sec-hdr">
                          <span className="detail-sec-bar" />
                          <span className="detail-sec-label">PDF 摘要</span>
                          <div className="detail-sec-actions">
                            <button className="nbtn-icon" onClick={() => handleRefreshAbstract(selected)} title="重新整理 PDF 摘要">
                              <RefreshCw size={12} />
                            </button>
                            {!editingAbstract && (
                              <button className="nbtn-icon" onClick={() => setEditingAbstract(true)} title="編輯摘要">
                                <Pencil size={12} />
                              </button>
                            )}
                          </div>
                        </div>

                        {editingAbstract ? (
                          <>
                            <textarea className="ncu-textarea" value={abstractDraft} onChange={event => setAbstractDraft(event.target.value)} />
                            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                              <button className="nbtn nbtn-blue nbtn-sm" onClick={() => handleSaveAbstract(selected)} disabled={savingAbstract}>
                                {savingAbstract ? '儲存中...' : '儲存'}
                              </button>
                              <button className="nbtn nbtn-sm" onClick={() => { setEditingAbstract(false); setAbstractDraft(selected.abstract_text ?? '') }}>
                                取消
                              </button>
                            </div>
                          </>
                        ) : (
                          <p className="detail-sec-text">{selected.abstract_text || '尚未整理 PDF 摘要。'}</p>
                        )}
                      </div>

                      {selected.raw_research_answer && (
                        <div className="detail-sec">
                          <div className="detail-sec-hdr">
                            <span className="detail-sec-bar" />
                            <span className="detail-sec-label">Step 1 原始研究摘要</span>
                          </div>
                          <div className="detail-markdown">
                            <Suspense fallback={<p className="detail-sec-text">{selected.raw_research_answer}</p>}>
                              <MarkdownRenderer content={selected.raw_research_answer} />
                            </Suspense>
                          </div>
                          {selected.raw_research_sources?.length > 0 && (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                              {selected.raw_research_sources.slice(0, 6).map(source => <span key={source} className="detail-tag">{source}</span>)}
                            </div>
                          )}
                        </div>
                      )}

                      {/* AI 摘要內容：動機 / 方法 / 成果 / 標籤 */}
                      <div className="detail-sec">
                        <div className="detail-sec-hdr">
                          <span className="detail-sec-bar" />
                          <span className="detail-sec-label">AI 摘要內容</span>
                        </div>
                        {selected.summary ? (
                          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                            {(['motivation', 'method', 'results'] as const).map((key, i) => (
                              <section key={key}>
                                <div className="detail-sec-label" style={{ marginBottom: 5 }}>
                                  {['研究動機', '研究方法', '研究成果'][i]}
                                </div>
                                <div className="detail-markdown">
                                  <Suspense fallback={<p className="detail-sec-text">{selected.summary![key]}</p>}>
                                    <MarkdownRenderer content={selected.summary![key]} />
                                  </Suspense>
                                </div>
                              </section>
                            ))}
                            {selected.summary.tags?.length > 0 && (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                {selected.summary.tags.map(tag => <span key={tag} className="detail-tag">{tag}</span>)}
                              </div>
                            )}
                          </div>
                        ) : (
                          <p className="detail-sec-text">尚未產生 AI 摘要。</p>
                        )}
                      </div>

                      {/* 一分鐘看研究 + 興趣量表題目 */}
                      {selected.summary && (selected.summary.intro || (selected.summary.questions ?? []).length > 0) && (
                        <div className="detail-sec">
                          <div className="detail-sec-hdr">
                            <span className="detail-sec-bar" />
                            <span className="detail-sec-label">一分鐘看研究</span>
                          </div>
                          {selected.summary.intro && (
                            <div className="detail-markdown" style={{ marginBottom: 14 }}>
                              <Suspense fallback={<p className="detail-sec-text">{selected.summary.intro}</p>}>
                                <MarkdownRenderer content={selected.summary.intro} />
                              </Suspense>
                            </div>
                          )}
                          {(selected.summary.questions ?? []).length > 0 && (
                            <>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                                <span style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>興趣量表</span>
                                <span style={{ fontSize: 11, color: '#94a3b8' }}>1＝完全不感興趣　5＝非常感興趣</span>
                              </div>
                              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                                {(selected.summary.questions ?? []).map((q, i) => (
                                  <div key={i} style={{
                                    padding: '9px 12px', borderRadius: 8,
                                    background: '#f8fafc', border: '1px solid #e8edf3',
                                    fontSize: 13, color: '#334155', lineHeight: 1.65,
                                  }}>
                                    {q}
                                  </div>
                                ))}
                              </div>
                            </>
                          )}
                        </div>
                      )}

                      <div className="detail-sec">
                        <div className="detail-sec-hdr">
                          <span className="detail-sec-bar" />
                          <span className="detail-sec-label">追蹤紀錄</span>
                          <div className="detail-sec-actions">
                            <button
                              className="nbtn-icon"
                              title="重新整理追蹤紀錄"
                              onClick={() => {
                                if (selectedId == null) return
                                const capturedId = selectedId
                                setTracesLoading(true)
                                getDocumentTraces(capturedId)
                                  .then(data => { if (selectedIdRef.current === capturedId) setDocTraces(data) })
                                  .catch(() => { if (selectedIdRef.current === capturedId) setDocTraces([]) })
                                  .finally(() => { if (selectedIdRef.current === capturedId) setTracesLoading(false) })
                              }}
                            >
                              <RefreshCw size={12} />
                            </button>
                          </div>
                        </div>
                        {tracesLoading ? (
                          <p className="detail-sec-text">載入中...</p>
                        ) : docTraces.length === 0 ? (
                          <p className="detail-sec-text">尚無追蹤紀錄。</p>
                        ) : (
                          <div className="trace-list">
                            {docTraces.map(trace => {
                              const isOpen = expandedTraceId === trace.id
                              const latency = trace.latency == null ? null
                                : trace.latency < 60 ? `${trace.latency.toFixed(1)}s`
                                : `${Math.floor(trace.latency / 60)}m ${(trace.latency % 60).toFixed(0)}s`
                              const timeAgo = trace.start_time ? relativeTime(trace.start_time) : null
                              return (
                                <div key={trace.id} className={`trace-row${isOpen ? ' open' : ''}`}>
                                  <button
                                    type="button"
                                    className="trace-row-head"
                                    onClick={() => setExpandedTraceId(prev => prev === trace.id ? null : trace.id)}
                                  >
                                    <span className="trace-dot" data-status={trace.status} />
                                    <span className="trace-name">{trace.name}</span>
                                    <span className="trace-latency" style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                                      {timeAgo && <span>{timeAgo}</span>}
                                      {latency && <span style={{ color: '#94a3b8' }}>· {latency}</span>}
                                    </span>
                                    {trace.url && (
                                      <a
                                        href={trace.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        onClick={e => e.stopPropagation()}
                                        className="trace-ext-link"
                                        title="在 LangSmith 開啟"
                                      >
                                        <ExternalLink size={11} />
                                      </a>
                                    )}
                                    <ChevronRight size={13} className="trace-chevron" />
                                  </button>
                                  {isOpen && trace.display && (
                                    <div className="trace-row-body">
                                      {trace.display.messages.map((msg, i) => {
                                        if (msg.role === 'system') return (
                                          <div key={i} className="trace-msg">
                                            <span className="trace-msg-type" style={{ color: '#475569' }}>系統</span>
                                            <div className="trace-msg-content">{msg.content}</div>
                                          </div>
                                        )
                                        if (msg.role === 'human') return (
                                          <div key={i} className="trace-msg">
                                            <span className="trace-msg-type" style={{ color: '#2563eb' }}>問題</span>
                                            <div className="trace-msg-content">{msg.content}</div>
                                          </div>
                                        )
                                        if (msg.role === 'ai' && msg.content) return (
                                          <div key={i} className="trace-msg">
                                            <span className="trace-msg-type" style={{ color: '#7c3aed' }}>AI</span>
                                            <div className="trace-msg-content">{msg.content}</div>
                                          </div>
                                        )
                                        if (msg.role === 'ai_tool_call') return msg.tool_calls.map(tc => (
                                          <div key={tc.call_id} className="trace-msg">
                                            <span className="trace-msg-type" style={{ color: '#7c3aed' }}>AI</span>
                                            <div className="trace-tool-name">{tc.tool}</div>
                                            <pre className="trace-code">{JSON.stringify(tc.args, null, 2)}</pre>
                                          </div>
                                        ))
                                        if (msg.role === 'tool') {
                                          let prettyRaw = msg.raw ?? ''
                                          try {
                                            prettyRaw = JSON.stringify(JSON.parse(msg.raw ?? ''), null, 2)
                                          } catch {}
                                          const isSearch = msg.tool === 'search_report'
                                          let chunkCount = 0
                                          if (isSearch) {
                                            chunkCount = msg.chunks.length
                                            if (chunkCount === 0 && msg.raw) {
                                              try {
                                                const p = JSON.parse(msg.raw)
                                                chunkCount = Array.isArray(p?.results) ? p.results.length : 0
                                              } catch {}
                                            }
                                          }
                                          return (
                                            <div key={i} className="trace-msg">
                                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                                <span className="trace-msg-type" style={{ color: '#0d9488' }}>TOOL</span>
                                                {isSearch && <span className="trace-chunk-badge">{chunkCount} chunks</span>}
                                              </div>
                                              {prettyRaw && (
                                                <pre className="trace-code" style={{ maxHeight: 220, overflowY: 'auto', overflowX: 'hidden', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{prettyRaw}</pre>
                                              )}
                                            </div>
                                          )
                                        }
                                        return null
                                      })}
                                      {trace.display.answer && (
                                        <div className="trace-msg">
                                          <span className="trace-msg-type" style={{ color: '#4f46e5' }}>回答</span>
                                          <div className="trace-msg-content">{trace.display.answer}</div>
                                        </div>
                                      )}
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="detail-pdf-col" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
                      {/* Tab toggle */}
                      <div style={{ display: 'flex', gap: 4, padding: '8px 10px 6px', background: '#fff', borderBottom: '1px solid #e8edf3', flexShrink: 0 }}>
                        {(['pdf', 'chat'] as const).map(v => (
                          <button
                            key={v}
                            onClick={() => setRightView(v)}
                            style={{
                              display: 'inline-flex', alignItems: 'center', gap: 5,
                              padding: '4px 12px', borderRadius: 8, border: 'none', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                              background: rightView === v ? '#1a56db' : 'transparent',
                              color: rightView === v ? '#fff' : '#64748b',
                              transition: 'background 0.15s, color 0.15s',
                            }}
                          >
                            {v === 'pdf' ? <FileText size={12} /> : <MessageCircle size={12} />}
                            {v === 'pdf' ? 'PDF' : '聊天'}
                          </button>
                        ))}
                      </div>
                      {/* Content */}
                      <div style={{ flex: 1, overflow: 'hidden' }}>
                        {rightView === 'pdf' ? (
                          <iframe src={`${API_BASE}/api/documents/${selected.id}/file`} title={selected.filename} style={{ width: '100%', height: '100%', border: 'none' }} />
                        ) : (
                          <Suspense fallback={<div style={{ padding: 20, color: '#94a3b8', fontSize: 13 }}>載入中…</div>}>
                            <DocChat docId={selected.id} filename={selected.filename} />
                          </Suspense>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </main>

        {jobDrawerOpen && (
          <div
            className="summary-job-drawer"
            style={{
              position: 'absolute',
              top: 14,
              right: 14,
              bottom: 14,
              width: 360,
              background: 'rgba(255,255,255,0.94)',
              border: '1px solid rgba(207,219,232,0.95)',
              borderRadius: 24,
              display: 'flex',
              flexDirection: 'column',
              boxShadow: '0 18px 40px rgba(148,163,184,0.22)',
              zIndex: 10,
              backdropFilter: 'blur(18px)',
              overflow: 'hidden',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0 18px', height: 58, borderBottom: '1px solid rgba(236,241,247,0.95)', background: 'rgba(255,255,255,0.58)' }}>
              <span style={{ fontSize: 13, fontWeight: 700, color: '#163257' }}>
                工作佇列{activeJobs.length > 0 ? ` (${activeJobs.length} 筆進行中)` : ''}
              </span>
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  {doneJobs.length > 0 && (
                    <button
                      style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', height: 34, background: '#fff', border: '1px solid rgba(207,219,232,0.95)', borderRadius: 999, color: '#64748b', cursor: 'pointer', fontSize: 11, fontWeight: 600, padding: '0 12px', boxShadow: '0 8px 18px rgba(148,163,184,0.08)' }}
                      onClick={() => { if (window.confirm('要清除已完成的工作紀錄嗎？')) clearJobHistory().then(() => loadJobs()).catch(() => {}) }}
                    >
                      清除
                    </button>
                  )}
                <button
                  style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 34, height: 34, background: '#fff', border: '1px solid rgba(226,232,240,0.95)', borderRadius: 999, color: '#64748b', cursor: 'pointer', padding: 0 }}
                  onClick={() => setJobDrawerOpen(false)}
                  title="關閉"
                  aria-label="關閉工作佇列"
                >
                  <X size={16} />
                </button>
              </div>
            </div>
            <div style={{ overflowY: 'auto', flex: 1, padding: 6 }}>
              {jobs.length === 0 ? (
                <div style={{ padding: 24, textAlign: 'center', color: '#9ca3af', fontSize: 13 }}>目前沒有工作</div>
              ) : (
                <>
                  {activeJobs.map(renderJobRow)}
                  {activeJobs.length > 0 && doneJobs.length > 0 && <div style={{ borderTop: '1px solid #e5e7eb', margin: '4px 0' }} />}
                  {doneJobs.map(renderJobRow)}
                </>
              )}
            </div>
          </div>
        )}

        {chunkDocId && (
          <Suspense fallback={<div style={{ position: 'fixed', inset: 0, display: 'grid', placeItems: 'center', background: 'rgba(15,23,42,0.18)', zIndex: 220 }}>載入 chunks 中...</div>}>
            <ChunkViewer docId={chunkDocId} onClose={() => setChunkDocId(null)} />
          </Suspense>
        )}

        {parserCompareOpen && selected && (() => {
          const PARSER_KEYS = ['pymupdf4llm', 'azure_di', 'llamaparse'] as const
          const LABELS: Record<string, string> = { pymupdf4llm: 'PyMuPDF4LLM', azure_di: 'Azure DI', llamaparse: 'LlamaParse' }
          const maxPages = Math.max(0, ...Object.values(panelContents).map(c => c.pages.length))
          const panelCount = activePanels.size
          return (
            <div
              style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.22)', backdropFilter: 'blur(6px)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 240, padding: 16 }}
              onClick={() => setParserCompareOpen(false)}
            >
              <div
                style={{ width: 'min(1600px, 98vw)', height: 'min(900px, 94vh)', background: 'rgba(255,255,255,0.97)', border: '1px solid rgba(207,219,232,0.95)', borderRadius: 28, boxShadow: '0 28px 60px rgba(148,163,184,0.24)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
                onClick={e => e.stopPropagation()}
              >
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid rgba(226,232,240,0.92)', flexShrink: 0 }}>
                  <div>
                    <div style={{ fontSize: 16, fontWeight: 800, color: '#163257' }}>解析對比</div>
                    <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>{getDisplayTitle(selected.filename)}</div>
                  </div>

                  {/* Panel toggles */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                    {/* PDF toggle */}
                    <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, cursor: 'pointer', userSelect: 'none', color: '#334155' }}>
                      <input
                        type="checkbox"
                        checked={activePanels.has('pdf')}
                        onChange={() => togglePanel(selected.id, 'pdf')}
                        style={{ width: 14, height: 14, cursor: 'pointer' }}
                      />
                      PDF
                    </label>
                    {PARSER_KEYS.map(p => {
                      const info = parserCaches.find(c => c.parser === p)
                      const busy = parsingTabs.has(`${selected.id}_${p}`) || isParseQueuedOrRunning(selected.id, p)
                      return (
                        <label key={p} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, fontWeight: 600, cursor: info?.available ? 'pointer' : 'default', userSelect: 'none', opacity: info?.available ? 1 : 0.38, color: '#334155' }}>
                          <input
                            type="checkbox"
                            checked={activePanels.has(p)}
                            disabled={!info?.available}
                            onChange={() => togglePanel(selected.id, p)}
                            style={{ width: 14, height: 14, cursor: info?.available ? 'pointer' : 'default' }}
                          />
                          {LABELS[p]}
                          {info?.available && <span style={{ color: '#94a3b8', fontSize: 10, fontWeight: 400 }}>({(info.size / 1024).toFixed(0)}KB)</span>}
                          <button
                            onClick={e => { e.preventDefault(); !busy && handleParseWith(selected, p) }}
                            disabled={busy}
                            title={info?.available ? '重新解析' : '建立解析'}
                            style={{ padding: '2px 7px', fontSize: 10, fontWeight: 700, border: '1px solid #d1d5db', borderRadius: 999, background: '#f9fafb', cursor: busy ? 'not-allowed' : 'pointer', color: '#64748b' }}
                          >
                            {busy ? '…' : info?.available ? '↺' : '解析'}
                          </button>
                        </label>
                      )
                    })}
                    <button className="nbtn nbtn-sm" style={{ marginLeft: 8 }} onClick={() => setParserCompareOpen(false)}>關閉</button>
                  </div>
                </div>

                {/* Page navigation */}
                {maxPages > 1 && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 20px', borderBottom: '1px solid rgba(226,232,240,0.92)', flexShrink: 0, background: 'rgba(248,250,252,0.9)' }}>
                    <button
                      disabled={parserComparePage === 0}
                      onClick={() => handleComparePage(parserComparePage - 1)}
                      style={{ padding: '3px 10px', fontSize: 15, border: '1px solid #d1d5db', borderRadius: 999, background: '#fff', cursor: parserComparePage === 0 ? 'not-allowed' : 'pointer', color: '#374151', opacity: parserComparePage === 0 ? 0.4 : 1 }}
                    >‹</button>
                    <input
                      type="range"
                      min={0} max={maxPages - 1} value={parserComparePage}
                      onChange={e => handleComparePage(Number(e.target.value))}
                      style={{ flex: 1, cursor: 'pointer', accentColor: '#2563eb' }}
                    />
                    <button
                      disabled={parserComparePage >= maxPages - 1}
                      onClick={() => handleComparePage(parserComparePage + 1)}
                      style={{ padding: '3px 10px', fontSize: 15, border: '1px solid #d1d5db', borderRadius: 999, background: '#fff', cursor: parserComparePage >= maxPages - 1 ? 'not-allowed' : 'pointer', color: '#374151', opacity: parserComparePage >= maxPages - 1 ? 0.4 : 1 }}
                    >›</button>
                    <span style={{ fontSize: 12, color: '#64748b' }}>第</span>
                    {pageInputEditing ? (
                      <input
                        type="text" inputMode="numeric" autoFocus
                        value={pageInputValue}
                        onChange={e => setPageInputValue(e.target.value.replace(/\D/g, ''))}
                        onBlur={() => {
                          const n = Math.max(1, Math.min(maxPages, parseInt(pageInputValue) || 1))
                          handleComparePage(n - 1)
                          setPageInputEditing(false)
                        }}
                        onKeyDown={e => {
                          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
                          if (e.key === 'Escape') { setPageInputEditing(false); setPageInputValue(String(parserComparePage + 1)) }
                        }}
                        style={{ width: 48, fontSize: 13, fontWeight: 700, textAlign: 'center', padding: '3px 6px', border: '1px solid #93c5fd', borderRadius: 8, outline: 'none', color: '#1e3a8a' }}
                      />
                    ) : (
                      <span
                        onClick={() => { setPageInputEditing(true); setPageInputValue(String(parserComparePage + 1)) }}
                        title="點擊輸入頁碼"
                        style={{ fontSize: 13, fontWeight: 700, color: '#1e3a8a', minWidth: 32, textAlign: 'center', cursor: 'text', borderBottom: '2px dashed #93c5fd', paddingBottom: 1 }}
                      >
                        {parserComparePage + 1}
                      </span>
                    )}
                    <span style={{ fontSize: 12, color: '#64748b' }}>/ {maxPages} 頁</span>
                  </div>
                )}

                {/* Panels */}
                <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
                  {panelCount === 0 && (
                    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 13 }}>
                      請在上方勾選要顯示的面板
                    </div>
                  )}

                  {/* PDF panel */}
                  {activePanels.has('pdf') && (
                    <div style={{ flex: `0 0 ${Math.floor(100 / panelCount)}%`, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, borderRight: '1px solid rgba(226,232,240,0.92)' }}>
                      <div style={{ padding: '8px 14px', fontSize: 10.5, fontWeight: 800, color: '#374151', background: 'rgba(248,250,252,0.92)', borderBottom: '1px solid rgba(226,232,240,0.92)', textTransform: 'uppercase', letterSpacing: '0.06em', flexShrink: 0 }}>
                        PDF 原檔
                      </div>
                      <Suspense fallback={<div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#94a3b8', fontSize: 13 }}>載入中...</div>}>
                        <PdfPageViewer
                          url={`${API_BASE}/api/documents/${selected.id}/file`}
                          page={parserComparePage + 1}
                          onPageCount={n => { if (maxPages === 0) handleComparePage(0) }}
                        />
                      </Suspense>
                    </div>
                  )}

                  {/* Parser text panels */}
                  {PARSER_KEYS.filter(p => activePanels.has(p)).map((p, idx) => {
                    const content = panelContents[p]
                    return (
                      <div key={p} style={{ flex: `0 0 ${Math.floor(100 / panelCount)}%`, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0, borderLeft: idx > 0 || activePanels.has('pdf') ? '1px solid rgba(226,232,240,0.92)' : 'none' }}>
                        <div style={{ padding: '8px 14px', fontSize: 10.5, fontWeight: 800, color: '#374151', background: 'rgba(248,250,252,0.92)', borderBottom: '1px solid rgba(226,232,240,0.92)', textTransform: 'uppercase', letterSpacing: '0.06em', flexShrink: 0 }}>
                          {LABELS[p]}
                        </div>
                        <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px', minHeight: 0, background: '#fff' }}>
                          {!content || content.loading ? (
                            <span style={{ color: '#94a3b8', fontSize: 13 }}>載入中...</span>
                          ) : (
                            <pre style={{ margin: 0, fontSize: 12, lineHeight: 1.75, color: '#1e293b', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'ui-monospace, SFMono-Regular, Consolas, monospace' }}>
                              {content.pages[parserComparePage] ?? '（本頁無內容）'}
                            </pre>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          )
        })()}
      </div>
    </>
  )
}
