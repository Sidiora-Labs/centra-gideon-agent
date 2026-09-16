import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { useChatSocket, type WsMessage } from './useChatSocket'
import { useVisiblePoll } from './useVisiblePoll'
import type { ChatSessionSummary, Loop, PendingApproval, SpawnedAgent } from './api'


export type AgentActivityKind = 'session' | 'loop' | 'subagent'

export type AgentActivityState =
  | 'working'
  | 'needs_input'
  | 'waiting_approval'
  | 'idle'
  | 'error'

export interface AgentActivityRefs {
  link: string
  session?: string
  parent?: string
}

export interface AgentActivityEntity {
  id: string
  kind: AgentActivityKind
  state: AgentActivityState
  title: string
  progress?: number
  refs: AgentActivityRefs
}

export interface AgentActivityFeed {
  entities: AgentActivityEntity[]
  truncated: number
  error: unknown
  loading: boolean
  refresh: () => void
}

export const MAX_ENTITIES = 64

export const IDLE_SESSION_LIMIT = 8

const SALIENCE: Record<AgentActivityState, number> = {
  needs_input: 0, waiting_approval: 1, error: 2, working: 3, idle: 4,
}

const LOOP_STATE: Record<string, AgentActivityState> = {
  intake: 'working', planning: 'working', review: 'working', running: 'working',
  needs_input: 'needs_input', blocked: 'needs_input', stagnant: 'needs_input',
  failed: 'error',
  ready: 'idle', paused: 'idle', stopped: 'idle', complete: 'idle',
}

function label(s: string | undefined, fallback: string): string {
  const t = (s ?? '').replace(/\s+/g, ' ').trim()
  return t ? t.slice(0, 80) : fallback
}

export function approvalSessions(approvals: PendingApproval[]): Set<string> {
  return new Set(approvals.map((a) => a.session).filter(Boolean))
}

export function foldLoops(loops: Loop[], blocked: Set<string>): AgentActivityEntity[] {
  return loops.map((l) => {
    const awaiting = !!l.session_key && blocked.has(l.session_key)
    const ended = l.status === 'complete' && !!l.error_message
    const state: AgentActivityState = awaiting
      ? 'waiting_approval'
      : ended ? 'error' : (LOOP_STATE[l.status] ?? 'idle')
    const total = l.max_cycles > 0 ? Math.min(1, Math.max(0, l.total_cycles / l.max_cycles)) : undefined
    return {
      id: `loop:${l.id}`,
      kind: 'loop' as const,
      state,
      title: label(l.name || l.task, 'Loop'),
      ...(total === undefined ? {} : { progress: total }),
      refs: {
        link: `#/${l.kind === 'code' ? 'code' : 'loops'}/${l.id}`,
        ...(l.session_key ? { session: l.session_key } : {}),
      },
    }
  })
}

export function foldSessions(sessions: ChatSessionSummary[], blocked: Set<string>): AgentActivityEntity[] {
  const live = sessions.filter((s) => s.lifecycle !== 'archived')
  const kept: ChatSessionSummary[] = []
  const idle: ChatSessionSummary[] = []
  for (const s of live) {
    if (s.running || blocked.has(s.key)) kept.push(s)
    else idle.push(s)
  }
  idle.sort((a, b) => (b.last_activity_at ?? 0) - (a.last_activity_at ?? 0))
  return [...kept, ...idle.slice(0, IDLE_SESSION_LIMIT)].map((s) => ({
    id: `session:${s.key}`,
    kind: 'session' as const,
    state: blocked.has(s.key) ? 'waiting_approval' : s.running ? 'working' : 'idle',
    title: label(s.title, 'Chat'),
    refs: { link: `#/chat/${encodeURIComponent(s.key)}`, session: s.key },
  }))
}

export function foldSubagents(agents: SpawnedAgent[]): AgentActivityEntity[] {
  return agents.map((a) => ({
    id: `subagent:${a.id}`,
    kind: 'subagent' as const,
    state: a.error ? 'error' : a.done ? 'idle' : 'working',
    title: label(a.task || a.agent, 'Subagent'),
    refs: { link: '', ...(a.parent ? { parent: a.parent } : {}) },
  }))
}

export interface AgentActivitySources {
  loops: Loop[]
  sessions: ChatSessionSummary[]
  subagents: SpawnedAgent[]
  approvals: PendingApproval[]
}

export function foldAgentActivity(src: AgentActivitySources): { entities: AgentActivityEntity[]; truncated: number } {
  const blocked = approvalSessions(src.approvals)
  const all = [
    ...foldLoops(src.loops, blocked),
    ...foldSessions(src.sessions, blocked),
    ...foldSubagents(src.subagents),
  ]
  all.sort((a, b) =>
    SALIENCE[a.state] - SALIENCE[b.state] || a.title.localeCompare(b.title) || a.id.localeCompare(b.id))
  return { entities: all.slice(0, MAX_ENTITIES), truncated: Math.max(0, all.length - MAX_ENTITIES) }
}

const FAST_POLL = 10_000
const SIGNAL_DEBOUNCE = 600

export function useAgentActivity(): AgentActivityFeed {
  const [sources, setSources] = useState<AgentActivitySources | null>(null)
  const [error, setError] = useState<unknown>(null)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const load = useCallback(() => {
    Promise.all([
      api.uLoops(), api.chatSessions(), api.spawnedAgents(), api.approvals(),
    ]).then(([loops, sessions, subagents, approvals]) => {
      if (!alive.current) return
      setSources({ loops, sessions, subagents, approvals })
      setError(null)
    }).catch((e) => { if (alive.current) setError(e) })
  }, [])

  const debounce = useRef<number | undefined>(undefined)
  const signal = useCallback(() => {
    if (debounce.current) clearTimeout(debounce.current)
    debounce.current = window.setTimeout(load, SIGNAL_DEBOUNCE)
  }, [load])
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current) }, [])

  const onMessage = useCallback((m: WsMessage) => {
    const t = m.type
    if (
      t === 'chat_status' || t === 'sessions' || t === 'update_progress' ||
      t.startsWith('subagent') || t === 'approval' || t === 'approval_resolved'
    ) signal()
  }, [signal])

  useChatSocket(onMessage, load)
  useVisiblePoll(load, FAST_POLL)

  const folded = useMemo(
    () => sources ? foldAgentActivity(sources) : { entities: [], truncated: 0 },
    [sources],
  )

  return {
    entities: folded.entities,
    truncated: folded.truncated,
    error,
    loading: sources === null && error === null,
    refresh: load,
  }
}
