import { useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Eye, EyeOff } from 'lucide-react'
import { GoogleLogin, GoogleOAuthProvider } from '@react-oauth/google'
import { useAuthStore } from '../stores/authStore'
import axios from 'axios'

const API_BASE_URL = import.meta.env.VITE_API_URL ?? ''
const api = axios.create({ baseURL: `${API_BASE_URL}/api` })

const GOOGLE_CLIENT_ID = import.meta.env.VITE_GOOGLE_CLIENT_ID ?? ''

function PasswordInput({ value, onChange, placeholder = '••••••••' }: {
  value: string; onChange: (v: string) => void; placeholder?: string
}) {
  const [show, setShow] = useState(false)
  return (
    <div style={{ position: 'relative' }}>
      <input
        style={{ ...S.input, width: '100%', boxSizing: 'border-box', paddingRight: 40 }}
        type={show ? 'text' : 'password'}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="current-password"
        required
      />
      <button type="button" onClick={() => setShow(v => !v)} style={S.eyeBtn} tabIndex={-1}>
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  )
}

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const login = useAuthStore(s => s.login)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null); setLoading(true)
    try { await login(email, password); onSuccess() }
    catch (err: any) { setError(err?.response?.data?.detail ?? '登入失敗') }
    finally { setLoading(false) }
  }

  return (
    <form onSubmit={handleSubmit} style={S.form}>
      <label style={S.label}>Email 或 Username
        <input style={S.input} type="text" value={email} onChange={e => setEmail(e.target.value)}
          placeholder="your@email.com 或 username" autoComplete="username email" required />
      </label>
      <label style={S.label}>密碼
        <PasswordInput value={password} onChange={setPassword} />
      </label>
      {error && <div style={S.error}>{error}</div>}
      <button style={S.btn} type="submit" disabled={loading}>
        {loading ? '登入中…' : '登入'}
      </button>
    </form>
  )
}

function RegisterForm({ onSuccess }: { onSuccess: () => void }) {
  const setAuth = useAuthStore(s => s.setFromResponse)
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null); setLoading(true)
    try {
      const { data } = await api.post('/auth/register', { email, username, password, display_name: displayName || undefined })
      setAuth(data)
      onSuccess()
    } catch (err: any) { setError(err?.response?.data?.detail ?? '註冊失敗') }
    finally { setLoading(false) }
  }

  return (
    <form onSubmit={handleSubmit} style={S.form}>
      <label style={S.label}>Email
        <input style={S.input} type="email" value={email} onChange={e => setEmail(e.target.value)}
          placeholder="your@email.com" autoComplete="email" required />
      </label>
      <label style={S.label}>Username（帳號名稱）
        <input style={S.input} type="text" value={username} onChange={e => setUsername(e.target.value)}
          placeholder="your_username" required />
      </label>
      <label style={S.label}>顯示名稱（可選）
        <input style={S.input} type="text" value={displayName} onChange={e => setDisplayName(e.target.value)}
          placeholder="王小明" />
      </label>
      <label style={S.label}>密碼（至少 8 個字元）
        <PasswordInput value={password} onChange={setPassword} placeholder="至少 8 個字元" />
      </label>
      {error && <div style={S.error}>{error}</div>}
      <button style={S.btn} type="submit" disabled={loading}>
        {loading ? '建立中…' : '建立帳號'}
      </button>
    </form>
  )
}

export default function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const setAuth = useAuthStore(s => s.setFromResponse)
  const [tab, setTab] = useState<'login' | 'register'>('login')

  const from = (location.state as any)?.from?.pathname ?? '/chat'
  const onSuccess = () => navigate(from, { replace: true })

  const handleGoogle = async (credentialResponse: any) => {
    try {
      const { data } = await api.post('/auth/google', { credential: credentialResponse.credential })
      setAuth(data)
      onSuccess()
    } catch (err: any) {
      console.error('Google login failed', err)
    }
  }

  return (
    <div style={S.page}>
      <div style={S.card}>
        <div style={S.logo}>Report Agent</div>

        <div style={S.tabs}>
          <button style={{ ...S.tab, ...(tab === 'login' ? S.tabActive : {}) }} onClick={() => setTab('login')}>登入</button>
          <button style={{ ...S.tab, ...(tab === 'register' ? S.tabActive : {}) }} onClick={() => setTab('register')}>註冊</button>
        </div>

        {tab === 'login' ? <LoginForm onSuccess={onSuccess} /> : <RegisterForm onSuccess={onSuccess} />}

        {GOOGLE_CLIENT_ID && (
          <>
            <div style={S.divider}><span>或</span></div>
            <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
              <div style={{ display: 'flex', justifyContent: 'center' }}>
                <GoogleLogin
                  onSuccess={handleGoogle}
                  onError={() => console.error('Google login error')}
                  text={tab === 'login' ? 'signin_with' : 'signup_with'}
                  shape="rectangular"
                  width="300"
                />
              </div>
            </GoogleOAuthProvider>
          </>
        )}
      </div>
    </div>
  )
}

const S: Record<string, React.CSSProperties> = {
  page: { minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#f1f5f9' },
  card: { background: '#fff', borderRadius: 12, boxShadow: '0 4px 24px rgba(0,0,0,0.08)', padding: '36px 32px', width: 360, display: 'flex', flexDirection: 'column', gap: 0 },
  logo: { fontSize: 22, fontWeight: 700, color: '#0f172a', marginBottom: 20 },
  tabs: { display: 'flex', gap: 0, marginBottom: 20, border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' },
  tab: { flex: 1, padding: '9px', border: 'none', background: 'transparent', fontSize: 13, fontWeight: 500, color: '#64748b', cursor: 'pointer' },
  tabActive: { background: '#2563eb', color: '#fff', fontWeight: 600 },
  form: { display: 'flex', flexDirection: 'column', gap: 14 },
  label: { display: 'flex', flexDirection: 'column', gap: 5, fontSize: 13, color: '#374151', fontWeight: 500 },
  input: { padding: '9px 12px', border: '1px solid #e2e8f0', borderRadius: 7, fontSize: 14, outline: 'none' },
  error: { background: '#fef2f2', color: '#dc2626', fontSize: 13, borderRadius: 6, padding: '8px 12px' },
  btn: { marginTop: 4, padding: '10px', background: '#2563eb', color: '#fff', border: 'none', borderRadius: 7, fontSize: 14, fontWeight: 600, cursor: 'pointer' },
  eyeBtn: { position: 'absolute' as const, right: 10, top: '50%', transform: 'translateY(-50%)', background: 'none', border: 'none', cursor: 'pointer', color: '#94a3b8', display: 'flex', alignItems: 'center', padding: 0 },
  divider: { textAlign: 'center' as const, color: '#94a3b8', fontSize: 12, margin: '16px 0', position: 'relative' as const, borderTop: '1px solid #e2e8f0', paddingTop: 12 },
}
