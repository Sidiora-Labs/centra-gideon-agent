import { IconButton } from "../../shared/ui/IconButton"
import { useEffect, useMemo, useState, type ComponentProps, type ReactNode, type Ref } from 'react'
import { Button } from '../../shared/ui/Button'
import { X } from 'lucide-react'
import { ConversationSearch, type SearchHit } from '../../shared/vendor/assistant-ui/elements/conversation-search'
import { ThreadSearch } from '../../shared/vendor/assistant-ui/elements/thread-search'
import { ChatPanel, ChatPanelAssistantMessage, ChatPanelMessages, ChatPanelTyping, ChatPanelUserMessage } from '../../shared/vendor/assistant-ui/elements/chat-panel'
import { EmptyState as AuiEmptyState, EmptyStateGreeting, EmptyStateSuggestion, EmptyStateSuggestions } from '../../shared/vendor/assistant-ui/elements/empty-state'
import { ConnectionState } from '../../shared/vendor/assistant-ui/elements/connection-state'
import { SharedConversation } from '../../shared/vendor/assistant-ui/elements/shared-conversation'
import { ConversationHistoryView } from './auiConversationViews'
import { findInText } from '../../shared/ui/findText'
import { findSegments } from './findSegments'
import { turnText, type ChatTurn } from './chatTypes'
import { sessionTitle } from '../../shared/data/sessionTitle'
import type { ChatSessionSummary, ChatSessionShare, ChatSessionShareDetail } from '../../shared/data/api'

export const MAX_CONVERSATION_SEARCH_QUERY = 200

export function ThreadConversationSearch({ turns, nodeOf, initialQuery = '', onClose }: {
  turns: readonly ChatTurn[]
  nodeOf: (index: number) => HTMLElement | null | undefined
  initialQuery?: string
  onClose: () => void
}) {
  const boundedInitialQuery = initialQuery.slice(0, MAX_CONVERSATION_SEARCH_QUERY)
  const [query, setQuery] = useState(boundedInitialQuery)
  const [active, setActive] = useState(0)
  useEffect(() => { setQuery(boundedInitialQuery); setActive(0) }, [boundedInitialQuery])
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
    <ConversationSearch query={query} hits={hits} activeIndex={current} onQueryChange={(value) => { setQuery(value.slice(0, MAX_CONVERSATION_SEARCH_QUERY)); setActive(0) }} onStep={step}/>
    <IconButton  label="Close find" onClick={onClose} className="grid size-8 shrink-0 place-items-center rounded-full bg-surface-high" icon={X} size={32} iconSize={15} />
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

export function ThreadPeekViews({ turns, streamingText, busy = false, renderAssistant, endRef, labels }: {
  turns: readonly ChatTurn[]
  streamingText?: string | null
  busy?: boolean
  renderAssistant?: (text: string) => ReactNode
  endRef?: Ref<HTMLDivElement>
  labels?: { preview?: string; timeline?: string; group?: string; conversation?: ComponentProps<typeof ConversationHistoryView>['labels'] }
}) {
  const [view, setView] = useState<'preview' | 'timeline'>('preview')
  const live = busy || !!streamingText
  const timeline = view === 'timeline' && !live && turns.length > 0
  return <>
    {turns.length > 0 && <div role="group" aria-label={labels?.group ?? 'Conversation view'} className="flex gap-1 px-1 pb-2">
      <Button variant="ghost" size="xs" shape="squircle" type="button" ariaPressed={!timeline} onClick={() => setView('preview')}
        className="min-h-9 rounded-pill px-3 text-on-surface-var aria-pressed:bg-surface-high aria-pressed:text-on-surface">{labels?.preview ?? 'Preview'}</Button>
      <Button size="sm" variant="ghost" ariaPressed={timeline} disabled={live}
        disabledReason="Wait for the current response to finish before opening the timeline."
        onClick={() => { if (!live && turns.length > 0) setView('timeline') }}
        className="min-h-9 text-on-surface-var aria-pressed:bg-surface-high aria-pressed:text-on-surface">{labels?.timeline ?? 'Timeline'}</Button>
    </div>}
    {timeline ? <ConversationHistoryView turns={turns} labels={labels?.conversation}/>
      : <ThreadChatPreview turns={turns} streamingText={streamingText} busy={busy} renderAssistant={renderAssistant} endRef={endRef}/>}
  </>
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
    <p data-type="body-s" className="text-on-surface-low">Copies are redacted, read-only, and available only to the owner of this workspace.</p>
    <Button variant="ghost" size="xs" shape="squircle" type="button" disabled={busy} loading={busy} loadingLabel="Creating private copy…" onClick={onCreate} className="self-start rounded-pill bg-primary px-4 py-2 text-on-primary disabled:opacity-50">Create private copy</Button>
    {error && <p data-type="body-s" role="alert" className="text-error">{error}</p>}
    {shares.length === 0 ? <p data-type="body-s" className="text-on-surface-low">No private copies yet.</p> : <div className="flex flex-col gap-s" role="list" aria-label="Private conversation copies">
      {shares.map((share) => <div key={share.slug} role="listitem" className="rounded-lg border border-outline-variant/40 p-s">
        <Button variant="ghost" size="xs" shape="squircle" type="button" ariaPressed={selected === share.slug} onClick={() => onSelect(share.slug)} className="w-full truncate text-left font-medium">{share.name}</Button>
        <div data-type="body-s" className="mt-1 flex flex-wrap gap-s ">
          <Button variant="ghost" size="xs" shape="squircle" type="button" onClick={() => onOpen(share)}>Open read-only copy</Button>
          <Button variant="ghost" size="xs" shape="squircle" type="button" onClick={() => onCopy(share)}>Copy private link</Button>
          <Button variant="ghost" size="xs" shape="squircle" type="button" disabled={busy} loading={busy} loadingLabel="Revoking private copy…" onClick={() => onRevoke(share)} className="text-error disabled:opacity-50">Revoke copy</Button>
        </div>
      </div>)}
    </div>}
    {selected && detail?.slug === selected && <SharedConversation title={detail.name} sharedBy={detail.shared_by ?? undefined}
      sharedAt={formatPrivateCopyTime(detail.created_at)} turns={detail.turns} className="max-w-none"/>}
  </div>
}
