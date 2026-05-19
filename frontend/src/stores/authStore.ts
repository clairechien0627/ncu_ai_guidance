import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_URL ?? ''

export interface AuthUser {
  user_id: number
  username: string
  email?: string
  display_name: string | null
  role: 'user' | 'admin'
  quota_tokens_per_day: number | null
  quota_requests_per_day: number | null
}

interface AuthState {
  token: string | null
  user: AuthUser | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  refreshMe: () => Promise<void>
  setFromResponse: (data: any) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,

      setFromResponse: (data: any) => {
        set({ token: data.access_token, user: data as AuthUser })
      },

      login: async (email: string, password: string) => {
        const { data } = await axios.post(`${API_BASE}/api/auth/login`, { email, password })
        set({ token: data.access_token, user: data as AuthUser })
      },

      logout: () => set({ token: null, user: null }),

      refreshMe: async () => {
        const { token } = get()
        if (!token) return
        try {
          const { data } = await axios.get(`${API_BASE}/api/auth/me`, {
            headers: { Authorization: `Bearer ${token}` },
          })
          set({ user: data as AuthUser })
        } catch {
          set({ token: null, user: null })
        }
      },
    }),
    { name: 'auth-store' }
  )
)
