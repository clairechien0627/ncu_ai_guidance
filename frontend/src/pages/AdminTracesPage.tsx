import { Fragment, type ReactNode, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertTriangle, ArrowLeft, CheckCheck, ChevronDown, ChevronRight, Clock, PanelLeftClose, PanelLeftOpen, RefreshCw, Save, X } from 'lucide-react'
import {
  batchScoreTraces,
  compareTraceVersions,
  getPromptList,
  getSlowRuns,
  getTraceErrors,
  getTraceStats,
  getTraces,
  getTracesByPrompt,
  getTracesByPromptVersion,
  getTraceDetail,
  getTraceTimeline,
  runEval,
  testRoute,
  updateTraceFeedback,
} from '../api'
import type {
  EvalReport,
  PromptInfo,
  TimelinePoint,
  TraceDetail,
  TraceEvent,
  TraceGroupStats,
  TraceItem,
  TraceStats,
  VersionCompareResult,
} from '../api'

interface Props {
  onBack: () => void
}

// ── Filter state ───────────────────────────────────────────────────────────────

type FilterState = {
  prompt: string
  version: string
  status: string
  latency: string
  originalIntent: string
  resolvedIntent: string
  handoffOnly: boolean
}

const FILTER_DEFAULT: FilterState = {
  prompt: 'all',
  version: 'all',
  status: 'all',
  latency: '',
  originalIntent: 'all',
  resolvedIntent: 'all',
  handoffOnly: false,
}

function filterReducer(state: FilterState, action: Partial<FilterState> | 'reset'): FilterState {
  if (action === 'reset') return FILTER_DEFAULT
  return { ...state, ...action }
}

/** Convert "sha256:abc123def456" → "#abc123" for compact display. */
function fmtVersion(v: string | null | undefined): string {
  if (!v) return '-'
  const hash = v.startsWith('sha256:') ? v.slice(7) : v
  return `#${hash.slice(0, 6)}`
}

function fmt(value: number | null | undefined, suffix = '') {
  if (value === null || value === undefined) return '-'
  return `${value}${suffix}`
}

function timeLabel(value: string | null | undefined) {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-TW', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatJson(value: unknown) {
  if (value === null || value === undefined) return '-'
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function CollapsiblePanel({
  id,
  title,
  summary,
  headerExtra,
  collapsed,
  onToggle,
  collapsible = true,
  className = '',
  children,
}: {
  id: string
  title: string
  summary?: ReactNode
  headerExtra?: ReactNode
  collapsed: boolean
  onToggle: (id: string) => void
  collapsible?: boolean
  className?: string
  children: ReactNode
}) {
  const isCollapsed = collapsible && collapsed
  const titleContent = (
    <span className="trace-panel-title">
      {collapsible && (isCollapsed ? <ChevronRight size={14} /> : <ChevronDown size={14} />)}
      <span>{title}</span>
    </span>
  )
  return (
    <section className={`trace-panel ${isCollapsed ? 'trace-panel-collapsed' : ''} ${className}`} data-panel-id={id}>
      <div className="trace-panel-head">
        {collapsible ? (
          <button className="trace-panel-head-button" onClick={() => onToggle(id)} type="button">
            {titleContent}
            {summary && <span className="trace-panel-summary">{summary}</span>}
          </button>
        ) : (
          <div className="trace-panel-head-button trace-panel-head-static">
            {titleContent}
            {summary && <span className="trace-panel-summary">{summary}</span>}
          </div>
        )}
        {headerExtra && (
          <span className="trace-panel-header-extra" onClick={e => e.stopPropagation()}>
            {headerExtra}
          </span>
        )}
      </div>
      {!isCollapsed && children}
    </section>
  )
}

const EMPTY_TRACES: TraceItem[] = []

export default function AdminTracesPage({ onBack }: Props) {
  const queryClient = useQueryClient()

  const refreshAll = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['trace-stats'] })
    queryClient.invalidateQueries({ queryKey: ['traces-by-prompt'] })
    queryClient.invalidateQueries({ queryKey: ['traces-by-version'] })
    queryClient.invalidateQueries({ queryKey: ['trace-errors'] })
    queryClient.invalidateQueries({ queryKey: ['slow-runs'] })
    queryClient.invalidateQueries({ queryKey: ['prompt-list'] })
    queryClient.invalidateQueries({ queryKey: ['timeline'] })
    queryClient.invalidateQueries({ queryKey: ['traces'] })
  }, [queryClient])

  // ── Filter state ───────────────────────────────────────────────────────────
  const [filters, dispatchFilter] = useReducer(filterReducer, FILTER_DEFAULT)
  const { prompt: filterPrompt, version: filterVersion,
          status: filterStatus, latency: filterLatency,
          originalIntent: filterOriginalIntent, resolvedIntent: filterResolvedIntent,
          handoffOnly: filterHandoffOnly } = filters
  const [filtersOpen, setFiltersOpen] = useState(false)

  // ── Trace pagination (load-more appends to baseTraces from query) ──────────
  const [appendedTraces, setAppendedTraces] = useState<TraceItem[]>([])
  const [traceOffset, setTraceOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)

  // ── Detail / UI state ──────────────────────────────────────────────────────
  const [selectedTrace, setSelectedTrace] = useState<TraceDetail | null>(null)
  const [detailTab, setDetailTab] = useState<'overview' | 'events' | 'steps' | 'evidence' | 'raw'>('overview')
  const [feedbackScore, setFeedbackScore] = useState('')
  const [feedbackText, setFeedbackText] = useState('')
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // ── 新增功能 state ──────────────────────────────────────────────────────────
  const [timelinePrompt, setTimelinePrompt] = useState('all')
  const [compareV1, setCompareV1] = useState('')
  const [compareV2, setCompareV2] = useState('')
  const [compareResult, setCompareResult] = useState<VersionCompareResult | null>(null)
  const [compareLoading, setCompareLoading] = useState(false)
  const [batchScoring, setBatchScoring] = useState(false)
  const [batchMessage, setBatchMessage] = useState<string | null>(null)
  const [routeTestMsg, setRouteTestMsg] = useState('')
  const [routeTestResult, setRouteTestResult] = useState<import('../api').TestRouteResult | null>(null)
  const [routeTestLoading, setRouteTestLoading] = useState(false)
  const [recentSearch, setRecentSearch] = useState('')
  const [evalResult, setEvalResult] = useState<EvalReport | null>(null)
  const [evalLoading, setEvalLoading] = useState(false)
  const [batchLimit, setBatchLimit] = useState(20)
  const [visibleToolPanels, setVisibleToolPanels] = useState<Set<string>>(
    () => new Set(['timeline', 'promptCompare', 'batchScore', 'routeTest', 'promptList', 'eval']),
  )
  const [toolsPickerOpen, setToolsPickerOpen] = useState(true)
  const [collapsedCards, setCollapsedCards] = useState<Set<string>>(
    () => new Set([
      'byPrompt', 'byPromptVersion', 'errors', 'slowRuns',
      'promptCompare', 'batchScore', 'routeTest', 'promptList', 'eval',
    ]),
  )
  const [workbenchTab, setWorkbenchTab] = useState<'monitor' | 'tools'>('monitor')

  // ── Server queries ─────────────────────────────────────────────────────────

  const { data: stats = null, isPending: statsLoading } = useQuery({
    queryKey: ['trace-stats'],
    queryFn: getTraceStats,
  })
  const { data: byPrompt = [] } = useQuery({ queryKey: ['traces-by-prompt'], queryFn: getTracesByPrompt })
  const { data: byPromptVersion = [] } = useQuery({ queryKey: ['traces-by-version'], queryFn: getTracesByPromptVersion })
  const { data: errors = [] } = useQuery({ queryKey: ['trace-errors'], queryFn: () => getTraceErrors(20) })
  const { data: slowRuns = [] } = useQuery({ queryKey: ['slow-runs'], queryFn: () => getSlowRuns(20, 10) })
  const { data: promptList = [] } = useQuery({ queryKey: ['prompt-list'], queryFn: getPromptList })
  const { data: timeline = [] } = useQuery({
    queryKey: ['timeline', timelinePrompt],
    queryFn: () => getTraceTimeline(timelinePrompt === 'all' ? undefined : timelinePrompt, undefined, 14),
  })

  // Filter-dependent trace list — resets pagination when filters change.
  const traceFilters = useMemo(() => ({
    prompt_name: filterPrompt === 'all' ? undefined : filterPrompt,
    prompt_version: filterVersion === 'all' ? undefined : filterVersion,
    status: filterStatus === 'all' ? undefined : filterStatus,
    min_latency: filterLatency.trim() || undefined,
    original_intent: filterHandoffOnly
      ? 'retrieval_handoff'
      : filterOriginalIntent === 'all' ? undefined : filterOriginalIntent,
    resolved_intent: filterResolvedIntent === 'all' ? undefined : filterResolvedIntent,
  }), [filterPrompt, filterVersion, filterStatus, filterLatency,
       filterHandoffOnly, filterOriginalIntent, filterResolvedIntent])

  const { data: baseTraces = EMPTY_TRACES, isPending: tracesLoading, isError: tracesError } = useQuery({
    queryKey: ['traces', traceFilters],
    queryFn: () => getTraces(30, { ...traceFilters, offset: 0 }),
  })

  const loading = statsLoading || tracesLoading

  // Reset loadMore state whenever the filter query returns fresh data.
  const prevBaseRef = useRef(baseTraces)
  useEffect(() => {
    if (prevBaseRef.current === baseTraces) return
    prevBaseRef.current = baseTraces
    setAppendedTraces([])
    setTraceOffset(0)
    setHasMore(baseTraces.length === 30)
  }, [baseTraces])

  const recent = useMemo(
    () => [...baseTraces, ...appendedTraces],
    [baseTraces, appendedTraces],
  )

  const topPrompt = useMemo(
    () => byPrompt.find((row) => row.key !== 'unknown')?.key ?? byPrompt[0]?.key ?? '-',
    [byPrompt],
  )

  const promptVersionList = useMemo(() => {
    const versions: string[] = []
    byPromptVersion.forEach((row) => {
      const at = row.key.lastIndexOf('@')
      const v = at >= 0 ? row.key.slice(at + 1) : row.key
      if (!versions.includes(v)) versions.push(v)
    })
    return versions
  }, [byPromptVersion])

  const promptVersionOptions = useMemo(() => {
    const versions = new Set<string>()
    byPromptVersion.forEach((row) => {
      const at = row.key.lastIndexOf('@')
      versions.add(at >= 0 ? row.key.slice(at + 1) : row.key)
    })
    return Array.from(versions)
  }, [byPromptVersion])

  const filteredRecent = useMemo(() => {
    if (!recentSearch.trim()) return recent
    const q = recentSearch.toLowerCase()
    return recent.filter(r =>
      (r.input ?? '').toLowerCase().includes(q) ||
      (r.output ?? '').toLowerCase().includes(q) ||
      (r.agent_name ?? '').toLowerCase().includes(q) ||
      (r.mode ?? '').toLowerCase().includes(q),
    )
  }, [recent, recentSearch])

  const toolPanelOptions = useMemo(() => ([
    { id: 'timeline', label: '品質走勢', summary: `${timeline.length} days` },
    { id: 'promptCompare', label: 'Prompt 版本比較', summary: compareResult ? '已有比較結果' : '選擇兩個版本' },
    { id: 'batchScore', label: '批次自動評分', summary: batchMessage ?? `最近 ${batchLimit} 條` },
    { id: 'routeTest', label: '路由測試', summary: routeTestResult ? routeTestResult.intent : '測試 router' },
    { id: 'promptList', label: 'Prompt 列表', summary: `${promptList.length} prompts` },
    { id: 'eval', label: '路由準確性評估', summary: evalResult ? `${(evalResult.accuracy * 100).toFixed(1)}%` : '尚未執行' },
  ]), [batchLimit, batchMessage, compareResult, evalResult, promptList.length, routeTestResult, timeline.length])

  const selectedPromptStack = useMemo(() => {
    const stack = selectedTrace?.prompt_stack_json
    if (!stack) return '-'
    if (Array.isArray(stack)) {
      return stack.map(prompt => prompt.name).join(' + ')
    }
    return stack
  }, [selectedTrace])

  const clearFilters = useCallback(() => dispatchFilter('reset'), [])

  const isCardCollapsed = useCallback((id: string) => collapsedCards.has(id), [collapsedCards])

  const toggleCard = useCallback((id: string) => {
    setCollapsedCards(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const toggleToolPanel = useCallback((id: string) => {
    setVisibleToolPanels(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const loadMore = useCallback(async () => {
    const nextOffset = traceOffset + 30
    try {
      const more = await getTraces(30, { ...traceFilters, offset: nextOffset })
      setAppendedTraces(prev => [...prev, ...more])
      setHasMore(more.length === 30)
      setTraceOffset(nextOffset)
    } catch {
      /* ignore */
    }
  }, [traceFilters, traceOffset])

  const openTrace = useCallback(async (runId: string) => {
    setDetailLoading(true)
    setDetailTab('overview')
    setError(null)
    try {
      const detail = await getTraceDetail(runId)
      setSelectedTrace(detail)
      setFeedbackScore(detail.quality_score === null || detail.quality_score === undefined ? '' : String(detail.quality_score))
      setFeedbackText(detail.user_feedback ?? '')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load trace detail')
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const saveFeedback = useCallback(async () => {
    if (!selectedTrace) return
    const parsedScore = feedbackScore.trim() === '' ? null : Number(feedbackScore)
    if (parsedScore !== null && (!Number.isFinite(parsedScore) || parsedScore < 0 || parsedScore > 5)) {
      setError('Quality score must be between 0 and 5')
      return
    }
    setDetailLoading(true)
    setError(null)
    try {
      const updated = await updateTraceFeedback(selectedTrace.id, {
        quality_score: parsedScore,
        user_feedback: feedbackText.trim() || null,
      })
      setSelectedTrace(updated)
      refreshAll()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save feedback')
    } finally {
      setDetailLoading(false)
    }
  }, [feedbackScore, feedbackText, refreshAll, selectedTrace])

  const handleBatchScore = async () => {
    setBatchScoring(true)
    setBatchMessage(null)
    try {
      const res = await batchScoreTraces(batchLimit)
      setBatchMessage(res.message)
    } catch {
      setBatchMessage('批次評分啟動失敗')
    } finally {
      setBatchScoring(false)
    }
  }

  const handleCompare = async () => {
    if (!compareV1 || !compareV2) return
    setCompareLoading(true)
    try {
      const res = await compareTraceVersions(compareV1, compareV2)
      setCompareResult(res)
    } catch {
      setError('版本比較失敗')
    } finally {
      setCompareLoading(false)
    }
  }

  const handleTestRoute = async () => {
    if (!routeTestMsg.trim()) return
    setRouteTestLoading(true)
    setRouteTestResult(null)
    try {
      const res = await testRoute(routeTestMsg.trim())
      setRouteTestResult(res)
    } catch {
      setError('路由測試失敗')
    } finally {
      setRouteTestLoading(false)
    }
  }

  const handleRunEval = async () => {
    setEvalLoading(true)
    setEvalResult(null)
    try {
      const res = await runEval()
      setEvalResult(res)
    } catch {
      setError('評估執行失敗')
    } finally {
      setEvalLoading(false)
    }
  }

  return (
    <div className="admin-page">
      <header className="admin-header">
        <button className="admin-back-btn" onClick={onBack} title="Back">
          <ArrowLeft size={16} />
        </button>
        <div>
          <h1>Trace Monitor</h1>
          <p>Operational telemetry</p>
        </div>
        <button className="admin-refresh-btn" onClick={refreshAll} disabled={loading}>
          <RefreshCw size={15} className={loading ? 'spin-icon' : ''} />
          <span>Refresh</span>
        </button>
      </header>

      {error && (
        <div className="admin-error">
          <AlertTriangle size={15} />
          {error}
        </div>
      )}

      {/* Tab bar + 輕量摘要 */}
      <div className="trace-wb-bar">
        <div className="trace-wb-tabs">
          <button className={`trace-wb-tab${workbenchTab === 'monitor' ? ' active' : ''}`} onClick={() => setWorkbenchTab('monitor')}>
            監控
          </button>
          <button className={`trace-wb-tab${workbenchTab === 'tools' ? ' active' : ''}`} onClick={() => setWorkbenchTab('tools')}>
            工具 &amp; 分析
          </button>
        </div>
        {stats && (
          <div className="trace-wb-stats">
            <span>{stats.total_runs} 筆</span>
            {stats.error_runs > 0 && <span className="err">{stats.error_runs} 錯誤</span>}
            {stats.avg_latency != null && <span>{Math.round(stats.avg_latency)}s 均</span>}
          </div>
        )}
      </div>

            {/* ── 監控 tab ── */}
      {workbenchTab === 'monitor' && (
        <Fragment>
          {/* 可收折篩選列 */}
          <div className="trace-filter-bar">
            <button className="trace-filter-toggle" onClick={() => setFiltersOpen(p => !p)}>
              {filtersOpen ? '▲' : '▼'} 篩選
              {(filterPrompt !== 'all' || filterVersion !== 'all' ||
                filterStatus !== 'all' || filterLatency || filterHandoffOnly) && (
                <span className="trace-filter-badge">已篩選</span>
              )}
            </button>
            {filtersOpen && (
              <div className="trace-filters">
                <label>Prompt<select value={filterPrompt} onChange={(e) => dispatchFilter({ prompt: e.target.value })}>
                  <option value="all">All</option>
                  {byPrompt.map((row) => <option key={row.key} value={row.key}>{row.key}</option>)}
                </select></label>
                <label>Version<select value={filterVersion} onChange={(e) => dispatchFilter({ version: e.target.value })}>
                  <option value="all">All</option>
                  {promptVersionOptions.map((v) => <option key={v} value={v}>{v}</option>)}
                </select></label>
                <label>Status<select value={filterStatus} onChange={(e) => dispatchFilter({ status: e.target.value })}>
                  <option value="all">All</option>
                  <option value="success">Success</option>
                  <option value="error">Error</option>
                </select></label>
                <label>Min latency<input value={filterLatency} onChange={(e) => dispatchFilter({ latency: e.target.value })} placeholder="seconds" inputMode="decimal" /></label>
                <label title="original 與 resolved 不同代表請求被自動升級（如 retrieval → research）">
                  <span style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <input type="checkbox" checked={filterHandoffOnly} onChange={e => dispatchFilter({ handoffOnly: e.target.checked })} style={{ width: 13, height: 13 }} />
                    只看路由升級
                  </span>
                </label>
                <button onClick={clearFilters}>清除</button>
              </div>
            )}
          </div>

                    <main className="trace-workbench">
            {/* LEFT: Recent Runs 優先，統計折疊在下 */}
            <section className="trace-workbench-left">
              <div className="trace-grid-stack">
                <CollapsiblePanel id="recentRuns" title="Recent Runs" summary={`${filteredRecent.length} shown`} collapsed={isCardCollapsed('recentRuns')} onToggle={toggleCard} className="">
                  <div className="trace-panel-body trace-panel-body-compact">
                    <input className="trace-search-input" value={recentSearch} onChange={e => setRecentSearch(e.target.value)} placeholder="搜尋輸入/輸出/agent…" />
                  </div>
                  <div className="trace-recent-scroll">
                    <TraceList rows={filteredRecent} onSelect={openTrace} selectedId={selectedTrace?.id} />
                  </div>
                  {hasMore && <div className="trace-load-more"><button onClick={loadMore}>載入更多</button></div>}
                </CollapsiblePanel>

                <CollapsiblePanel id="errors" title="Errors" summary={`${errors.length} runs`} collapsed={isCardCollapsed('errors')} onToggle={toggleCard}>
                  <div className="trace-table-scroll trace-table-scroll-sm">
                    <TraceList rows={errors} onSelect={openTrace} selectedId={selectedTrace?.id} />
                  </div>
                </CollapsiblePanel>

                <CollapsiblePanel id="slowRuns" title="Slow Runs" summary={`${slowRuns.length} runs`} collapsed={isCardCollapsed('slowRuns')} onToggle={toggleCard}>
                  <div className="trace-table-scroll trace-table-scroll-sm">
                    <TraceList rows={slowRuns} onSelect={openTrace} selectedId={selectedTrace?.id} />
                  </div>
                </CollapsiblePanel>

                <CollapsiblePanel id="byPrompt" title="By Prompt" summary={`${byPrompt.length} prompts`} collapsed={isCardCollapsed('byPrompt')} onToggle={toggleCard}>
                  <div className="trace-table-scroll trace-table-scroll-sm"><TraceGroupTable rows={byPrompt} /></div>
                </CollapsiblePanel>

                <CollapsiblePanel id="byPromptVersion" title="By Prompt Version" summary={`${byPromptVersion.length} versions`} collapsed={isCardCollapsed('byPromptVersion')} onToggle={toggleCard}>
                  <div className="trace-table-scroll trace-table-scroll-sm"><TraceGroupTable rows={byPromptVersion} /></div>
                </CollapsiblePanel>
              </div>
            </section>

            {/* RIGHT: Detail panel 或 empty state */}
            <section className="trace-workbench-right">
              {selectedTrace ? (
                <section className="trace-detail-panel">
                  <div className="trace-detail-head">
                    <div>
                      <h2>Trace Detail</h2>
                      <p style={{ fontSize: 11, fontFamily: 'monospace', color: '#94a3b8', marginTop: 2 }}>{selectedTrace.id}</p>
                    </div>
                    <button className="trace-detail-close" onClick={() => setSelectedTrace(null)} title="Close"><X size={15} /></button>
                  </div>

                  <div className="trace-tabs">
                    {(['overview', 'events', 'steps', 'evidence', 'raw'] as const).map(tab => {
                      const labels: Record<string, string> = { overview: '總覽', events: '事件流', steps: '研究步驟', evidence: '證據', raw: '原始 JSON' }
                      const rd = selectedTrace.display as ResearchDisplay | null
                      if (tab === 'events' && !(rd?.events?.length)) return null
                      if (tab === 'steps' && !rd?.trace_summary?.steps?.length) return null
                      if (tab === 'evidence' && !rd?.trace_summary) return null
                      return <button key={tab} className={`trace-tab-btn${detailTab === tab ? ' active' : ''}`} onClick={() => setDetailTab(tab)}>{labels[tab]}</button>
                    })}
                  </div>

                  {detailTab === 'overview' && (
                    <div className="trace-tab-pane" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                      <div className="trace-detail-grid">
                        <div className="trace-detail-card" title={selectedTrace.mode ?? undefined}><span>Mode</span><strong>{selectedTrace.mode ?? '-'}</strong></div>
                        <div className="trace-detail-card" title={selectedTrace.agent_name ?? undefined}><span>Agent</span><strong>{selectedTrace.agent_name ?? '-'}</strong></div>
                        <div className="trace-detail-card" title={selectedTrace.prompt_name ?? undefined}><span>Prompt</span><strong>{selectedTrace.prompt_name ?? '-'}</strong></div>
                        <div className="trace-detail-card" title={selectedTrace.prompt_version ?? undefined}><span>Version</span><strong>{fmtVersion(selectedTrace.prompt_version)}</strong></div>
                        {(selectedTrace.original_intent || selectedTrace.resolved_intent) && (
                          <div className="trace-detail-card" style={{ gridColumn: '1 / -1' }}>
                            <span>意圖路由</span>
                            <strong>
                              {selectedTrace.original_intent ?? '-'}
                              {selectedTrace.original_intent !== selectedTrace.resolved_intent && (
                                <span className="trace-route-result-intent-arrow">→ {selectedTrace.resolved_intent}</span>
                              )}
                            </strong>
                          </div>
                        )}
                        <div className="trace-detail-card" title={selectedTrace.prompt_stack_name ?? undefined}><span>Stack</span><strong>{selectedTrace.prompt_stack_name ?? '-'}</strong></div>
                        <div className="trace-detail-card" title={selectedPromptStack}><span>Stack Prompts</span><strong>{selectedPromptStack}</strong></div>
                        <div className="trace-detail-card"><span>Stack Tokens</span><strong>{selectedTrace.prompt_stack_tokens ?? '-'}</strong></div>
                        <div className="trace-detail-card" title={fmt(selectedTrace.latency, 's')}><span>Latency</span><strong>{fmt(selectedTrace.latency, 's')}</strong></div>
                        <div className="trace-detail-card"><span>Tokens</span><strong>{(selectedTrace.prompt_tokens ?? 0) + (selectedTrace.completion_tokens ?? 0)}</strong></div>
                      </div>
                      {selectedTrace.quality_detail && (
                        <div className="trace-quality-detail">
                          <p className="trace-quality-detail-title">品質細項</p>
                          {(['grounding', 'completeness', 'source_quality', 'format_fit', 'limitations_honesty'] as const).map(k => {
                            const qLabels: Record<string, string> = { grounding: '接地性', completeness: '完整性', source_quality: '來源品質', format_fit: '格式適切', limitations_honesty: '限制誠實' }
                            const v = selectedTrace.quality_detail?.[k]
                            if (v == null) return null
                            return (
                              <div key={k} className="trace-quality-bar-row">
                                <span className="trace-quality-bar-label">{qLabels[k]}</span>
                                <div className="trace-quality-bar-track"><div className={`trace-quality-bar-fill ${v >= 4 ? 'high' : v >= 2.5 ? 'mid' : 'low'}`} style={{ width: `${(v / 5) * 100}%` }} /></div>
                                <span className="trace-quality-bar-value">{v.toFixed(1)}</span>
                              </div>
                            )
                          })}
                          {(selectedTrace.quality_detail.issues ?? []).length > 0 && (
                            <div className="trace-quality-issues">
                              {selectedTrace.quality_detail.issues!.map((issue, i) => <span key={i}>• {issue}</span>)}
                            </div>
                          )}
                        </div>
                      )}
                      <div className="trace-feedback">
                        <label>Quality score<input value={feedbackScore} onChange={(e) => setFeedbackScore(e.target.value)} placeholder="0-5" inputMode="decimal" /></label>
                        <label>Feedback<textarea value={feedbackText} onChange={(e) => setFeedbackText(e.target.value)} placeholder="What should be improved?" /></label>
                        <button onClick={saveFeedback} disabled={detailLoading}><Save size={14} />Save</button>
                      </div>
                      {selectedTrace.error && <div className="trace-detail-error"><AlertTriangle size={14} />{selectedTrace.error}</div>}
                      {selectedTrace.children && selectedTrace.children.length > 0 && (
                        <div className="trace-child-runs"><h3>Child Runs</h3><TraceList rows={selectedTrace.children} onSelect={openTrace} selectedId={selectedTrace.id} /></div>
                      )}
                    </div>
                  )}
                  {detailTab === 'events' && <div className="trace-tab-pane"><TraceEventPanel events={(selectedTrace.display as ResearchDisplay | null)?.events ?? []} /></div>}
                  {detailTab === 'steps' && <div className="trace-tab-pane"><ResearchTracePanel display={selectedTrace.display as ResearchDisplay | null} /></div>}
                  {detailTab === 'evidence' && <div className="trace-tab-pane"><EvidencePanel display={selectedTrace.display as ResearchDisplay | null} /></div>}
                  {detailTab === 'raw' && (
                    <div className="trace-detail-columns">
                      <div><h3>Input</h3><pre>{formatJson(selectedTrace.inputs_raw)}</pre></div>
                      <div><h3>Raw Output</h3><pre>{formatJson(selectedTrace.outputs_raw)}</pre></div>
                    </div>
                  )}
                </section>
              ) : (
                <div className="trace-detail-empty">
                  <div className="trace-detail-empty-inner">
                    <Activity size={28} />
                    <p>從左側點選一筆 trace 查看詳情</p>
                  </div>
                </div>
              )}
            </section>
          </main>
        </Fragment>
      )}

      {/* ── 工具 & 分析 tab ── */}
      {workbenchTab === 'tools' && (
        <div className={`trace-tools-page${toolsPickerOpen ? '' : ' tools-picker-collapsed'}`}>
          <aside className={`trace-tools-picker${toolsPickerOpen ? '' : ' is-collapsed'}`}>
            <div className="trace-tools-picker-head">
              <span className="trace-tools-picker-title">顯示面板</span>
              <div className="trace-tools-picker-actions">
                <button
                  className="trace-tools-picker-icon-btn"
                  type="button"
                  title={toolsPickerOpen ? '收起面板' : '展開面板'}
                  onClick={() => setToolsPickerOpen(open => !open)}
                >
                  {toolsPickerOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}
                </button>
                {toolsPickerOpen && (
                  <button
                    className="trace-tools-picker-all-btn"
                    type="button"
                    onClick={() => setVisibleToolPanels(new Set(toolPanelOptions.map(item => item.id)))}
                  >
                    <CheckCheck size={14} />
                    <span>全選</span>
                  </button>
                )}
              </div>
            </div>
            {toolsPickerOpen && (
              <>
                <div className="trace-tools-picker-list">
                  {toolPanelOptions.map(item => (
                    <label key={item.id} className="trace-tools-picker-item">
                      <input
                        type="checkbox"
                        checked={visibleToolPanels.has(item.id)}
                        onChange={() => toggleToolPanel(item.id)}
                      />
                      <span>
                        <strong>{item.label}</strong>
                        <small>{item.summary}</small>
                      </span>
                    </label>
                  ))}
                </div>
                <button
                  className="trace-tools-picker-reset"
                  type="button"
                  onClick={() => setVisibleToolPanels(new Set(['timeline', 'promptCompare', 'batchScore', 'routeTest']))}
                >
                  精簡工作台
                </button>
              </>
            )}
          </aside>

          <div className="trace-tools-scroll">
            <div className="trace-tools-grid">
            {visibleToolPanels.has('timeline') && (
            <CollapsiblePanel
              id="timeline"
              title="品質走勢（14 天）"
              summary={`${timeline.length} days`}
              headerExtra={
                <select className="trace-head-select" value={timelinePrompt} onChange={e => setTimelinePrompt(e.target.value)}>
                  <option value="all">所有 Prompt</option>
                  {byPrompt.map(p => <option key={p.key} value={p.key}>{p.key}</option>)}
                </select>
              }
              collapsed={false}
              onToggle={toggleCard}
              collapsible={false}
              className="trace-tools-wide"
            >
              <TimelineChart data={timeline} />
            </CollapsiblePanel>
            )}

            {visibleToolPanels.has('promptCompare') && (
            <CollapsiblePanel id="promptCompare" title="Prompt 版本比較" summary={compareResult ? '已有比較結果' : '選擇兩個版本'} collapsed={false} onToggle={toggleCard} collapsible={false}>
              <div className="trace-panel-actions">
                <label>版本 A<select value={compareV1} onChange={e => setCompareV1(e.target.value)}>
                  <option value="">選擇版本</option>
                  {promptVersionList.map(v => <option key={v} value={v}>{fmtVersion(v)}</option>)}
                </select></label>
                <label>版本 B<select value={compareV2} onChange={e => setCompareV2(e.target.value)}>
                  <option value="">選擇版本</option>
                  {promptVersionList.map(v => <option key={v} value={v}>{fmtVersion(v)}</option>)}
                </select></label>
                <button className="trace-btn trace-btn-primary" onClick={handleCompare} disabled={compareLoading || !compareV1 || !compareV2}>{compareLoading ? '比較中…' : '比較'}</button>
              </div>
              {compareResult && (
                <div className="trace-table-scroll trace-table-scroll-sm">
                  <table className="trace-table">
                    <thead><tr><th>指標</th><th>版本 A</th><th>版本 B</th><th>勝出</th></tr></thead>
                    <tbody>
                      {([
                        { label: '執行次數', key: 'runs', higher: true },
                        { label: '錯誤率', key: 'error_rate', higher: false, pct: true },
                        { label: '平均延遲 (s)', key: 'avg_latency', higher: false },
                        { label: '平均品質分', key: 'avg_quality', higher: true },
                      ] as Array<{ label: string; key: string; higher: boolean; pct?: boolean }>).map(({ label, key, higher, pct }) => {
                        const a = (compareResult.v1 as unknown as Record<string, number | null>)[key]
                        const b = (compareResult.v2 as unknown as Record<string, number | null>)[key]
                        const winner = a === null || b === null ? '—' : higher ? (a > b ? 'A' : b > a ? 'B' : '=') : (a < b ? 'A' : b < a ? 'B' : '=')
                        const fmtV = (v: number | null) => v === null ? '-' : pct ? `${(v * 100).toFixed(1)}%` : v
                        return (
                          <tr key={key}>
                            <td>{label}</td>
                            <td style={{ fontWeight: winner === 'A' ? 700 : 400, color: winner === 'A' ? '#16a34a' : undefined }}>{fmtV(a ?? null)}</td>
                            <td style={{ fontWeight: winner === 'B' ? 700 : 400, color: winner === 'B' ? '#16a34a' : undefined }}>{fmtV(b ?? null)}</td>
                            <td style={{ color: winner !== '—' && winner !== '=' ? '#16a34a' : '#94a3b8', fontWeight: 700 }}>{winner}</td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </CollapsiblePanel>
            )}

            {visibleToolPanels.has('batchScore') && (
            <CollapsiblePanel id="batchScore" title="批次自動評分" summary={batchMessage ?? `最近 ${batchLimit} 條未評分`} collapsed={false} onToggle={toggleCard} collapsible={false}>
              <div className="trace-panel-body">
                <p className="trace-panel-note">對最近 N 條未評分的 trace 執行 quality_agent 多維評分。</p>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <label style={{ fontSize: 11, color: '#64748b', fontWeight: 700, display: 'flex', alignItems: 'center', gap: 6 }}>
                    數量
                    <input
                      type="number"
                      min={1} max={200}
                      value={batchLimit}
                      onChange={e => setBatchLimit(Math.max(1, Math.min(200, Number(e.target.value))))}
                      style={{ width: 60, height: 34, border: '1px solid #dbe4ef', borderRadius: 8, padding: '0 8px', fontSize: 12, textAlign: 'center' }}
                    />
                  </label>
                  <button className="trace-btn trace-btn-purple" onClick={handleBatchScore} disabled={batchScoring}>{batchScoring ? '評分中…' : '開始批次評分'}</button>
                </div>
                {batchMessage && <p className="trace-panel-msg">{batchMessage}</p>}
              </div>
            </CollapsiblePanel>
            )}

            {visibleToolPanels.has('routeTest') && (
            <CollapsiblePanel id="routeTest" title="路由測試" summary={routeTestResult ? `${routeTestResult.intent}` : '測試 router'} collapsed={false} onToggle={toggleCard} collapsible={false}>
              <div className="trace-panel-body">
                <input className="trace-search-input" style={{ width: '100%' }} value={routeTestMsg} onChange={e => setRouteTestMsg(e.target.value)} onKeyDown={e => e.key === 'Enter' && handleTestRoute()} placeholder="輸入訊息測試路由決策…" />
                <button className="trace-btn trace-btn-sky" onClick={handleTestRoute} disabled={routeTestLoading || !routeTestMsg.trim()}>{routeTestLoading ? '測試中…' : '測試路由'}</button>
                {routeTestResult && (
                  <div className="trace-route-result">
                    <div>意圖：<strong>{routeTestResult.intent}</strong>
                      {routeTestResult.original_intent && routeTestResult.original_intent !== routeTestResult.resolved_intent && (
                        <span className="trace-route-result-intent-arrow">{routeTestResult.original_intent} → {routeTestResult.resolved_intent}</span>
                      )}
                      <span className="trace-route-result-path">（{routeTestResult.path === 'keyword' ? '關鍵字' : 'LLM'}）</span>
                    </div>
                    <div>Agent：<strong>{routeTestResult.agent_name}</strong></div>
                    <div style={{ fontFamily: 'monospace', fontSize: 11, color: '#64748b' }}>Prompt：{routeTestResult.prompt_name} <span title={routeTestResult.prompt_version}>{fmtVersion(routeTestResult.prompt_version)}</span></div>
                  </div>
                )}
              </div>
            </CollapsiblePanel>
            )}

            {visibleToolPanels.has('promptList') && (
            <CollapsiblePanel id="promptList" title="Prompt 列表" summary={`${promptList.length} prompts`} collapsed={false} onToggle={toggleCard} collapsible={false} className="trace-tools-wide">
              <div className="trace-table-scroll trace-table-scroll-tools">
                <table className="trace-table">
                  <thead><tr><th>名稱</th><th>版本</th><th>字數</th><th>最後修改</th><th>Avg 品質</th></tr></thead>
                  <tbody>
                    {promptList.map(p => {
                      const ps = byPrompt.find(r => r.key === p.name)
                      return (
                        <tr key={p.name}>
                          <td style={{ fontFamily: 'monospace', fontSize: 12 }}>{p.name}</td>
                          <td style={{ fontFamily: 'monospace', fontSize: 11, color: '#94a3b8' }} title={p.version}>{fmtVersion(p.version)}</td>
                          <td>{p.size_chars.toLocaleString()}</td>
                          <td style={{ fontSize: 11, color: '#94a3b8' }}>{p.last_modified ? new Date(p.last_modified).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}</td>
                          <td>{ps?.avg_quality_score != null ? `${ps.avg_quality_score}/5` : '-'}</td>
                        </tr>
                      )
                    })}
                    {promptList.length === 0 && <tr><td colSpan={5} className="trace-empty" style={{ textAlign: 'center' }}>載入中…</td></tr>}
                  </tbody>
                </table>
              </div>
            </CollapsiblePanel>
            )}

            {visibleToolPanels.has('eval') && (
            <CollapsiblePanel id="eval" title="路由準確性評估" summary={evalResult ? `${(evalResult.accuracy * 100).toFixed(1)}%` : '尚未執行'} collapsed={false} onToggle={toggleCard} collapsible={false} className="trace-tools-wide">
              <div className="trace-panel-body trace-panel-body-row">
                <button className="trace-btn trace-btn-amber" onClick={handleRunEval} disabled={evalLoading}>{evalLoading ? '執行中…' : '執行評估'}</button>
              </div>
              {evalResult && (
                <div className="trace-panel-body">
                  <p className={`trace-eval-accuracy ${evalResult.accuracy >= 0.9 ? 'good' : evalResult.accuracy >= 0.7 ? 'warn' : 'bad'}`}>
                    準確率：{(evalResult.accuracy * 100).toFixed(1)}%（{evalResult.correct}/{evalResult.total}）
                  </p>
                  {evalResult.wrong_cases.length === 0 ? <p className="trace-panel-msg">所有測試案例全部通過！</p> : (
                    <div className="trace-table-scroll trace-table-scroll-sm">
                      <table className="trace-table">
                        <thead><tr><th>ID</th><th>訊息</th><th>預期</th><th>實際</th><th>備註</th></tr></thead>
                        <tbody>
                          {evalResult.wrong_cases.map((c) => (
                            <tr key={c.id}>
                              <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{c.id}</td>
                              <td style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={c.message}>{c.message}</td>
                              <td style={{ color: '#16a34a', fontWeight: 600 }}>{c.expected}</td>
                              <td style={{ color: '#dc2626', fontWeight: 600 }}>{c.got}</td>
                              <td style={{ fontSize: 11, color: '#94a3b8' }}>{c.note}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </CollapsiblePanel>
            )}
            {visibleToolPanels.size === 0 && (
              <div className="trace-tools-empty">左側勾選要顯示的工具面板。</div>
            )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Research Trace Panel ──────────────────────────────────────────────────────

interface ResearchCoverage {
  id: string
  label: string
  required: boolean
  status: string
  evidence_count: number
}

interface ResearchStep {
  round: number
  slot: string
  slot_label: string
  display_intent: string
  quality: string
  chunk_count: number
  updated_slots: string[]
  missing_gap?: string
  query_bundle?: {
    keyword_query: string
    semantic_query?: string
    section_terms?: string[]
    use_hyde: boolean
  }
}

interface ResearchDisplay {
  answer?: string
  sources?: string[]
  events?: TraceEvent[]
  research_state?: {
    evidence_details_brief?: Record<string, Array<{ quote?: string; interpretation?: string; source_type?: string; filename?: string; page?: number | null }>>
  }
  trace_summary?: {
    coverage?: ResearchCoverage[]
    steps?: ResearchStep[]
    progress?: { search_count: number; verification_done: boolean }
  }
}

const STATUS_ZH: Record<string, string> = {
  FILLED: '✓ 完整', PARTIAL: '△ 部分', EXHAUSTED: '— 已窮盡', NOT_FILLED: '✕ 缺失',
}
const QUALITY_ZH: Record<string, string> = {
  USEFUL: '有用', NOT_USEFUL: '不夠用', NO_RESULTS: '無結果',
}
const QUALITY_COLOR: Record<string, string> = {
  USEFUL: '#16a34a', NOT_USEFUL: '#d97706', NO_RESULTS: '#dc2626',
}
const STATUS_COLOR: Record<string, string> = {
  FILLED: '#16a34a', PARTIAL: '#d97706', EXHAUSTED: '#94a3b8', NOT_FILLED: '#dc2626',
}

function ResearchTracePanel({ display }: { display: ResearchDisplay | null }) {
  if (!display?.trace_summary) return null
  const { trace_summary: ts, answer, sources } = display

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {ts.coverage && ts.coverage.length > 0 && (
        <div>
          <h3 style={{ fontSize: 13, fontWeight: 600, margin: '0 0 8px', color: '#10233f' }}>
            Coverage 狀態
            {ts.progress && (
              <span style={{ fontSize: 11, fontWeight: 400, color: '#94a3b8', marginLeft: 8 }}>
                {ts.progress.search_count} 輪搜尋・{ts.progress.verification_done ? '已驗證' : '未驗證'}
              </span>
            )}
          </h3>
          <table className="trace-table trace-table-stats">
            <thead><tr><th>檢索面向</th><th>狀態</th><th>證據</th><th>必填</th></tr></thead>
            <tbody>
              {ts.coverage.map(item => (
                <tr key={item.id}>
                  <td style={{ fontWeight: 600 }}>{item.label || item.id}</td>
                  <td><span style={{ color: STATUS_COLOR[item.status] ?? '#94a3b8', fontWeight: 700, fontSize: 11 }}>{STATUS_ZH[item.status] ?? item.status}</span></td>
                  <td>{item.evidence_count}</td>
                  <td style={{ color: item.required ? '#16a34a' : '#94a3b8' }}>{item.required ? '✓' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {ts.steps && ts.steps.length > 0 && (
        <div>
          <h3 style={{ fontSize: 13, fontWeight: 600, margin: '0 0 8px', color: '#10233f' }}>搜尋步驟</h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {ts.steps.map(step => (
              <div key={step.round} style={{ border: '1px solid #e7edf5', borderRadius: 8, background: '#fafbfc', overflow: 'hidden' }}>
                {/* 主資訊列 */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderBottom: '1px solid #f1f5f9' }}>
                  <span style={{ fontSize: 11, fontWeight: 800, color: '#94a3b8', width: 20, flexShrink: 0 }}>#{step.round}</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: '#334155', flexShrink: 0 }}>{step.slot_label || step.slot}</span>
                  <span style={{ fontSize: 11, color: '#64748b', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{step.display_intent}</span>
                  <span style={{ color: QUALITY_COLOR[step.quality] ?? '#94a3b8', fontWeight: 700, fontSize: 11, flexShrink: 0 }}>{QUALITY_ZH[step.quality] ?? step.quality}</span>
                  <span style={{ fontSize: 11, color: '#94a3b8', flexShrink: 0 }}>{step.chunk_count} 筆{step.query_bundle?.use_hyde ? ' · HyDE' : ''}</span>
                </div>
                {/* Query bundle — 預設展開 */}
                <div style={{ padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11 }}>
                  {step.query_bundle?.keyword_query && (
                    <div><span style={{ color: '#94a3b8', fontWeight: 700, marginRight: 6 }}>關鍵字</span>
                      <span style={{ color: '#334155', fontFamily: 'monospace' }}>{step.query_bundle.keyword_query}</span></div>
                  )}
                  {step.query_bundle?.semantic_query && (
                    <div><span style={{ color: '#94a3b8', fontWeight: 700, marginRight: 6 }}>語意</span>
                      <span style={{ color: '#475569' }}>{step.query_bundle.semantic_query}</span></div>
                  )}
                  {step.missing_gap && (
                    <div><span style={{ color: '#94a3b8', fontWeight: 700, marginRight: 6 }}>缺口</span>
                      <span style={{ color: '#d97706' }}>{step.missing_gap}</span></div>
                  )}
                  {step.updated_slots && step.updated_slots.length > 0 && (
                    <div><span style={{ color: '#94a3b8', fontWeight: 700, marginRight: 6 }}>更新</span>
                      <span style={{ color: '#16a34a' }}>{step.updated_slots.join('、')}</span></div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {answer && (
        <div>
          <h3 style={{ fontSize: 13, fontWeight: 600, margin: '0 0 8px', color: '#10233f' }}>最終答案</h3>
          <div style={{
            background: '#f8fafc', border: '1px solid #e2e8f0', borderRadius: 8,
            padding: '12px 16px', fontSize: 13, lineHeight: 1.8,
            whiteSpace: 'pre-wrap', color: '#1e293b', maxHeight: 360, overflowY: 'auto',
          }}>
            {answer}
          </div>
          {sources && sources.length > 0 && (
            <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {sources.map((s, i) => (
                <span key={i} style={{
                  fontSize: 10, fontFamily: 'monospace', color: '#64748b',
                  background: '#f1f5f9', borderRadius: 4, padding: '2px 6px',
                }}>{s}</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Trace Event Panel ─────────────────────────────────────────────────────────

const EVENT_COLOR: Record<string, string> = {
  user_message: '#64748b', planner_message: '#7c3aed', tool_call_args: '#0ea5e9',
  tool_result: '#16a34a', reflector_message: '#d97706', writer_message: '#2563eb',
}
const EVENT_LABEL: Record<string, string> = {
  user_message: '使用者', planner_message: '規劃', tool_call_args: '搜尋請求',
  tool_result: '搜尋結果', reflector_message: '反思', writer_message: '最終撰寫',
}

function TraceEventPanel({ events }: { events: TraceEvent[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set())
  const toggleExpanded = (index: number) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }
  if (!events.length) return <div className="trace-empty">無事件資料</div>
  return (
    <div className="trace-event-list">
      {events.map((ev, i) => {
        const isExpanded = expanded.has(i)
        return (
          <div key={i} className="trace-event-item">
            <div
              className={`trace-event-header${ev.payload_debug ? '' : ' no-debug'}`}
              onClick={() => ev.payload_debug && toggleExpanded(i)}
            >
              <span className="trace-event-dot" style={{ background: EVENT_COLOR[ev.type] ?? '#e2e8f0' }} />
              <span className="trace-event-type" style={{ color: EVENT_COLOR[ev.type] ?? '#94a3b8' }}>
                {EVENT_LABEL[ev.type] ?? ev.type}
              </span>
              {ev.round > 0 && <span className="trace-event-round">#{ev.round}</span>}
              {ev.slot_label && <span className="trace-event-slot">{ev.slot_label}</span>}
              <span className="trace-event-summary">{ev.payload_summary}</span>
              {ev.payload_debug && <span className="trace-event-expand">{isExpanded ? '▲' : '▼'}</span>}
            </div>
            {isExpanded && ev.payload_debug && (
              <pre className="trace-event-debug">{JSON.stringify(ev.payload_debug, null, 2)}</pre>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ── Evidence Panel ─────────────────────────────────────────────────────────────

function EvidencePanel({ display }: { display: ResearchDisplay | null }) {
  const [expandedEvidence, setExpandedEvidence] = useState<Set<string>>(() => new Set())
  const evidence = display?.research_state?.evidence_details_brief
  if (!evidence || Object.keys(evidence).length === 0) {
    return <div className="trace-empty">此 trace 無 evidence 細節記錄（非 research mode）</div>
  }
  const coverage = display?.trace_summary?.coverage ?? []
  const labelMap: Record<string, string> = {}
  coverage.forEach(c => { labelMap[c.id] = c.label || c.id })

  const toggleEvidence = (key: string) => {
    setExpandedEvidence(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <div className="trace-evidence-list">
      {Object.entries(evidence).map(([slotId, items]) => (
        <div key={slotId}>
          <h4 className="trace-evidence-slot-title">
            {labelMap[slotId] ?? slotId}
            <span className="trace-evidence-slot-count">({items.length} 筆)</span>
          </h4>
          {items.map((item, i) => {
            const evidenceKey = `${slotId}-${i}`
            const isExpanded = expandedEvidence.has(evidenceKey)
            const shouldToggle = (item.quote?.length ?? 0) > 180
            return (
              <div key={evidenceKey} className="trace-evidence-card">
                <div className="trace-evidence-meta">
                  {item.filename && <span className="trace-evidence-filename">{item.filename}</span>}
                  {item.page != null && <span>p.{item.page}</span>}
                  {item.source_type && <span className="trace-evidence-source-type">[{item.source_type}]</span>}
                </div>
                {item.quote && (
                  <>
                    <div className={`trace-evidence-quote${isExpanded ? ' expanded' : ''}`}>{item.quote}</div>
                    {shouldToggle && (
                      <button className="trace-inline-toggle" type="button" onClick={() => toggleEvidence(evidenceKey)}>
                        {isExpanded ? '收合引用' : '展開引用'}
                      </button>
                    )}
                  </>
                )}
                {item.interpretation && <div className="trace-evidence-interpretation">{item.interpretation}</div>}
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}

function TimelineChart({ data }: { data: TimelinePoint[] }) {
  if (!data.length) return <div className="trace-empty">無走勢資料（需有近 14 天的 trace 記錄）</div>

  const W = 600, H = 120, PAD = { top: 12, right: 20, bottom: 28, left: 40 }
  const innerW = W - PAD.left - PAD.right
  const innerH = H - PAD.top - PAD.bottom

  const maxQ = 5
  const maxL = Math.max(...data.map(d => d.avg_latency ?? 0), 1)
  const n = data.length

  const xScale = (i: number) => PAD.left + (n > 1 ? (i / (n - 1)) * innerW : innerW / 2)
  const yQ = (v: number) => PAD.top + innerH - (v / maxQ) * innerH
  const yL = (v: number) => PAD.top + innerH - (v / maxL) * innerH

  const qPoints = data.map((d, i) => d.avg_quality != null ? `${xScale(i)},${yQ(d.avg_quality)}` : null).filter(Boolean).join(' ')
  const lPoints = data.map((d, i) => d.avg_latency != null ? `${xScale(i)},${yL(d.avg_latency)}` : null).filter(Boolean).join(' ')

  return (
    <div style={{ padding: '8px 12px', overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', maxWidth: W, display: 'block' }}>
        {[0, 1, 2, 3, 4, 5].map(v => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={yQ(v)} y2={yQ(v)} stroke="#f1f5f9" strokeWidth={1} />
            <text x={PAD.left - 4} y={yQ(v) + 4} fontSize={9} fill="#94a3b8" textAnchor="end">{v}</text>
          </g>
        ))}
        {qPoints && <polyline points={qPoints} fill="none" stroke="#2563eb" strokeWidth={2} strokeLinejoin="round" />}
        {lPoints && <polyline points={lPoints} fill="none" stroke="#f59e0b" strokeWidth={1.5} strokeLinejoin="round" strokeDasharray="4 2" />}
        {data.map((d, i) => i % Math.max(1, Math.floor(n / 7)) === 0 && (
          <text key={d.date} x={xScale(i)} y={H - 4} fontSize={9} fill="#94a3b8" textAnchor="middle">
            {d.date.slice(5)}
          </text>
        ))}
        <circle cx={PAD.left + 10} cy={PAD.top + 8} r={3} fill="#2563eb" />
        <text x={PAD.left + 17} y={PAD.top + 12} fontSize={10} fill="#2563eb">品質 (0-5)</text>
        <line x1={PAD.left + 80} x2={PAD.left + 94} y1={PAD.top + 8} y2={PAD.top + 8} stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="4 2" />
        <text x={PAD.left + 98} y={PAD.top + 12} fontSize={10} fill="#f59e0b">延遲 (s)</text>
      </svg>
      {data.length > 0 && (() => {
        const last = data[data.length - 1]
        return (
          <div style={{ display: 'flex', gap: 16, fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
            <span>最新日：<strong style={{ color: '#2563eb' }}>{last.avg_quality ?? '-'}</strong> 品質分</span>
            <span><strong style={{ color: '#f59e0b' }}>{last.avg_latency ?? '-'}s</strong> 延遲</span>
            <span>{last.runs} 次執行</span>
          </div>
        )
      })()}
    </div>
  )
}

function TraceGroupTable({ rows }: { rows: TraceGroupStats[] }) {
  if (rows.length === 0) return <div className="trace-empty">No data</div>
  return (
    <table className="trace-table trace-table-stats">
      <colgroup>
        <col style={{ width: '120px' }} />
        <col />
        <col />
        <col />
        <col />
      </colgroup>
      <thead>
        <tr>
          <th>名稱</th>
          <th style={{ textAlign: 'center' }}>次數</th>
          <th style={{ textAlign: 'center' }}>錯誤</th>
          <th style={{ textAlign: 'center' }}>延遲</th>
          <th style={{ textAlign: 'center' }}>品質</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key}>
            <td title={row.key} style={{ fontFamily: 'monospace', color: '#334155' }}>
              {row.key.replace(/sha256:[a-f0-9]+/g, (m) => fmtVersion(m))}
            </td>
            <td style={{ textAlign: 'center', color: '#475569' }}>{row.runs}</td>
            <td style={{ textAlign: 'center', color: row.errors > 0 ? '#dc2626' : '#94a3b8', fontWeight: row.errors > 0 ? 700 : 400 }}>{row.errors}</td>
            <td style={{ textAlign: 'center', color: '#64748b' }}>{row.avg_latency != null ? `${Math.round(row.avg_latency)}s` : '-'}</td>
            <td style={{ textAlign: 'center', color: row.avg_quality_score != null ? '#16a34a' : '#94a3b8', fontWeight: row.avg_quality_score != null ? 700 : 400 }}>
              {row.avg_quality_score != null ? `${row.avg_quality_score}/5` : '-'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

// ── Trace List (card-style, for the narrow left column) ───────────────────────

const MODE_COLOR: Record<string, string> = {
  research: '#7c3aed', summary: '#2563eb', chat: '#64748b',
  retrieval: '#0ea5e9', question: '#d97706',
}

function TraceList({
  rows,
  onSelect,
  selectedId,
}: {
  rows: TraceItem[]
  onSelect?: (id: string) => void
  selectedId?: string
}) {
  if (rows.length === 0) return <div className="trace-empty">No runs</div>
  return (
    <div className="trace-list">
      {rows.map((row) => {
        const isErr = row.status === 'error'
        const dotColor = isErr ? '#dc2626' : (MODE_COLOR[row.mode ?? ''] ?? '#94a3b8')
        const isSelected = selectedId === row.id
        const hasHandoff = row.original_intent && row.original_intent !== row.resolved_intent
        return (
          <div
            key={row.id}
            className={`trace-list-item${isSelected ? ' selected' : ''}${onSelect ? ' clickable' : ''}`}
            onClick={() => onSelect?.(row.id)}
          >
            <div className="trace-list-row1">
              <span className="trace-list-dot" style={{ background: dotColor }} />
              <span className="trace-list-mode">{row.mode ?? '-'}</span>
              {hasHandoff && (
                <span className="trace-list-handoff">→ {row.resolved_intent}</span>
              )}
              <span className="trace-list-time">{timeLabel(row.start_time)}</span>
            </div>
            <div className="trace-list-row2">
              <span className="trace-list-prompt" title={row.prompt_name ?? undefined}>
                {row.prompt_name ?? row.agent_name ?? '-'}
              </span>
              <span className="trace-list-meta">
                {row.latency != null && <span>{row.latency}s</span>}
                <span className={`trace-list-status ${isErr ? 'err' : 'ok'}`}>
                  {isErr ? '✕' : '✓'}
                </span>
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── Trace Table (used for compact errors/slow-runs and child runs) ─────────────

