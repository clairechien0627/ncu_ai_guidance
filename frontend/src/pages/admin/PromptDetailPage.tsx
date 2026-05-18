import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { getPromptDetail, getPromptVersionContent, type PromptVersion } from '../../api'
import { PageHeader } from '../../components/admin/PageHeader'

type DiffLine = { type: 'same' | 'add' | 'remove'; text: string }

function diffLines(a: string, b: string): DiffLine[] {
  const aLines = a.split('\n'), bLines = b.split('\n'), result: DiffLine[] = []
  const m = aLines.length, n = bLines.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i--)
    for (let j = n - 1; j >= 0; j--)
      dp[i][j] = aLines[i] === bLines[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  let i = 0, j = 0
  while (i < m || j < n) {
    if (i < m && j < n && aLines[i] === bLines[j]) { result.push({ type: 'same', text: aLines[i] }); i++; j++ }
    else if (j < n && (i >= m || dp[i][j + 1] >= dp[i + 1][j])) { result.push({ type: 'add', text: bLines[j] }); j++ }
    else { result.push({ type: 'remove', text: aLines[i] }); i++ }
  }
  return result
}

function DiffViewer({ oldContent, newContent }: { oldContent: string; newContent: string }) {
  const COLORS = { add: 'var(--adm-green-bg)', remove: 'var(--adm-red-bg)', same: undefined }
  const TEXT_COLORS = { add: 'var(--adm-green-text)', remove: 'var(--adm-red-text)', same: 'var(--adm-text-2)' }
  return (
    <div style={{ background: 'var(--adm-surface)', border: '1px solid var(--adm-border)', borderRadius: 'var(--adm-radius)', overflow: 'hidden' }}>
      {diffLines(oldContent, newContent).map((line, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, padding: '1px 12px', background: COLORS[line.type], fontFamily: 'var(--adm-font-mono)', fontSize: 12 }}>
          <span style={{ color: TEXT_COLORS[line.type], width: 12, flexShrink: 0 }}>{line.type === 'add' ? '+' : line.type === 'remove' ? '-' : ' '}</span>
          <span style={{ color: TEXT_COLORS[line.type] }}>{line.text || ' '}</span>
        </div>
      ))}
    </div>
  )
}

type Tab = 'content' | 'history' | 'diff'
const TAB_STYLE = (active: boolean) => ({ padding: '6px 14px', border: 'none', background: 'none', cursor: 'pointer', fontSize: 13, fontWeight: active ? 600 : 500, color: active ? 'var(--adm-text)' : 'var(--adm-text-3)', borderBottom: active ? '2px solid var(--adm-text)' : '2px solid transparent', transition: 'color 120ms, border-color 120ms' })

export default function PromptDetailPage() {
  const { name } = useParams<{ name: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('content')
  const [compareHash, setCompareHash] = useState<string | null>(null)

  const { data, isLoading } = useQuery({ queryKey: ['prompt-detail', name], queryFn: () => getPromptDetail(name!), enabled: !!name })
  const { data: compareVersion } = useQuery({ queryKey: ['prompt-version', name, compareHash], queryFn: () => getPromptVersionContent(name!, compareHash!), enabled: !!compareHash })

  if (isLoading) return <div style={{ padding: 32, color: 'var(--adm-text-3)' }}>載入中…</div>
  if (!data) return <div style={{ padding: 32, color: 'var(--adm-red)' }}>Prompt not found</div>

  const latest = data.versions[0]

  return (
    <div>
      <PageHeader
        title={data.name}
        crumbs={[{ label: 'Prompts', to: '/admin/prompts' }, { label: data.name }]}
        subtitle={latest ? `#${latest.hash} · ${latest.word_count} words · ${data.versions.length} versions` : undefined}
        actions={<Link to={`/admin/prompts/${encodeURIComponent(data.name)}/metrics`} className="adm-btn adm-btn-secondary adm-btn-sm">Metrics →</Link>}
      />

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--adm-border)', marginBottom: 16 }}>
        {(['content', 'history', 'diff'] as Tab[]).map(t => {
          if (t === 'diff' && !compareVersion) return null
          return <button key={t} style={TAB_STYLE(tab === t)} onClick={() => setTab(t)}>{t === 'content' ? 'Content' : t === 'history' ? 'History' : 'Diff'}</button>
        })}
        {compareVersion && (
          <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--adm-text-2)', display: 'flex', alignItems: 'center', gap: 6 }}>
            #{latest?.hash} ↔ #{compareVersion.hash}
            <button className="adm-btn-icon" onClick={() => { setCompareHash(null); setTab('content') }}>✕</button>
          </span>
        )}
      </div>

      {tab === 'content' && (
        <pre style={{ fontFamily: 'var(--adm-font-mono)', fontSize: 12, background: 'var(--adm-surface)', border: '1px solid var(--adm-border)', borderRadius: 'var(--adm-radius)', padding: 16, whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.6, maxHeight: 600, overflowY: 'auto' }}>
          {data.content}
        </pre>
      )}

      {tab === 'history' && (
        <div className="adm-table-wrap">
          <table className="adm-table">
            <thead>
              <tr>
                <th style={{ width: 40 }}>#</th>
                <th style={{ width: 120 }}>Hash</th>
                <th className="adm-col-time">Synced At</th>
                <th style={{ width: 70 }}>Words</th>
                <th className="adm-col-action" />
              </tr>
            </thead>
            <tbody>
              {data.versions.map((v: PromptVersion, i: number) => (
                <tr key={v.hash} style={{ background: i === 0 ? 'var(--adm-blue-bg)' : undefined }}>
                  <td style={{ fontSize: 11, color: 'var(--adm-text-3)' }}>v{data.versions.length - i}</td>
                  <td className="adm-cell-mono">#{v.hash}</td>
                  <td className="adm-cell-mono">{new Date(v.synced_at).toLocaleString('zh-TW', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}</td>
                  <td style={{ fontSize: 12 }}>{v.word_count}</td>
                  <td>
                    <div className="adm-action-row">
                      {i === 0
                        ? <span className="adm-badge adm-badge--info">latest</span>
                        : <button className="adm-btn adm-btn-secondary adm-btn-sm" onClick={() => { setCompareHash(v.hash); setTab('diff') }}>↕ Compare</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'diff' && compareVersion && (
        <div>
          <div style={{ display: 'flex', gap: 16, fontSize: 11, marginBottom: 8 }}>
            <span style={{ color: 'var(--adm-red)', fontFamily: 'var(--adm-font-mono)' }}>— #{compareVersion.hash} ({compareVersion.word_count} words)</span>
            <span style={{ color: 'var(--adm-green)', fontFamily: 'var(--adm-font-mono)' }}>+ #{latest?.hash} ({latest?.word_count} words, current)</span>
          </div>
          <DiffViewer oldContent={compareVersion.content} newContent={data.content} />
        </div>
      )}
    </div>
  )
}
