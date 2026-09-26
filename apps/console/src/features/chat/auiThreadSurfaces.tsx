import { useMemo, useState, type ReactNode, type Ref } from 'react'
import { X } from 'lucide-react'
import { ConversationSearch, type SearchHit } from '../../shared/vendor/assistant-ui/elements/conversation-search'
import { ThreadSearch } from '../../shared/vendor/assistant-ui/elements/thread-search'
import { ChatPanel, ChatPanelAssistantMessage, ChatPanelMessages, ChatPanelTyping, ChatPanelUserMessage } from '../../shared/vendor/assistant-ui/elements/chat-panel'
import { EmptyState as AuiEmptyState, EmptyStateGreeting, EmptyStateSuggestion, EmptyStateSuggestions } from '../../shared/vendor/assistant-ui/elements/empty-state'
import { ConnectionState } from '../../shared/vendor/assistant-ui/elements/connection-state'
import { SharedConversation } from '../../shared/vendor/assistant-ui/elements/shared-conversation'
import { findInText } from '../../shared/ui/findText'
import { findSegments } from './findSegments'
import { turnText, type ChatTurn } from './chatTypes'
import { sessionTitle } from '../../shared/data/sessionTitle'
import type { ChatSessionSummary, ChatSessionShare, ChatSessionShareDetail } from '../../shared/data/api'

export function ThreadConversationSearch({ turns, nodeOf, onClose }: {
  turns: readonly ChatTurn[]
  nodeOf: (index: number) => HTMLElement | null | undefined
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const hits = useMemo(() => {
    if (!query.trim()) return []
    return turns.flatMap((turn, turnIndex) => findSegments(turn).flatMap((text, segmentIndex) =>
      findInText(text, query).map(({ start, end }, matchIndex): SearchHit & { turnIndex: number } => ({
        id: `${turnIndex}:${segmentIndex}:${matchIndex}`,
        turnIndex,
        before: text.slice(Math.max(0, start - 36), start),
        match: text.slice(start, end),
        after: text.slice(end, end + 36),
        position: turns.length > 1 ? 100 * turnIndex / (turns.length - 1) : 0,
      }))))
  }, [query, turns])
  const current = hits.length ? ((active % hits.length) + hits.length) % hits.length : -1
  const step = (delta: number) => {
    if (!hits.length) return
    const next = (current + delta + hits.length) % hits.length
    setActive(next)
    nodeOf(hits[next].turnIndex)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
  return <div role="search" className="sticky top-2 z-30 ml-auto mr-l flex max-w-[calc(100%-2rem)] items-start gap-1" onKeyDown={(event) => {
    if (event.key === 'Escape') { event.stopPropagation(); onClose() }
    if (event.key === 'Enter') { event.preventDefault(); step(event.shiftKey ? -1 : 1) }
  }}>
    <ConversationSearch query={query} hits={hits} activeIndex={current} onQueryChange={(value) => { setQuery(value); setActive(0) }} onStep={step}/>
    <button type="button" aria-label="Close find" onClick={onClose} className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-high"><X size={15}/></button>
  </div>
}

export function ThreadSessionSearch({ sessions, activeId, onSelect }: {
  sessions: readonly ChatSessionSummary[]
  activeId: string
  onSelect: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const threads = useMemo(() => sessions.filter((session) => (session.origin ?? 'manual') === 'manual').map((session) => ({
    id: session.key,
    title: sessionTitle(session),
    group: session.lifecycle === 'archived' ? 'Archived' : 'Conversations',
    preview: session.last_message ?? session.prompt_preview ?? '',
    pinned: !!session.pinned,
  })), [sessions])
  return <ThreadSearch threads={threads} query={query} activeId={activeId} onQueryChange={setQuery} onSelect={onSelect}/>
}

export function ThreadChatPreview({ turns, streamingText, busy = false, renderAssistant, endRef }: {
  turns: readonly ChatTurn[]
  streamingText?: string | null
  busy?: boolean
  renderAssistant?: (text: string) => ReactNode
  endRef?: Ref<HTMLDivElement>
}) {
  return <ChatPanel className="h-full max-h-none max-w-none">
    <ChatPanelMessages className="justify-start">
      {turns.slice(-12).map((turn, index) => turn.role === 'user'
        ? <ChatPanelUserMessage key={index}>{turnText(turn)}</ChatPanelUserMessage>
        : <ChatPanelAssistantMessage key={index}>{renderAssistant ? renderAssistant(turnText(turn)) : turnText(turn)}</ChatPanelAssistantMessage>)}
      {streamingText && <ChatPanelAssistantMessage>{renderAssistant ? renderAssistant(streamingText) : streamingText}</ChatPanelAssistantMessage>}
      {busy && !streamingText && <ChatPanelTyping/>}
      <div ref={endRef}/>
    </ChatPanelMessages>
  </ChatPanel>
}

export function recentCanvasTurns(turns: readonly ChatTurn[]): { speaker: 'user' | 'assistant'; text: string }[] {
  return turns.map((turn) => ({ speaker: turn.role, text: turnText(turn).trim() }))
    .filter((turn) => turn.text.length > 0).slice(-4)
    .map((turn) => ({ ...turn, text: turn.text.slice(0, 500) }))
}

export function appendSelectionQuote(previous: string, selected: string, attribution?: string): string {
  const text = selected.trim()
  if (!text) return previous
  const lines = text.split('\n').map((line) => `> ${line}`)
  const block = attribution ? `> **${attribution} said:**\n${lines.join('\n')}` : lines.join('\n')
  return previous ? `${previous}\n\n${block}\n\n` : `${block}\n\n`
}

export type ActualContextUsage = {
  input_tokens: number | null
  cache_creation_tokens: number | null
  cache_read_tokens: number | null
  context_window_tokens: number | null
  total_input_tokens?: number
}

export function readActualContextUsage(value: unknown): ActualContextUsage | undefined {
  if (!value || typeof value !== 'object') return undefined
  const record = value as Record<string, unknown>
  const fields = ['input_tokens', 'cache_creation_tokens', 'cache_read_tokens', 'context_window_tokens'] as const
  if (fields.some((field) => record[field] !== null && (typeof record[field] !== 'number' || !Number.isFinite(record[field])))) return undefined
  if (fields.some((field) => !(field in record))) return undefined
  const total = record.total_input_tokens
  if (total !== undefined && (typeof total !== 'number' || !Number.isFinite(total) || total < 0)) return undefined
  return { ...Object.fromEntries(fields.map((field) => [field, record[field]])), ...(total === undefined ? {} : { total_input_tokens: total }) } as ActualContextUsage
}

export function measuredContextDisplay(value: ActualContextUsage | undefined) {
  if (!value) return null
  const { input_tokens: input, cache_creation_tokens: created, cache_read_tokens: read, context_window_tokens: window } = value
  if (window === null || window <= 0) return null
  const complete = input !== null && created !== null && read !== null && input >= 0 && created >= 0 && read >= 0
  const total = value.total_input_tokens ?? (complete ? input + created + read : undefined)
  if (total === undefined) return null
  return { modelContextWindow: window, usage: { totalTokens: total, ...(complete ? { inputTokens: input + created, cachedInputTokens: read } : {}) } }
}

export function ThreadEmptyWelcome({ greeting, suggestions, onPick }: {
  greeting: string
  suggestions: readonly string[]
  onPick: (text: string) => void
}) {
  return <AuiEmptyState><EmptyStateGreeting>{greeting}</EmptyStateGreeting>
    <EmptyStateSuggestions>{suggestions.map((text, index) => <EmptyStateSuggestion key={text} index={index} onClick={() => onPick(text)}>{text}</EmptyStateSuggestion>)}</EmptyStateSuggestions>
  </AuiEmptyState>
}

export function ThreadConnectionNotice({ connected }: { connected: boolean }) {
  return <ConnectionState phase={connected ? 'online' : 'reconnecting'} role="status"/>
}

export function formatPrivateCopyTime(value: string): string | undefined {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

export function ThreadSharedSnapshots({ shares, selected, detail, busy, error, onCreate, onSelect, onOpen, onCopy, onRevoke }: {
  shares: readonly ChatSessionShare[]
  selected: string | null
  detail: ChatSessionShareDetail | null
  busy: boolean
  error: string | null
  onCreate: () => void
  onSelect: (slug: string) => void
  onOpen: (share: ChatSessionShare) => void
  onCopy: (share: ChatSessionShare) => void
  onRevoke: (share: ChatSessionShare) => void
}) {
  return <div className="flex max-h-[70vh] min-w-[min(90vw,32rem)] flex-col gap-m overflow-y-auto p-l">
    <p className="text-sm text-on-surface-low">Copies are redacted, read-only, and available only to the owner of this workspace.</p>
    <button type="button" disabled={busy} onClick={onCreate} className="self-start rounded-pill bg-primary px-4 py-2 text-sm text-on-primary disabled:opacity-50">Create private copy</button>
    {error && <p role="alert" className="text-sm text-error">{error}</p>}
    {shares.length === 0 ? <p className="text-sm text-on-surface-low">No private copies yet.</p> : <div className="flex flex-col gap-s" role="list" aria-label="Private conversation copies">
      {shares.map((share) => <div key={share.slug} role="listitem" className="rounded-lg border border-outline-variant/40 p-s">
        <button type="button" aria-pressed={selected === share.slug} onClick={() => onSelect(share.slug)} className="w-full truncate text-left text-sm font-medium">{share.name}</button>
        <div className="mt-1 flex flex-wrap gap-s text-xs">
          <button type="button" onClick={() => onOpen(share)}>Open read-only copy</button>
          <button type="button" onClick={() => onCopy(share)}>Copy private link</button>
          <button type="button" disabled={busy} onClick={() => onRevoke(share)} className="text-error disabled:opacity-50">Revoke copy</button>
        </div>
      </div>)}
    </div>}
    {selected && detail?.slug === selected && <SharedConversation title={detail.name} sharedBy={detail.shared_by ?? undefined}
      sharedAt={formatPrivateCopyTime(detail.created_at)} turns={detail.turns} className="max-w-none"/>}
  </div>
}
