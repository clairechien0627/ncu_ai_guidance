import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { FileText, MessageCircle, Send, Trash2 } from 'lucide-react'
import { sendMessageStream } from '../../api'

const MarkdownRenderer = lazy(() => import('../MarkdownRenderer'))

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  streaming?: boolean
}

interface Props {
  docId: number
  filename?: string
}

export default function DocChat({ docId, filename }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [convId, setConvId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    setMessages([])
    setConvId(null)
    setInput('')
  }, [docId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
    setBusy(true)
    setMessages(prev => [
      ...prev,
      { role: 'user', content: text },
      { role: 'assistant', content: '', streaming: true },
    ])
    // Only send document context on the first message of a new conversation.
    // Subsequent turns already have the context in LangGraph checkpointer history.
    const docIds = convId === null ? [docId] : []
    await sendMessageStream(
      text, convId, 'openai', docIds,
      (token) => {
        setMessages(prev => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last.role === 'assistant') next[next.length - 1] = { ...last, content: last.content + token }
          return next
        })
      },
      (newConvId, sources, _title) => {
        setConvId(newConvId)
        setMessages(prev => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last.role === 'assistant') next[next.length - 1] = { ...last, streaming: false, sources }
          return next
        })
        setBusy(false)
      },
      (err) => {
        setMessages(prev => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last.role === 'assistant') next[next.length - 1] = { ...last, content: `（錯誤：${err}）`, streaming: false }
          return next
        })
        setBusy(false)
      },
    )
  }

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  }

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    e.target.style.height = 'auto'
    e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
  }

  const canSend = input.trim().length > 0 && !busy

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#f8fafc' }}>

      {/* Header — only shown when there are messages (for the clear button) */}
      {messages.length > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
          padding: '4px 10px', borderBottom: '1px solid #e8edf3', flexShrink: 0,
        }}>
          <button
            onClick={() => { setMessages([]); setConvId(null) }}
            title="清除對話"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#94a3b8', padding: 2, display: 'flex', alignItems: 'center' }}
          >
            <Trash2 size={13} />
          </button>
        </div>
      )}

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '16px 16px 8px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        {messages.length === 0 && (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, textAlign: 'center', padding: '0 24px' }}>
            <div style={{ width: 44, height: 44, borderRadius: '50%', background: '#e8edf3', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <MessageCircle size={22} color="#94a3b8" strokeWidth={1.5} />
            </div>
            <div style={{ fontSize: 14, fontWeight: 600, color: '#475569' }}>針對這篇論文提問</div>
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>例如：這篇的研究方法是什麼？<br />主要結論為何？和其他研究有何不同？</div>
          </div>
        )}

        {messages.map((msg, i) => {
          const isWaiting = busy && i === messages.length - 1 && msg.role === 'assistant' && msg.content === ''
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
              {msg.role === 'user' ? (
                <>
                  {filename && (
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, color: '#60a5fa', marginBottom: 3 }}>
                      <FileText size={10} style={{ flexShrink: 0 }} />
                      <span style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{filename}</span>
                    </div>
                  )}
                  <div style={{
                    maxWidth: '85%', padding: '8px 12px',
                    borderRadius: '14px 14px 4px 14px',
                    background: '#1a56db', color: '#fff',
                    fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  }}>{msg.content}</div>
                </>
              ) : (
                <div style={{ maxWidth: '92%' }}>
                  {isWaiting ? (
                    <div className="thinking-pulse"><span /><span /><span /></div>
                  ) : (
                    <>
                      <Suspense fallback={<div style={{ fontSize: 13, lineHeight: 1.6, color: '#1e293b' }}>{msg.content}</div>}>
                        <MarkdownRenderer content={msg.content} />
                      </Suspense>
                      {msg.sources && msg.sources.length > 0 && (
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                          {msg.sources.map((s, si) => (
                            <span key={si} className="source-chip">{s}</span>
                          ))}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Input row */}
      <div style={{
        flexShrink: 0, padding: '12px 14px 16px',
        borderTop: '1px solid #e8edf3', background: '#fff',
        display: 'flex', alignItems: 'flex-end', gap: 8,
      }}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKey}
          placeholder="輸入問題，Enter 送出…"
          rows={1}
          disabled={busy}
          style={{
            flex: 1, resize: 'none', border: '1px solid #dbe3ef', borderRadius: 12,
            padding: '9px 13px', fontSize: 13, outline: 'none', lineHeight: 1.6,
            fontFamily: 'inherit', background: '#f8fafc', color: '#1e293b',
            maxHeight: 120, overflow: 'auto', transition: 'border-color 0.15s',
          }}
          onFocus={e => e.currentTarget.style.borderColor = '#93c5fd'}
          onBlur={e => e.currentTarget.style.borderColor = '#dbe3ef'}
        />
        <button
          onClick={send}
          disabled={!canSend}
          title="送出"
          style={{
            width: 36, height: 36, borderRadius: 999, border: 'none', cursor: canSend ? 'pointer' : 'default',
            background: canSend ? 'linear-gradient(180deg,#264b8b,#19376b)' : '#e2e8f0',
            color: canSend ? '#fff' : '#94a3b8',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            flexShrink: 0, transition: 'background 0.15s',
            boxShadow: canSend ? '0 4px 12px rgba(35,68,121,0.25)' : 'none',
          }}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  )
}
