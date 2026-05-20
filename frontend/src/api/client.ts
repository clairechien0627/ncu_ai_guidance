import axios from 'axios'

export const API_BASE = import.meta.env.VITE_API_URL ?? ''

const api = axios.create({ baseURL: `${API_BASE}/api` })

api.interceptors.request.use(config => {
  try {
    const stored = localStorage.getItem('auth-store')
    if (stored) {
      const token = JSON.parse(stored)?.state?.token
      if (token) config.headers['Authorization'] = `Bearer ${token}`
    }
  } catch { /* ignore */ }
  return config
})

export default api
