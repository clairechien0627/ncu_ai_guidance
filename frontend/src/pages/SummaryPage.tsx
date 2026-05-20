import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useJobStore, selectJobs } from '../stores/jobStore'
import { useJobSSE } from '../hooks/useJobSSE'
import { useDocumentList } from '../hooks/useDocumentList'
import { useInlineEdit } from '../hooks/useInlineEdit'
import { useParserCompare } from '../hooks/useParserCompare'
import { ArrowLeft, Clock3, FolderDown, MessageSquareMore, RefreshCw } from 'lucide-react'
import {
  batchImport,
  exportDocument,
  extractSummary,
  extractSummaryStep,
  getDocumentTraces,
  getJobs,
  getParserCaches,
  parseDocument,
  refreshAbstractText,
  reindexDocument,
  renameDocument,
  updateAbstractText,
  type ParserCacheInfo,
  type SummaryItem,
  type TraceItem,
} from '../api'
import { JobDrawer } from '../components/JobDrawer'
import { DocumentDetailPanel, type DocumentDetailCallbacks } from '../components/DocumentDetailPanel'
import { ParserCompareModal } from '../components/ParserCompareModal'
import { getCollegeInfo, getDisplayTitle, getStatusMeta } from '../utils/summaryUtils'

const BatchPage = lazy(() => import('./BatchPage'))
const ChunkViewer = lazy(() => import('../components/viewer/ChunkViewer'))

interface Props {
  onBack: () => void
}

export default function SummaryPage({ onBack }: Props) {
  // ── Document list ─────────────────────────────────────────────────────────
  const { items, setItems, loading, setLoading, search, setSearch, deptFilter, setDeptFilter, loadItems } = useDocumentList()
  // ── Inline editors ────────────────────────────────────────────────────────
  const abstractEdit = useInlineEdit()
  const titleEdit = useInlineEdit()
  // ── Parser compare modal ──────────────────────────────────────────────────
  const compare = useParserCompare()
  // ── Local UI state ────────────────────────────────────────────────────────
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [rightView, setRightView] = useState<'pdf' | 'chat'>('pdf')
  const [flash, setFlash] = useState<string | null>(null)
  const [batchPageOpen, setBatchPageOpen] = useState(false)
  const [jobDrawerOpen, setJobDrawerOpen] = useState(false)
  const jobs = useJobStore(selectJobs)
  const setJobs = useJobStore(s => s.setJobs)
  const [chunkDocId, setChunkDocId] = useState<number | null>(null)
  const [extracting, setExtracting] = useState<Set<number>>(new Set())
  const [extractingSteps, setExtractingSteps] = useState<Set<string>>(new Set())
  const [reindexing, setReindexing] = useState<Set<number>>(new Set())
  const [parserChoice, setParserChoice] = useState<Record<number, string>>({})
  const [parserCaches, setParserCaches] = useState<ParserCacheInfo[]>([])
  const [parserCachesLoaded, setParserCachesLoaded] = useState(false)
  const [parsingTabs, setParsingTabs] = useState<Set<string>>(new Set())
  const [docTraces, setDocTraces] = useState<TraceItem[]>([])
  const [tracesLoading, setTracesLoading] = useState(false)
  const [expandedTraceId, setExpandedTraceId] = useState<string | null>(null)
  const [narrowShowDetail, setNarrowShowDetail] = useState(false)
  const pollRef = useRef<number | null>(null)
  const selectedIdRef = useRef<number | null>(null)

  const flashMessage = (message: string) => {
    setFlash(message)
    window.setTimeout(() => setFlash(null), 3500)
  }

  const loadItemsAndSelect = useCallback(async () => {
    const data = await loadItems()
    setSelectedId(prev => (prev == null && data.length > 0 ? data[0].id : prev))
  }, [loadItems])

  const loadJobs = useCallback(async () => {
    const data = await getJobs()
    setJobs(data)
  }, [setJobs])

  const refreshAll = useCallback(async () => {
    setLoading(true)
    try {
      await Promise.all([loadItemsAndSelect(), loadJobs()])
    } finally {
      setLoading(false)
    }
  }, [loadItemsAndSelect, loadJobs])

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
        Promise.all([loadItemsAndSelect(), loadJobs()]).catch(() => {})
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

  useJobSSE({
    onItemsDone: () => loadItemsAndSelect().catch(() => {}),
    onParseDone: (docId) => {
      getParserCaches(docId).then(setParserCaches).catch(() => {})
    },
    onExtractDone: (docId) => {
      setTracesLoading(true)
      getDocumentTraces(docId)
        .then(data => { if (selectedIdRef.current === docId) setDocTraces(data) })
        .catch(() => { if (selectedIdRef.current === docId) setDocTraces([]) })
        .finally(() => { if (selectedIdRef.current === docId) setTracesLoading(false) })
    },
    selectedIdRef,
  })

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

  const selected = useMemo(
    () => filtered.find(item => item.id === selectedId) ?? items.find(item => item.id === selectedId) ?? null,
    [filtered, items, selectedId],
  )

  const isParseQueuedOrRunning = useCallback((docId: number, parser: string) => {
    return jobs.some(job =>
      job.doc_id === docId &&
      job.status !== 'done' &&
      job.job_type === `parse_${parser}`
    )
  }, [jobs])

  // Reset detail state when selection changes
  useEffect(() => {
    selectedIdRef.current = selectedId
    abstractEdit.cancelEdit()
    abstractEdit.setDraft('')
    setParserCaches([])
    setParserCachesLoaded(false)
    setDocTraces([])
    setExpandedTraceId(null)
    if (selectedId != null) {
      const capturedId = selectedId
      const item = items.find(i => i.id === capturedId)
      if (item) abstractEdit.setDraft(item.abstract_text ?? '')
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

  // ── Event handlers ────────────────────────────────────────────────────────

  const summaryCount = items.filter(item => item.summary).length

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
      setReindexing(prev => { const next = new Set(prev); next.delete(item.id); return next })
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
      setParsingTabs(prev => { const next = new Set(prev); next.delete(key); return next })
    }
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
      setExtracting(prev => { const next = new Set(prev); next.delete(item.id); return next })
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
      setExtractingSteps(prev => { const next = new Set(prev); next.delete(key); return next })
    }
  }

  const handleRefreshAbstract = async (item: SummaryItem) => {
    try {
      const result = await refreshAbstractText(item.id)
      setItems(prev => prev.map(entry => entry.id === item.id ? { ...entry, abstract_text: result.text } : entry))
      abstractEdit.setDraft(result.text ?? '')
      flashMessage(result.skipped ? '找不到可更新的摘要。' : '已重新整理 PDF 摘要。')
    } catch (error: any) {
      flashMessage(`更新摘要失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    }
  }

  const handleSaveTitle = async (item: SummaryItem) => {
    const trimmed = titleEdit.draft.trim()
    if (!trimmed) return
    titleEdit.setSaving(true)
    try {
      const { filename } = await renameDocument(item.id, trimmed)
      setItems(prev => prev.map(e => e.id === item.id ? { ...e, filename } : e))
      titleEdit.cancelEdit()
    } catch (err: any) {
      alert(err?.response?.data?.detail ?? '重新命名失敗')
    } finally {
      titleEdit.setSaving(false)
    }
  }

  const handleSaveAbstract = async (item: SummaryItem) => {
    abstractEdit.setSaving(true)
    try {
      await updateAbstractText(item.id, abstractEdit.draft)
      setItems(prev => prev.map(entry => entry.id === item.id ? { ...entry, abstract_text: abstractEdit.draft } : entry))
      abstractEdit.cancelEdit()
      flashMessage('摘要已儲存。')
    } catch (error: any) {
      flashMessage(`儲存摘要失敗：${error?.response?.data?.detail ?? error?.message ?? '未知錯誤'}`)
    } finally {
      abstractEdit.setSaving(false)
    }
  }

  const handleRefreshTraces = useCallback(() => {
    if (selectedId == null) return
    const capturedId = selectedId
    setTracesLoading(true)
    getDocumentTraces(capturedId)
      .then(data => { if (selectedIdRef.current === capturedId) setDocTraces(data) })
      .catch(() => { if (selectedIdRef.current === capturedId) setDocTraces([]) })
      .finally(() => { if (selectedIdRef.current === capturedId) setTracesLoading(false) })
  }, [selectedId])

  const detailCallbacks: DocumentDetailCallbacks = useMemo(() => ({
    onReindex: handleReindex,
    onParseWith: handleParseWith,
    onOpenCompare: (item) => compare.openCompare(item.id, parserCaches),
    onViewChunks: (docId) => setChunkDocId(docId),
    onExtract: handleExtract,
    onExtractStep: handleExtractStep,
    onRefreshAbstract: handleRefreshAbstract,
    onSaveTitle: handleSaveTitle,
    onSaveAbstract: handleSaveAbstract,
    onExport: (item) => exportDocument(item.id, item.filename).catch(() => flashMessage('匯出失敗。')),
    onRefreshTraces: handleRefreshTraces,
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [compare.openCompare, parserCaches, handleRefreshTraces])

  const activeJobCount = jobs.filter(j => j.status === 'queued' || j.status === 'running').length

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
                {activeJobCount > 0 && <span className="nav-job-badge">{activeJobCount}</span>}
              </button>
            </div>
          </div>
        </nav>

        <main className="ncupage-main">
          <div className="ncupage-container" data-narrow-detail={narrowShowDetail ? 'true' : 'false'}>
            {/* ── Left rail: document list ── */}
            <div className="ncupage-left-rail">
              <div className="ncupage-filters">
                <div className="ncupage-filters-left" />
                <div className="ncupage-filters-right">
                  <input className="ncu-search" placeholder="搜尋標題、摘要、計畫編號…" value={search} onChange={e => setSearch(e.target.value)} />
                  <select className="ncu-select" value={deptFilter} onChange={e => setDeptFilter(e.target.value)}>
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
                    <div
                      key={item.id}
                      className={`project-card${selectedId === item.id ? ' pc-active' : ''}`}
                      onClick={() => { setSelectedId(item.id); setRightView('pdf'); titleEdit.cancelEdit(); setNarrowShowDetail(true) }}
                    >
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
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>

            {/* ── Right split: detail panel ── */}
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
                  <DocumentDetailPanel
                    selected={selected}
                    titleEdit={titleEdit}
                    abstractEdit={abstractEdit}
                    parserCaches={parserCaches}
                    parserCachesLoaded={parserCachesLoaded}
                    parserChoice={parserChoice}
                    onParserChoiceChange={(docId, parser) => setParserChoice(prev => ({ ...prev, [docId]: parser }))}
                    parsingTabs={parsingTabs}
                    isParseQueuedOrRunning={isParseQueuedOrRunning}
                    extracting={extracting}
                    extractingSteps={extractingSteps}
                    reindexing={reindexing}
                    docTraces={docTraces}
                    tracesLoading={tracesLoading}
                    expandedTraceId={expandedTraceId}
                    onExpandTrace={setExpandedTraceId}
                    rightView={rightView}
                    onSetRightView={setRightView}
                    callbacks={detailCallbacks}
                  />
                )}
              </div>
            </div>
          </div>
        </main>

        <JobDrawer
          open={jobDrawerOpen}
          onClose={() => setJobDrawerOpen(false)}
          onRefreshJobs={loadJobs}
          onRefreshItems={() => loadItemsAndSelect().catch(() => {})}
          setParsingTabs={setParsingTabs}
        />

        {chunkDocId && (
          <Suspense fallback={<div style={{ position: 'fixed', inset: 0, display: 'grid', placeItems: 'center', background: 'rgba(15,23,42,0.18)', zIndex: 220 }}>載入 chunks 中...</div>}>
            <ChunkViewer docId={chunkDocId} onClose={() => setChunkDocId(null)} />
          </Suspense>
        )}

        {compare.open && selected && (
          <ParserCompareModal
            {...compare}
            selected={selected}
            parserCaches={parserCaches}
            parsingTabs={parsingTabs}
            isParseQueuedOrRunning={isParseQueuedOrRunning}
            onParseWith={handleParseWith}
          />
        )}
      </div>
    </>
  )
}
