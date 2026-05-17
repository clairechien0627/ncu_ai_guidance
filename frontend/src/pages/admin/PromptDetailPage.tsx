import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getPromptDetail, getPromptVersionContent, type PromptVersion } from '../../api'
import './PromptDetailPage.css'

// ── Pure diff utility ─────────────────────────────────────────────────────────

type DiffLine = { type: 'same' | 'add' | 'remove'; text: string }

function diffLines(a: string, b: string): DiffLine[] {
  const aLines = a.split('\n')
  const bLines = b.split('\n')
  const result: DiffLine[] = []

  // Simple LCS-based diff (Myers-like, O(n²) — fine for small prompts)
  const m = aLines.length, n = bLines.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = aLines[i] === bLines[j]
        ? dp[i + 1][j + 1] + 1
        : Math.max(dp[i + 1][j], dp[i][j + 1])

  let i = 0, j = 0
  while (i < m || j < n) {
    if (i < m && j < n && aLines[i] === bLines[j]) {
      result.push({ type: 'same', text: aLines[i] })
      i++; j++
    } else if (j < n && (i >= m || dp[i][j + 1] >= dp[i + 1][j])) {
      result.push({ type: 'add', text: bLines[j] })
      j++
    } else {
      result.push({ type: 'remove', text: aLines[i] })
      i++
    }
  }
  return result
}

// ── Diff viewer ───────────────────────────────────────────────────────────────

function DiffViewer({ oldContent, newContent }: { oldContent: string; newContent: string }) {
  const lines = diffLines(oldContent, newContent)
  return (
    <div className="pd-diff">
      {lines.map((line, i) => (
        <div key={i} className={`pd-diff-line pd-diff-line--${line.type}`}>
          <span className="pd-diff-sign">
            {line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}
          </span>
          <span className="pd-diff-text">{line.text || ' '}</span>
        </div>
      ))}
    </div>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

type Tab = 'content' | 'history' | 'diff'

export default function PromptDetailPage() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('content')
  const [compareHash, setCompareHash] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['prompt-detail', name],
    queryFn: () => getPromptDetail(name!),
    enabled: !!name,
  })

  const { data: compareVersion } = useQuery({
    queryKey: ['prompt-version', name, compareHash],
    queryFn: () => getPromptVersionContent(name!, compareHash!),
    enabled: !!compareHash,
  })

  if (isLoading) return <div className="pd-loading">Loading prompt…</div>
  if (!data) return <div className="pd-error">Prompt not found</div>

  const latest = data.versions[0]
  const hasDiff = !!compareVersion

  return (
    <div className="pd-page">
      {/* Header */}
      <div className="pd-header">
        <div className="pd-header-left">
          <button className="pd-back-btn" onClick={() => navigate('/admin/prompts')}>
            ← Prompts
          </button>
          <div>
            <h1 className="pd-name">{data.name}</h1>
            {latest && (
              <span className="pd-version-label">
                #{latest.hash} · {latest.word_count} words · {data.versions.length} versions
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="pd-tabs">
        {(['content', 'history', 'diff'] as Tab[]).map(t => {
          if (t === 'diff' && !hasDiff) return null
          return (
            <button
              key={t}
              className={`pd-tab-btn${tab === t ? ' active' : ''}`}
              onClick={() => setTab(t)}
            >
              {t === 'content' ? 'Content' : t === 'history' ? 'History' : 'Diff'}
            </button>
          )
        })}
        {compareVersion && (
          <span className="pd-compare-label">
            Comparing #{latest?.hash} ↔ #{compareVersion.hash}
            <button className="pd-clear-compare" onClick={() => { setCompareHash(null); setTab('content') }}>✕</button>
          </span>
        )}
      </div>

      {/* Content */}
      {tab === 'content' && (
        <div className="pd-content-pane">
          <pre className="pd-content-text">{data.content}</pre>
        </div>
      )}

      {/* History */}
      {tab === 'history' && (
        <div className="pd-history-pane">
          <table className="pd-history-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Hash</th>
                <th>Synced At</th>
                <th>Words</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.versions.map((v: PromptVersion, i: number) => (
                <tr key={v.hash} className={`pd-history-row${i === 0 ? ' pd-history-row--latest' : ''}`}>
                  <td className="pd-td-num">v{data.versions.length - i}</td>
                  <td><code className="pd-hash">#{v.hash}</code></td>
                  <td className="pd-td-date">
                    {new Date(v.synced_at).toLocaleString('zh-TW', {
                      month: '2-digit', day: '2-digit',
                      hour: '2-digit', minute: '2-digit', hour12: false,
                    })}
                  </td>
                  <td className="pd-td-num">{v.word_count}</td>
                  <td>
                    {i > 0 && (
                      <button
                        className="pd-compare-btn"
                        onClick={() => { setCompareHash(v.hash); setTab('diff') }}
                      >
                        ↕ Compare with current
                      </button>
                    )}
                    {i === 0 && <span className="pd-latest-tag">latest</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Diff */}
      {tab === 'diff' && compareVersion && (
        <div className="pd-diff-pane">
          <div className="pd-diff-header">
            <span className="pd-diff-old">— #{compareVersion.hash} ({compareVersion.word_count} words)</span>
            <span className="pd-diff-new">+ #{latest?.hash} ({latest?.word_count} words, current)</span>
          </div>
          <DiffViewer oldContent={compareVersion.content} newContent={data.content} />
        </div>
      )}
    </div>
  )
}
