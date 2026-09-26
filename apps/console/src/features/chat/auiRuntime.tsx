import { createContext, useContext, useMemo, type ReactNode } from 'react'
import {
  AssistantRuntimeProvider,
  useExternalStoreRuntime,
  type AppendMessage,
  type ExternalStoreAdapter,
  type ExternalThreadQueueAdapter,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import type { ChatTurn, Segment } from './chatTypes'

export interface GideonChatRuntimeProps {
  sessionId: string | null
  turns: readonly ChatTurn[]
  streaming: boolean
  queued?: readonly { id: string; content: string }[]
  sessions?: readonly { key: string; title: string; lifecycle?: 'active' | 'archived' }[]
  suggestions?: readonly string[]
  onSwitchSession?: (key: string) => void | Promise<void>
  onNewSession?: () => void | Promise<void>
  onSend: (text: string) => void | Promise<void>
  onStop: () => void | Promise<void>
  onEdit: (turnIndex: number, text: string) => void | Promise<void>
  onReload: () => void | Promise<void>
  onQueue?: (text: string, lane: 'queue' | 'steer') => void | Promise<void>
  onQueueRemove?: (id: string) => void | Promise<void>
  onQueueEdit?: (id: string, text: string) => void | Promise<void>
  onQueueInterrupt?: (id: string) => void | Promise<void>
  children: ReactNode
}

export type GideonTurnRef = { turn: ChatTurn; index: number }
const TurnByAuiId = createContext<ReadonlyMap<string, GideonTurnRef>>(new Map())

export function useGideonTurnByAuiId(): ReadonlyMap<string, GideonTurnRef> {
  return useContext(TurnByAuiId)
}

export function gideonAuiId(sessionId: string | null, ordinal: number): string {
  return `gideon:${encodeURIComponent(sessionId ?? 'new')}:${ordinal}`
}

export function appendText(message: AppendMessage): string {
  const text = message.content.filter((part) => part.type === 'text').map((part) => part.text).join('\n').trim()
  if (message.content.some((part) => part.type !== 'text')) {
    throw new Error('Gideon composer must handle non-text attachments before sending')
  }
  return text
}

function segmentPart(segment: Segment) {
  switch (segment.kind) {
    case 'text': return { type: 'text' as const, text: segment.text }
    case 'thinking': return { type: 'reasoning' as const, text: segment.text }
    case 'tool': return {
      type: 'tool-call' as const,
      toolCallId: segment.id,
      toolName: segment.tool,
      argsText: segment.input ?? '{}',
      result: segment.done ? segment.output : undefined,
      isError: segment.ok === false,
    }
    default: return { type: 'data' as const, name: `gideon-${segment.kind}`, data: segment }
  }
}

export function convertGideonTurn(turn: ChatTurn, ordinal: number, sessionId: string | null, streaming: boolean): ThreadMessageLike {
  const createdAt = turn.ts ? new Date(turn.ts) : undefined
  const isCurrent = streaming && turn.role === 'assistant'
  const hasError = turn.segments.some((segment) => segment.kind === 'error')
  return {
    id: gideonAuiId(sessionId, ordinal),
    role: turn.role,
    content: turn.segments.map(segmentPart),
    ...(createdAt && !Number.isNaN(createdAt.getTime()) ? { createdAt } : {}),
    ...(turn.role === 'assistant' ? {
      status: isCurrent ? { type: 'running' as const }
        : hasError ? { type: 'incomplete' as const, reason: 'error' as const }
          : { type: 'complete' as const, reason: 'stop' as const },
    } : {}),
  }
}

export function makeGideonQueueAdapter({
  queued = [], streaming, onQueue, onQueueRemove, onQueueEdit, onQueueInterrupt,
}: Pick<GideonChatRuntimeProps, 'queued' | 'streaming' | 'onQueue' | 'onQueueRemove' | 'onQueueEdit' | 'onQueueInterrupt'>): ExternalThreadQueueAdapter | undefined {
  if ((!streaming && queued.length === 0) || !onQueue || !onQueueRemove || !onQueueEdit || !onQueueInterrupt) return undefined
  return {
    items: queued.map(({ id, content }) => ({ id, prompt: content, parts: [{ type: 'text' as const, text: content }] })),
    steerItems: [],
    enqueue: (message) => { void onQueue(appendText(message), 'queue') },
    steer: (message) => { void onQueue(appendText(message), 'steer') },
    edit: (id, message) => { void onQueueEdit(id, appendText(message)) },
    remove: (id) => { void onQueueRemove(id) },
    move: (id, placement) => {
      if (placement.lane === 'steer' && placement.insertAfter == null && placement.insertBefore == null) {
        void onQueueInterrupt(id)
      } else {
        throw new Error('Gideon does not support queue reordering')
      }
    },
  }
}

export async function reloadLatestGideonAnswer(
  parentId: string | null, sessionId: string | null, turns: readonly ChatTurn[], onReload: () => void | Promise<void>,
): Promise<void> {
  const lastUserIndex = turns.findLastIndex((turn) => turn.role === 'user')
  if (lastUserIndex < 0 || parentId !== gideonAuiId(sessionId, lastUserIndex)) {
    throw new Error('Gideon can regenerate only the latest answer')
  }
  await onReload()
}

export function GideonChatRuntimeProvider({
  sessionId, turns, streaming, queued = [], sessions, suggestions = [],
  onSwitchSession, onNewSession, onSend, onStop, onEdit, onReload,
  onQueue, onQueueRemove, onQueueEdit, onQueueInterrupt, children,
}: GideonChatRuntimeProps) {
  const turnByAuiId = useMemo(() => new Map(turns.map((turn, index) => [
    gideonAuiId(sessionId, index), { turn, index },
  ])), [sessionId, turns])

  const queue = useMemo(() => makeGideonQueueAdapter({ queued, streaming, onQueue, onQueueRemove, onQueueEdit, onQueueInterrupt }),
    [queued, streaming, onQueue, onQueueRemove, onQueueEdit, onQueueInterrupt])

  const adapter: ExternalStoreAdapter<ChatTurn> = {
    messages: turns,
    isRunning: streaming,
    suggestions: suggestions.map((prompt) => ({ prompt })),
    convertMessage: (turn, index) => convertGideonTurn(turn, index, sessionId, streaming && index === turns.length - 1),
    onNew: async (message) => { await onSend(appendText(message)) },
    onCancel: async () => { await onStop() },
    onEdit: async (message) => {
      const target = message.sourceId && turnByAuiId.get(message.sourceId)
      if (!target || target.turn.role !== 'user') throw new Error('Cannot edit a turn outside this Gideon session')
      await onEdit(target.index, appendText(message))
    },
    onReload: async (parentId) => { await reloadLatestGideonAnswer(parentId, sessionId, turns, onReload) },
    ...(sessions && onSwitchSession && onNewSession ? {
      adapters: {
        threadList: {
          threadId: sessionId ?? undefined,
          threads: sessions.filter((session) => session.lifecycle !== 'archived').map((session) => ({
            id: session.key, title: session.title, status: 'regular' as const,
          })),
          archivedThreads: sessions.filter((session) => session.lifecycle === 'archived').map((session) => ({
            id: session.key, title: session.title, status: 'archived' as const,
          })),
          onSwitchToThread: onSwitchSession,
          onSwitchToNewThread: onNewSession,
        },
      },
    } : {}),
    ...(queue ? { queue } : {}),
  }
  const runtime = useExternalStoreRuntime(adapter)

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TurnByAuiId.Provider value={turnByAuiId}>{children}</TurnByAuiId.Provider>
    </AssistantRuntimeProvider>
  )
}
