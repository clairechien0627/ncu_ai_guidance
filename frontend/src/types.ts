export interface Message {
  role: 'user' | 'assistant'
  content: string
  sources?: string[]
  attachedDocs?: { name: string; id: number }[]
  mode?: string | null
  agentName?: string | null
  promptName?: string | null
  promptVersion?: string | null
  traceRunId?: string | null
  stage?: string | null
}
