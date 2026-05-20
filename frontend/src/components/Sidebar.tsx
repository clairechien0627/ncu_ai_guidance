import { useState } from 'react'
import {
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
  onLoadConversation: (threadId: string) => void
  onDeleteConversation: (threadId: string) => void
  onRenameConversation?: (threadId: string, title: string) => void
  activeThreadId: string | null
  loading: boolean
  collapsed: boolean
  onToggleCollapse: () => void
}

export default function Sidebar({
  conversations,
  onNewChat,
  onLoadConversation,
  onDeleteConversation,
  activeThreadId,
  loading,
  collapsed,
  onToggleCollapse,
}: SidebarProps) {
  const [query, setQuery] = useState('')
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const filtered = query.trim()
    ? conversations.filter((c) =>
        (c.title ?? 'New chat').toLowerCase().includes(query.toLowerCase())
      )
    : conversations

  const handleDeleteClick = (e: React.MouseEvent, threadId: string) => {
    e.stopPropagation()
    if (confirmId === threadId) {
      onDeleteConversation(threadId)
      setConfirmId(null)
    } else {
      setConfirmId(threadId)
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

          <div className="section-label">Recent chats</div>

          <div className="conv-list">
            {filtered.length === 0 ? (
              <div className="doc-empty">{query ? 'No matching chats' : 'No chats yet'}</div>
            ) : (
              filtered.map((conv) => (
                <div
                  key={conv.thread_id}
                  className={`conv-item ${activeThreadId === conv.thread_id ? 'selected' : ''}`}
                  onClick={() => { if (!loading) { setConfirmId(null); onLoadConversation(conv.thread_id) } }}
                >
                  <MessageSquare size={13} className="conv-icon" />
                  <div className="conv-info">
                    <span className="conv-title">
                      {conv.title ?? 'New chat'}
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

                  {confirmId === conv.thread_id ? (
                    <div className="conv-confirm-row" onClick={(e) => e.stopPropagation()}>
                      <button className="conv-confirm-yes" onClick={(e) => handleDeleteClick(e, conv.thread_id)}>Delete</button>
                      <button className="conv-confirm-no" onClick={cancelConfirm}>Cancel</button>
                    </div>
                  ) : (
                    <button
                      className="conv-delete-btn"
                      onClick={(e) => handleDeleteClick(e, conv.thread_id)}
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
