import { useEffect, useRef, useState } from 'react'
import { api, type EpisodicEntry, type KnowledgeItem, type SemanticEntry, type SessionSearchAnswer, type TaskItem } from '../shared/data/api'
import { failureSentence } from './shell/reportingWrite'

export type ContentSource = 'chats' | 'memory' | 'knowledge' | 'tasks'
export const CONTENT_SOURCES: readonly ContentSource[] = ['chats', 'memory', 'knowledge', 'tasks']
export const MIN_CONTENT_QUERY = 2
export const MAX_CONTENT_QUERY = 200
export const HITS_PER_SOURCE = 5

export interface ContentHit {
  id: string
  source: ContentSource
  label: string
  detail?: string
  path: string
}

export interface SourceState {
  status: 'pending' | 'available' | 'partial' | 'unavailable'
  message?: string
}

export interface ContentSearch {
  query: string
  searching: boolean
  hits: ContentHit[]
  sources: Record<ContentSource, SourceState>
}

export interface ContentSearchData {
  chats?: SessionSearchAnswer
  episodes?: EpisodicEntry[]
  facts?: SemanticEntry[]
  knowledge?: { items: KnowledgeItem[]; total: number; limit: number }
  tasks?: { tasks: TaskItem[]; total: number }
  failures?: Partial<Record<ContentSource, string>>
  memoryFailure?: string
}

const idle = (query = ''): ContentSearch => ({
  query,
  searching: false,
  hits: [],
  sources: {
    chats: { status: 'pending' },
    memory: { status: 'pending' },
    knowledge: { status: 'pending' },
    tasks: { status: 'pending' },
  },
})

const oneLine = (value: string, cap = 120) => {
  const text = value.replace(/\s+/g, ' ').trim()
  return text.length > cap ? `${text.slice(0, cap - 1)}…` : text
}

function factValue(value?: string): string {
  if (!value) return ''
  try {
    const parsed: unknown = JSON.parse(value)
    return typeof parsed === 'string' ? parsed : JSON.stringify(parsed)
  } catch {
    return value
  }
}

function chatKey(key: string) {
  return key.replace(/^dashboard[_:]/, '')
}

export function chatFindPath(key: string, query: string) {
  return `chat/${encodeURIComponent(chatKey(key))}?find=${encodeURIComponent(query.slice(0, MAX_CONTENT_QUERY))}`
}

export function memoryRecordPath(uid: string) {
  return `settings/memory?tab=studio&sel=${encodeURIComponent(uid)}`
}

export function projectContentSearch(query: string, data: ContentSearchData): ContentSearch {
  const hits: ContentHit[] = []
  const sources = idle(query).sources
  const failure = data.failures ?? {}

  if (data.chats) {
    const unique = new Set<string>()
    const chatHits: ContentHit[] = []
    for (const session of data.chats.sessions) {
      const key = chatKey(session.key)
      if (unique.has(key)) continue
      unique.add(key)
      chatHits.push({
        id: `chat:${key}`,
        source: 'chats',
        label: session.title || 'Untitled chat',
        detail: session.snippet ? oneLine(session.snippet.replace(/<<|>>/g, '')) : undefined,
        path: chatFindPath(key, query),
      })
    }
    hits.push(...chatHits.slice(0, HITS_PER_SOURCE))
    const searched = data.chats.searched
    const resultLimit = chatHits.length > HITS_PER_SOURCE
    const coverage = data.chats.complete
      ? `Searched all ${searched.of.toLocaleString()} chats.`
      : `Searched ${searched.chats.toLocaleString()} of ${searched.of.toLocaleString()} chats; results may be incomplete.${data.chats.index?.building ? ' The search index is still being built.' : ''}`
    sources.chats = !data.chats.complete || resultLimit
      ? { status: 'partial', message: `${coverage}${resultLimit ? ` Showing the first ${HITS_PER_SOURCE} matches.` : ''}` }
      : { status: 'available', message: coverage }
  } else if (failure.chats) {
    sources.chats = { status: 'unavailable', message: failureSentence('search your chats', failure.chats) }
  }

  if (data.episodes || data.facts) {
    const memoryHits: ContentHit[] = []
    for (const fact of data.facts ?? []) {
      const value = factValue(fact.value_json)
      if (fact.key.toLowerCase().includes(query.toLowerCase()) || value.toLowerCase().includes(query.toLowerCase())) {
        memoryHits.push({
          id: `fact:${fact.key}`,
          source: 'memory',
          label: fact.key,
          detail: oneLine(value) || undefined,
          path: memoryRecordPath(`fact:${fact.key}`),
        })
      }
    }
    for (const episode of data.episodes ?? []) {
      memoryHits.push({
        id: `episode:${episode.id}`,
        source: 'memory',
        label: oneLine(episode.text, 80),
        detail: episode.created_at ? oneLine(episode.created_at, 48) : undefined,
        path: memoryRecordPath(`epi:${episode.id}`),
      })
    }
    hits.push(...memoryHits.slice(0, HITS_PER_SOURCE))
    sources.memory = data.memoryFailure
      ? { status: 'partial', message: failureSentence('search all of your memory', data.memoryFailure) }
      : { status: 'partial', message: 'Episode search is limited to 20; showing at most five combined memory matches.' }
  } else if (failure.memory) {
    sources.memory = { status: 'unavailable', message: failureSentence('search your memory', failure.memory) }
  }

  if (data.knowledge) {
    const { items, total, limit } = data.knowledge
    hits.push(...items.slice(0, HITS_PER_SOURCE).map((item) => ({
      id: `knowledge:${item.id}`,
      source: 'knowledge' as const,
      label: item.title || 'Untitled item',
      detail: item.summary ? oneLine(item.summary) : undefined,
      path: `knowledge/item/${encodeURIComponent(item.id)}`,
    })))
    sources.knowledge = total > Math.min(items.length, HITS_PER_SOURCE)
      ? { status: 'partial', message: `Showing ${Math.min(items.length, HITS_PER_SOURCE)} of ${total.toLocaleString()} knowledge matches (page limit ${limit}).` }
      : { status: 'available', message: `Searched all ${total.toLocaleString()} matching knowledge items.` }
  } else if (failure.knowledge) {
    sources.knowledge = { status: 'unavailable', message: failureSentence('search your knowledge', failure.knowledge) }
  }

  if (data.tasks) {
    const { tasks, total } = data.tasks
    hits.push(...tasks.slice(0, HITS_PER_SOURCE).map((task) => ({
      id: `task:${task.id}`,
      source: 'tasks' as const,
      label: task.title,
      detail: task.status || undefined,
      path: `tasks?open=${encodeURIComponent(task.id)}`,
    })))
    sources.tasks = total > Math.min(tasks.length, HITS_PER_SOURCE)
      ? { status: 'partial', message: `Showing ${Math.min(tasks.length, HITS_PER_SOURCE)} of ${total.toLocaleString()} task matches.` }
      : { status: 'available', message: `Searched all ${total.toLocaleString()} matching tasks.` }
  } else if (failure.tasks) {
    sources.tasks = { status: 'unavailable', message: failureSentence('search your tasks', failure.tasks) }
  }

  return { query, searching: false, hits, sources }
}

function errorReason(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

export function useContentSearch(queryText: string, open: boolean): ContentSearch {
  const [state, setState] = useState<ContentSearch>(() => idle())
  const facts = useRef<Promise<SemanticEntry[]> | null>(null)
  const latest = useRef(0)

  useEffect(() => { if (!open) facts.current = null }, [open])

  useEffect(() => {
    const query = queryText.trim().slice(0, MAX_CONTENT_QUERY)
    const run = ++latest.current
    if (!open || query.length < MIN_CONTENT_QUERY) {
      setState(idle())
      return
    }
    setState({ ...idle(query), searching: true })
    const timer = window.setTimeout(() => {
      const readFacts = () => (facts.current ??= api.memorySemantic())
      const chat = api.sessionsSearch(query)
      const episodes = api.searchEpisodic(query)
      const semantic = readFacts()
      const knowledge = api.knowledgeItems({ q: query, limit: HITS_PER_SOURCE })
      const tasks = api.searchTasks({ query, limit: HITS_PER_SOURCE })
      void Promise.allSettled([chat, episodes, semantic, knowledge, tasks]).then(([c, e, f, k, t]) => {
        if (run !== latest.current) return
        const data: ContentSearchData = {}
        const failures: Partial<Record<ContentSource, string>> = {}
        if (c.status === 'fulfilled') data.chats = c.value
        else failures.chats = errorReason(c.reason)
        if (e.status === 'fulfilled') data.episodes = e.value
        if (f.status === 'fulfilled') data.facts = f.value
        if (e.status === 'rejected' || f.status === 'rejected') {
          data.memoryFailure = [e.status === 'rejected' ? errorReason(e.reason) : '', f.status === 'rejected' ? errorReason(f.reason) : ''].filter(Boolean).join('; ')
          if (e.status === 'rejected' && f.status === 'rejected') failures.memory = data.memoryFailure
          facts.current = f.status === 'fulfilled' ? facts.current : null
        }
        if (k.status === 'fulfilled') data.knowledge = { ...k.value, limit: k.value.limit }
        else failures.knowledge = errorReason(k.reason)
        if (t.status === 'fulfilled') data.tasks = t.value
        else failures.tasks = errorReason(t.reason)
        data.failures = failures
        setState(projectContentSearch(query, data))
      })
    }, 250)
    return () => window.clearTimeout(timer)
  }, [queryText, open])

  return state
}
