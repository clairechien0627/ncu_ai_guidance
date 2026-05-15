import { useState } from 'react'

export interface UseInlineEditReturn {
  editing: boolean
  setEditing: React.Dispatch<React.SetStateAction<boolean>>
  draft: string
  setDraft: React.Dispatch<React.SetStateAction<string>>
  saving: boolean
  setSaving: React.Dispatch<React.SetStateAction<boolean>>
  startEdit: (currentValue: string) => void
  cancelEdit: () => void
}

/** Generic hook for inline text field editing (title, abstract, etc.). */
export function useInlineEdit(): UseInlineEditReturn {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  const startEdit = (currentValue: string) => {
    setDraft(currentValue)
    setEditing(true)
  }

  const cancelEdit = () => setEditing(false)

  return { editing, setEditing, draft, setDraft, saving, setSaving, startEdit, cancelEdit }
}
