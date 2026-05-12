import { useState } from 'react'
import {
  Activity,
  BookOpen,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import type { ConversationItem } from '../api'

interface SidebarProps {
  conversations: ConversationItem[]
  onNewChat: () => void
  onLoadConversation: (id: number) => void
  onDeleteConversation: (id: number) => void
  onRenameConversation?: (id: number, title: string) => void
  activeConversationId: number | null
  loading: boolean
  collapsed: boolean
  onToggleCollapse: () => void
  onOpenSummaries: () => void
  onOpenTraces: () => void
}

export default function Sidebar({
  conversations,
  onNewChat,
  onLoadConversation,
  onDeleteConversation,
  activeConversationId,
  loading,
  collapsed,
  onToggleCollapse,
  onOpenSummaries,
  onOpenTraces,
}: SidebarProps) {
  const [query, setQuery] = useState('')
  const [confirmId, setConfirmId] = useState<number | null>(null)

  const filtered = query.trim()
    ? conversations.filter((c) =>
        (c.title ?? `Chat #${c.id}`).toLowerCase().includes(query.toLowerCase())
      )
    : conversations

  const handleDeleteClick = (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    if (confirmId === id) {
      onDeleteConversation(id)
      setConfirmId(null)
    } else {
      setConfirmId(id)
    }
  }

  const cancelConfirm = (e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmId(null)
  }

  return (
    <div className={`sidebar ${collapsed ? 'sidebar-collapsed' : ''}`}>
      <div className="sidebar-header">
        {!collapsed && <span className="sidebar-title">Report Agent</span>}
        <button
          className="sidebar-toggle"
          onClick={onToggleCollapse}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
        </button>
      </div>

      {collapsed && (
        <>
          <button className="new-chat-icon-btn" onClick={onNewChat} disabled={loading} title="New chat">
            <Plus size={16} strokeWidth={2.5} />
          </button>
          <button className="new-chat-icon-btn" onClick={onOpenSummaries} title="Summaries">
            <BookOpen size={16} strokeWidth={1.8} />
          </button>
          <button className="new-chat-icon-btn" onClick={onOpenTraces} title="Traces">
            <Activity size={16} strokeWidth={1.8} />
          </button>
        </>
      )}

      {!collapsed && (
        <>
          <button className="new-chat-btn" onClick={onNewChat} disabled={loading}>
            <Plus size={15} strokeWidth={2.5} />
            New chat
          </button>

          <div className="sidebar-search-wrap">
            <Search size={13} className="sidebar-search-icon" />
            <input
              className="sidebar-search"
              placeholder="Search chats"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            {query && (
              <button className="sidebar-search-clear" onClick={() => setQuery('')} title="Clear">
                <X size={11} />
              </button>
            )}
          </div>

          <button className="new-chat-btn" onClick={onOpenSummaries} style={{ backgroundColor: 'transparent', border: '1px solid #45475a' }}>
            <BookOpen size={14} strokeWidth={1.8} />
            Summaries
          </button>

          <button className="new-chat-btn" onClick={onOpenTraces} style={{ backgroundColor: 'transparent', border: '1px solid #45475a' }}>
            <Activity size={14} strokeWidth={1.8} />
            Traces
          </button>

          <div className="section-label">Recent chats</div>

          <div className="conv-list">
            {filtered.length === 0 ? (
              <div className="doc-empty">{query ? 'No matching chats' : 'No chats yet'}</div>
            ) : (
              filtered.map((conv) => (
                <div
                  key={conv.id}
                  className={`conv-item ${activeConversationId === conv.id ? 'selected' : ''}`}
                  onClick={() => { if (!loading) { setConfirmId(null); onLoadConversation(conv.id) } }}
                >
                  <MessageSquare size={13} className="conv-icon" />
                  <div className="conv-info">
                    <span className="conv-title">
                      {conv.title ?? `Chat #${conv.id}`}
                    </span>
                    <span className="conv-meta-row">
                      <span className="conv-date">
                        {new Date(conv.created_at).toLocaleDateString('zh-TW', {
                          month: 'short',
                          day: 'numeric',
                        })}
                      </span>
                      <span className="conv-dot">-</span>
                      <span className="conv-count">{conv.message_count} msgs</span>
                    </span>
                  </div>

                  {confirmId === conv.id ? (
                    <div className="conv-confirm-row" onClick={(e) => e.stopPropagation()}>
                      <button className="conv-confirm-yes" onClick={(e) => handleDeleteClick(e, conv.id)}>Delete</button>
                      <button className="conv-confirm-no" onClick={cancelConfirm}>Cancel</button>
                    </div>
                  ) : (
                    <button
                      className="conv-delete-btn"
                      onClick={(e) => handleDeleteClick(e, conv.id)}
                      disabled={loading}
                      title="Delete chat"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}
