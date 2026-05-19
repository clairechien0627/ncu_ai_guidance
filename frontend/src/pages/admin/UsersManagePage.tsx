import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, ShieldCheck, User, ToggleLeft, ToggleRight, KeyRound } from 'lucide-react'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL ?? ''
const api = axios.create({ baseURL: `${API_BASE}/api` })
api.interceptors.request.use(cfg => {
  try { const s = localStorage.getItem('auth-store'); const t = s ? JSON.parse(s)?.state?.token : null; if (t) cfg.headers['Authorization'] = `Bearer ${t}` } catch {}
  return cfg
})

interface ManagedUser {
  id: number; email: string; username: string; display_name: string | null
  role: string; is_active: boolean
  quota_tokens_per_day: number | null; quota_requests_per_day: number | null
  created_at: string | null
}

function CreateUserModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({ email: '', username: '', password: '', role: 'user', display_name: '', quota_tokens_per_day: '', quota_requests_per_day: '' })
  const [error, setError] = useState<string | null>(null)
  const mut = useMutation({ mutationFn: (body: any) => api.post('/users', body).then(r => r.data), onSuccess: () => { qc.invalidateQueries({ queryKey: ['manage-users'] }); onClose() }, onError: (e: any) => setError(e?.response?.data?.detail ?? '建立失敗') })

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    mut.mutate({ ...form, quota_tokens_per_day: form.quota_tokens_per_day ? Number(form.quota_tokens_per_day) : null, quota_requests_per_day: form.quota_requests_per_day ? Number(form.quota_requests_per_day) : null })
  }

  return (
    <div style={M.overlay} onClick={e => e.target === e.currentTarget && onClose()}>
      <div style={M.modal}>
        <div style={M.title}>建立使用者</div>
        <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {(['email', 'username', 'password', 'display_name'] as const).map(f => (
            <label key={f} style={M.label}>{f}
              <input className="adm-input" style={{ fontSize: 13, padding: '7px 10px' }}
                type={f === 'password' ? 'password' : 'text'} required={f !== 'display_name'}
                value={(form as any)[f]} onChange={e => setForm(p => ({ ...p, [f]: e.target.value }))} />
            </label>
          ))}
          <label style={M.label}>Role
            <select className="adm-input" style={{ fontSize: 13, padding: '7px 10px' }} value={form.role} onChange={e => setForm(p => ({ ...p, role: e.target.value }))}>
              <option value="user">user</option>
              <option value="admin">admin</option>
            </select>
          </label>
          <div style={{ display: 'flex', gap: 10 }}>
            <label style={{ ...M.label, flex: 1 }}>每日 Token 上限（空=無限）
              <input className="adm-input" style={{ fontSize: 13, padding: '7px 10px' }} type="number" value={form.quota_tokens_per_day} onChange={e => setForm(p => ({ ...p, quota_tokens_per_day: e.target.value }))} />
            </label>
            <label style={{ ...M.label, flex: 1 }}>每日請求上限
              <input className="adm-input" style={{ fontSize: 13, padding: '7px 10px' }} type="number" value={form.quota_requests_per_day} onChange={e => setForm(p => ({ ...p, quota_requests_per_day: e.target.value }))} />
            </label>
          </div>
          {error && <div style={{ color: 'var(--adm-red)', fontSize: 12 }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            <button type="button" className="adm-btn adm-btn-secondary" onClick={onClose}>取消</button>
            <button type="submit" className="adm-btn adm-btn-primary" disabled={mut.isPending}>
              {mut.isPending ? '建立中…' : '建立'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function UsersManagePage() {
  const qc = useQueryClient()
  const [showCreate, setShowCreate] = useState(false)
  const { data: users = [], isLoading } = useQuery<ManagedUser[]>({ queryKey: ['manage-users'], queryFn: () => api.get('/users').then(r => r.data) })

  const toggle = useMutation({ mutationFn: ({ id, is_active }: { id: number; is_active: boolean }) => api.patch(`/users/${id}`, { is_active }).then(r => r.data), onSuccess: () => qc.invalidateQueries({ queryKey: ['manage-users'] }) })
  const setRole = useMutation({ mutationFn: ({ id, role }: { id: number; role: string }) => api.patch(`/users/${id}`, { role }).then(r => r.data), onSuccess: () => qc.invalidateQueries({ queryKey: ['manage-users'] }) })

  return (
    <div style={{ padding: '24px 28px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: 'var(--adm-text)' }}>使用者管理</h2>
        <button className="adm-btn adm-btn-primary" onClick={() => setShowCreate(true)}>
          <Plus size={14} style={{ marginRight: 4 }} />建立使用者
        </button>
      </div>

      {isLoading ? (
        <div style={{ textAlign: 'center', padding: 40 }}><span className="adm-spinner" /></div>
      ) : (
        <div className="adm-table-wrap">
          <table className="adm-table" style={{ tableLayout: 'fixed', width: '100%' }}>
            <colgroup>
              <col style={{ width: 220 }} /><col style={{ width: 120 }} /><col style={{ width: 140 }} />
              <col style={{ width: 80 }} /><col style={{ width: 140 }} /><col style={{ width: 140 }} />
              <col style={{ width: 100 }} />
            </colgroup>
            <thead><tr>
              <th>Email</th><th>Username</th><th>顯示名稱</th>
              <th>Role</th><th>Token 上限/日</th><th>請求上限/日</th><th>狀態</th>
            </tr></thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id} className={u.is_active ? '' : 'adm-row--danger'}>
                  <td style={{ fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis' }}>{u.email}</td>
                  <td className="adm-cell-mono" style={{ fontSize: 12 }}>{u.username}</td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{u.display_name || '—'}</td>
                  <td>
                    <button
                      className={`adm-badge ${u.role === 'admin' ? 'adm-badge--info' : ''}`}
                      style={{ cursor: 'pointer', border: 'none', background: 'none' }}
                      title="切換 role"
                      onClick={() => setRole.mutate({ id: u.id, role: u.role === 'admin' ? 'user' : 'admin' })}
                    >
                      {u.role === 'admin' ? <ShieldCheck size={11} style={{ marginRight: 3 }} /> : <User size={11} style={{ marginRight: 3 }} />}
                      {u.role}
                    </button>
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{u.quota_tokens_per_day?.toLocaleString() ?? '無限'}</td>
                  <td style={{ fontSize: 12, color: 'var(--adm-text-2)' }}>{u.quota_requests_per_day?.toLocaleString() ?? '無限'}</td>
                  <td>
                    <button
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: u.is_active ? 'var(--adm-green)' : 'var(--adm-text-3)', display: 'flex', alignItems: 'center', gap: 4, fontSize: 12 }}
                      onClick={() => toggle.mutate({ id: u.id, is_active: !u.is_active })}
                      title={u.is_active ? '停用' : '啟用'}
                    >
                      {u.is_active ? <ToggleRight size={18} /> : <ToggleLeft size={18} />}
                      {u.is_active ? '啟用' : '停用'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showCreate && <CreateUserModal onClose={() => setShowCreate(false)} />}
    </div>
  )
}

const M: Record<string, React.CSSProperties> = {
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.35)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 },
  modal: { background: '#fff', borderRadius: 12, padding: '28px 28px 24px', width: 460, boxShadow: '0 8px 32px rgba(0,0,0,0.15)' },
  title: { fontSize: 16, fontWeight: 700, color: '#0f172a', marginBottom: 20 },
  label: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: '#374151', fontWeight: 500 },
}
