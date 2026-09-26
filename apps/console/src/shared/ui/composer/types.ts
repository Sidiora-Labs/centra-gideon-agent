import type { ReactNode } from 'react'
import type { AgentDef, AgentProvider, DiscoveredAgent, ModelItem, ApprovalMode, TaskMode, ReasoningEffort } from '../../data/api'

export interface ComposerControls {
  agent?: boolean
  model?: boolean
  approval?: boolean
  reasoning?: boolean
  attach?: boolean
  mic?: boolean
  optimize?: boolean
  slash?: boolean
}

export interface ComposerValue {
  agent: string
  model: string
  approval: ApprovalMode
  taskMode: TaskMode
  reasoning: ReasoningEffort
}

export interface ComposerData {
  agents: AgentDef[]
  providers: AgentProvider[]
  discovered: Record<string, DiscoveredAgent[]>
  models: ModelItem[]
  ready?: boolean
  agentsErr?: unknown
  retry?: () => void
}

export interface ComposerProps {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  streaming?: boolean
  processing?: boolean
  onStop?: () => void
  placeholder?: string
  controls?: ComposerControls
  data?: ComposerData
  selection?: ComposerValue
  onSelect?: (patch: Partial<ComposerValue>) => void
  onAttach?: (files: File[]) => void
  attachments?: readonly { id: string; name: string; meta?: string; kind?: 'image' | 'text' | 'archive'; state?: 'uploading' | 'done' | 'error'; progress?: number }[]
  onRemoveAttachment?: (id: string) => void
  onOpenAttachment?: (id: string) => void
  draftKey?: string
  contextUsage?: { input_tokens: number | null; cache_creation_tokens: number | null; cache_read_tokens: number | null; context_window_tokens: number | null; total_input_tokens?: number }
  onOpenPrompts?: () => void
  plusMenuExtra?: (close: () => void) => ReactNode
  onFocusChange?: (focused: boolean) => void
  mentionProject?: string
  onMentionFile?: (file: { path: string; name: string }) => void
  onMentionKnowledge?: (item: { id: string; name: string }) => void
  onLargePaste?: (text: string) => boolean
  onOptimize?: () => void
  optimizing?: boolean
  history?: string[]
  onTranscribe?: (blob: Blob, opts?: { duplex?: boolean }) => Promise<string>
  onMicError?: (msg: string) => void
  handsFree?: {
    confirmationPhrases: readonly string[]
    exitPhrases: readonly string[]
    speaking?: boolean
    muteWhileSpeaking?: boolean
  }
  onHandsFreeSubmit?: (text: string) => void
  screenShare?: {
    available: boolean
    sharing: boolean
    disabledReason?: string
    onToggle: () => void
  }
  naturalVoice?: {
    choice: '' | 'on' | 'off'
    effective: boolean
    source: string
    agentDefault: boolean
    onSelect: (choice: '' | 'on' | 'off') => void
  }
  canQueue?: boolean
  contextPct?: number
  minChars?: number
  openModelSignal?: number
  auiModelSelector?: boolean
  openAgentSignal?: number
  openReasoningSignal?: number
}
