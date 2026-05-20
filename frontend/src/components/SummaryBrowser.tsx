import { useEffect, useState, useCallback } from 'react'
import { getSummaries, extractSummary, reindexDocument } from '../api'
import type { SummaryItem } from '../api'
import ChunkViewer from './viewer/ChunkViewer'

interface Props {
  onClose: () => void
}

export default function SummaryBrowser({ onClose }: Props) {
  const [items, setItems] = useState<SummaryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [extracting, setExtracting] = useState<Set<number>>(new Set())
  const [reindexing, setReindexing] = useState<Set<number>>(new Set())
  const [expanded, setExpanded] = useState<string | null>(null)
  const [chunkDocId, setChunkDocId] = useState<number | null>(null)
  const [search, setSearch] = useState('')

  const refresh = useCallback(async () => {
    try {
      setItems(await getSummaries())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleExtract = async (id: number) => {
    setExtracting((s) => new Set(s).add(id))
    setItems((prev) => prev.map((it) => it.id === id ? { ...it, batch_status: 'processing' } : it))
    try {
      await extractSummary(id)
    } catch {
      setItems((prev) => prev.map((it) => it.id === id ? { ...it, batch_status: 'error' } : it))
    } finally {
      setExtracting((s) => { const n = new Set(s); n.delete(id); return n })
      refresh()
    }
  }

  const handleReindex = async (id: number) => {
    setReindexing((s) => new Set(s).add(id))
    try {
      await reindexDocument(id)
    } finally {
      setReindexing((s) => { const n = new Set(s); n.delete(id); return n })
      refresh()
    }
  }

  const handleExtractAll = async () => {
    const pending = items.filter((it) => !it.summary && it.batch_status !== 'processing')
    for (const it of pending) await handleExtract(it.id)
  }

  const filtered = items.filter((it) =>
    it.filename.toLowerCase().includes(search.toLowerCase()) ||
    it.summary?.motivation.includes(search) ||
    it.summary?.method.includes(search) ||
    it.summary?.results.includes(search)
  )

  const getUniqueKey = (item: SummaryItem, index: number) => 
    `${item.id}-${index}`

  return (
    <div style={styles.overlay}>
      <div style={styles.panel}>
        <div style={styles.header}>
          <h2 style={styles.title}>大專生計畫摘要瀏覽</h2>
          <div style={styles.headerActions}>
            <input
              style={styles.search}
              placeholder="搜尋檔名或內容..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <button style={styles.btnSecondary} onClick={handleExtractAll}>
              批次提取未完成
            </button>
            <button style={styles.btnClose} onClick={onClose}>✕</button>
          </div>
        </div>

        <div style={styles.stats}>
          共 {items.length} 份 ／ 已提取 {items.filter((i) => i.summary).length} 份
        </div>

        {loading ? (
          <div style={styles.center}>載入中…</div>
        ) : filtered.length === 0 ? (
          <div style={styles.center}>沒有符合的文件</div>
        ) : (
          <div style={styles.list}>
            {filtered.map((item, idx) => {
              const ukey = getUniqueKey(item, idx)
              const isExpanded = expanded === ukey
              return (
                <div key={ukey} style={styles.card}>
                  <div style={styles.cardHeader}>
                    <span style={styles.filename} title={item.filename}>
                      {item.filename.replace(/^[^_]+_[^_]+_/, '').replace(/\.pdf$/i, '')}
                    </span>
                    <div style={styles.cardActions}>
                      <StatusBadge status={item.batch_status as any} />
                      {item.summary && (
                        <button
                          style={styles.btnToggle}
                          onClick={() => setExpanded(isExpanded ? null : ukey)}
                        >
                          {isExpanded ? '收起' : '展開'}
                        </button>
                      )}
                      <button style={styles.btnChunks} onClick={() => setChunkDocId(item.id)}>Chunks</button>
                      <button 
                        style={styles.btnReindex} 
                        disabled={reindexing.has(item.id)} 
                        onClick={() => handleReindex(item.id)}
                      >
                        {reindexing.has(item.id) ? '嵌入中…' : '重新嵌入'}
                      </button>
                      <button 
                        style={styles.btnExtract} 
                        disabled={extracting.has(item.id)} 
                        onClick={() => handleExtract(item.id)}
                      >
                        {extracting.has(item.id) ? '提取中…' : item.summary ? '重新提取' : '提取'}
                      </button>
                    </div>
                  </div>

                  {item.summary && isExpanded && (
                    <div style={styles.sections}>
                      {item.summary.tags?.length > 0 && (
                        <div style={styles.tags}>
                          {item.summary.tags.map((tag: string) => (
                            <span key={tag} style={styles.tag}>{tag}</span>
                          ))}
                        </div>
                      )}
                      <SectionBlock label="研究動機與問題" text={item.summary.motivation} />
                      <SectionBlock label="研究方法" text={item.summary.method} />
                      <SectionBlock label="研究成果" text={item.summary.results} />
                    </div>
                  )}

                  {item.summary && !isExpanded && (
                    <div style={styles.preview}>{item.summary.motivation.slice(0, 80)}…</div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {chunkDocId && (
        <ChunkViewer docId={chunkDocId} onClose={() => setChunkDocId(null)} />
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: 'pending' | 'processing' | 'summarized' | 'error' }) {
  const map = {
    pending:    { label: '待提取', color: '#6b7280' },
    processing: { label: '提取中', color: '#d97706' },
    summarized: { label: '已完成', color: '#059669' },
    error:      { label: '錯誤',   color: '#dc2626' },
  }
  const { label, color } = map[status] ?? map.pending
  return <span style={{ ...styles.badge, backgroundColor: color }}>{label}</span>
}

function SectionBlock({ label, text }: { label: string; text: string }) {
  return (
    <div style={styles.section}>
      <div style={styles.sectionLabel}>{label}</div>
      <div style={styles.sectionText}>{text}</div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.5)',
    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
  },
  panel: {
    backgroundColor: '#1e1e2e', color: '#cdd6f4', borderRadius: 12,
    width: '90vw', maxWidth: 960, maxHeight: '88vh',
    display: 'flex', flexDirection: 'column', overflow: 'hidden',
    boxShadow: '0 8px 32px rgba(0,0,0,0.5)',
  },
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    padding: '16px 20px', borderBottom: '1px solid #313244', gap: 12,
  },
  title: { margin: 0, fontSize: 18, fontWeight: 600, whiteSpace: 'nowrap' },
  headerActions: { display: 'flex', gap: 10, alignItems: 'center', flex: 1, justifyContent: 'flex-end' },
  search: {
    backgroundColor: '#313244', border: '1px solid #45475a', color: '#cdd6f4',
    padding: '6px 12px', borderRadius: 6, fontSize: 13, outline: 'none', width: '100%', maxWidth: 240,
  },
  btnSecondary: {
    backgroundColor: '#45475a', color: '#cdd6f4', border: 'none', padding: '6px 12px',
    borderRadius: 6, cursor: 'pointer', fontSize: 13, whiteSpace: 'nowrap',
  },
  btnClose: {
    background: 'none', border: 'none', color: '#9399b2', fontSize: 20, cursor: 'pointer', padding: '0 4px',
  },
  stats: { padding: '8px 20px', fontSize: 12, color: '#9399b2', backgroundColor: '#181825' },
  list: { flex: 1, overflowY: 'auto', padding: 20, display: 'flex', flexDirection: 'column', gap: 12 },
  center: { padding: 40, textAlign: 'center', color: '#9399b2' },
  card: {
    backgroundColor: '#313244', borderRadius: 8, padding: '12px 16px',
    border: '1px solid #45475a', transition: 'all 0.2s',
  },
  cardHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16 },
  filename: { fontSize: 14, fontWeight: 500, color: '#f5e0dc', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 },
  cardActions: { display: 'flex', alignItems: 'center', gap: 8 },
  badge: { fontSize: 11, padding: '2px 8px', borderRadius: 4, color: '#fff', fontWeight: 600 },
  btnToggle: {
    border: 'none', backgroundColor: 'transparent', color: '#89b4fa', cursor: 'pointer', fontSize: 13, fontWeight: 500,
  },
  btnChunks: { backgroundColor: '#45475a', color: '#cdd6f4', border: 'none', padding: '4px 8px', borderRadius: 4, cursor: 'pointer', fontSize: 12 },
  btnReindex: { backgroundColor: '#45475a', color: '#cdd6f4', border: 'none', padding: '4px 8px', borderRadius: 4, cursor: 'pointer', fontSize: 12 },
  btnExtract: { backgroundColor: '#89b4fa', color: '#1e1e2e', border: 'none', padding: '4px 12px', borderRadius: 4, cursor: 'pointer', fontSize: 12, fontWeight: 600 },
  preview: { fontSize: 12, color: '#9399b2', marginTop: 8, paddingLeft: 4, borderLeft: '2px solid #45475a' },
  sections: { display: 'flex', flexDirection: 'column', gap: 12, marginTop: 16, paddingTop: 16, borderTop: '1px solid #45475a' },
  section: { backgroundColor: '#1e1e2e', borderRadius: 6, padding: '12px' },
  sectionLabel: { fontSize: 11, fontWeight: 600, color: '#89b4fa', marginBottom: 6, textTransform: 'uppercase', letterSpacing: '0.05em' },
  sectionText: { fontSize: 13, lineHeight: 1.6, color: '#cdd6f4' },
  tags: { display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 },
  tag: { fontSize: 11, padding: '2px 8px', borderRadius: 10, backgroundColor: '#45475a', color: '#cdd6f4' },
}
