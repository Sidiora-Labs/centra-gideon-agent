import type { OwnerScope } from '../auth.web'

export type ConversationMessage = Readonly<{
  id: string
  role: string
  content: string
  ts?: string
  meta?: Readonly<Record<string, unknown>>
  streaming?: boolean
}>

export type ConversationState = Readonly<{
  scope: OwnerScope | null
  sessionId: string | null
  title: string
  messages: readonly ConversationMessage[]
  draft: string
  phase: 'signed-out' | 'idle' | 'loading' | 'ready' | 'sending' | 'recovering' | 'failed' | 'uncertain'
  connected: boolean
  running: boolean
  error: string
}>

export type ChatHistoryMessage = Readonly<{
  role: string
  content: string
  ts?: string
  meta?: Readonly<Record<string, unknown>>
}>

export type ChatDetail = Readonly<{
  key: string
  title: string
  running: boolean
  messages: readonly ChatHistoryMessage[]
}>

export type ChatSocketEvent = Readonly<{
  type: string
  data: Readonly<Record<string, unknown>>
}>
