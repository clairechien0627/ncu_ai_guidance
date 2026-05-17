import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface AdminState {
  environment: string | null
  setEnvironment: (env: string | null) => void
}

export const useAdminStore = create<AdminState>()(
  persist(
    (set) => ({
      environment: null,
      setEnvironment: (env) => set({ environment: env }),
    }),
    { name: 'admin-store' },
  ),
)
