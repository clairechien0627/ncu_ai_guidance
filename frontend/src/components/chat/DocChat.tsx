import { Suspense, lazy, useEffect, useRef, useState } from 'react'
import { Bot, FileText, MessageCircle, Send, Trash2 } from 'lucide-react'
import { sendMessageStream } from '../../api'
import { mapStage } from '../../utils/stageMap'

const MarkdownRenderer = lazy(() => import('../MarkdownRenderer'))

interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  streaming?: boolean
  stage?: string | null
}

interface Props {
  docId: number
  filename?: string
}

export default function DocChat({ docId, filename: _filename }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [convId, setConvId] = useState<string | null>(null)
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
      { role: 'assistant', content: '', streaming: true, stage: null },
    ])
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
          if (last.role === 'assistant') next[next.length - 1] = { ...last, streaming: false, sources, stage: null }
          return next
        })
        setBusy(false)
      },
      (err) => {
        setMessages(prev => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last.role === 'assistant') next[next.length - 1] = { ...last, content: `（錯誤：${err}）`, streaming: false, stage: null }
          return next
        })
        setBusy(false)
      },
      undefined,
      (stage) => {
        setMessages(prev => {
          const next = [...prev]
          const last = next[next.length - 1]
          if (last.role === 'assistant') next[next.length - 1] = { ...last, stage }
          return next
        })
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
    <div className="doc-chat-root">

      {/* Always-visible header */}
      <div className="doc-chat-header">
        <div className="doc-chat-header-left">
          <div className="doc-avatar doc-avatar--lg">
            <Bot size={17} strokeWidth={1.8} />
          </div>
          <div className="doc-chat-bot-info">
            <div className="doc-chat-bot-name">論文 AI 助理</div>
            <div className="doc-chat-bot-status">
              <span className="doc-chat-online-dot" />
              線上
            </div>
          </div>
        </div>
        {messages.length > 0 && (
          <button
            onClick={() => { setMessages([]); setConvId(null) }}
            aria-label="清除對話"
            title="清除對話"
            className="doc-chat-clear-btn"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="doc-chat-messages doc-chat-body">
        {messages.length === 0 && (
          <div className="doc-chat-empty">
            <div className="doc-chat-empty-icon">
              <MessageCircle size={20} color="#94a3b8" strokeWidth={1.5} />
            </div>
            <div className="doc-chat-empty-title">針對這篇論文提問</div>
            <div className="doc-chat-empty-hint">
              例如：這篇的研究方法是什麼？<br />主要結論為何？
            </div>
          </div>
        )}

        {messages.map((msg, i) => {
          const isWaiting = busy && i === messages.length - 1 && msg.role === 'assistant' && msg.content === ''
          const isStreaming = !!(msg.streaming && msg.content !== '')
          return (
            <div key={i} className={`doc-msg-row ${msg.role}`}>
              {msg.role === 'user' ? (
                <div className="doc-chat-user-bubble">{msg.content}</div>
              ) : (
                <div className="doc-ai-row">
                  <div className="doc-avatar doc-avatar--sm">
                    <Bot size={13} strokeWidth={1.8} />
                  </div>
                  <div className={`doc-ai-bubble${isStreaming ? ' streaming' : ''}`}>
                    {isWaiting ? (
                      <div className="doc-thinking">
                        {(() => {
                          const label = msg.stage ? mapStage(msg.stage) : null
                          const display = label ?? (msg.stage ? null : '準備中')
                          return display ? (
                            <span key={msg.stage ?? '__init__'} className="doc-thinking-text">
                              {display}
                            </span>
                          ) : null
                        })()}
                        <div className="thinking-pulse">
                          <span /><span /><span />
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className={isStreaming ? 'is-streaming' : ''}>
                          <Suspense fallback={<div className="markdown-body">{msg.content}</div>}>
                            <MarkdownRenderer content={msg.content} />
                          </Suspense>
                        </div>
                        {!isStreaming && msg.sources && msg.sources.length > 0 && (
                          <div className="doc-sources">
                            {msg.sources.map((s, si) => (
                              <span key={si} className="source-chip">
                                <FileText size={10} style={{ flexShrink: 0, opacity: 0.6 }} />
                                {s}
                              </span>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Input dock */}
      <div className="doc-chat-input-dock">
        <textarea
          ref={textareaRef}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKey}
          placeholder="輸入問題，Enter 送出…"
          rows={1}
          disabled={busy}
          className="doc-chat-input"
        />
        <button
          onClick={send}
          disabled={!canSend}
          aria-label="送出訊息"
          title="送出"
          className={`doc-chat-send${canSend ? ' active' : ''}`}
        >
          {busy ? (
            <span className="doc-chat-send-spinner" />
          ) : (
            <Send size={15} />
          )}
        </button>
      </div>
    </div>
  )
}
