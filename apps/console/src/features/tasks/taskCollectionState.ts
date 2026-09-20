import { useEffect, useRef, useState } from 'react'
import { api, type TaskItem, type ProjectItem, type TaskListItem } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { notify } from '../../app/shell/appSdk'
import { reportActionFailure } from '../../app/shell/reportingWrite'
import { parseDueDate, TERMINAL } from './taskMeta'

export const SCOPE_GOALS = '__goals__'
export const SCOPE_CODING = '__coding__'
export const GOAL_LOOPS_PROJECT = 'Goal Loops'
export const ASSIGNED_EVERYONE = ''
export const ASSIGNED_MINE = 'mine'
export type TaskView = 'list' | 'cards' | 'board' | 'dag'
export const FULL_WIDTH: TaskView[] = ['board', 'dag']

function stored(key: string, fallback: string) {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function remember(key: string, value: string) { try { localStorage.setItem(key, value) } catch {} }
export function useTaskPreference(raw: string, key: string, fallback: string, change: (value: string) => void, emptyIsExplicit = false) {
  const [saved, setSaved] = useState(() => emptyIsExplicit ? raw : stored(key, fallback))
  useEffect(() => { if (raw || emptyIsExplicit) { remember(key, raw); setSaved(raw) } }, [raw, key, emptyIsExplicit])
  return [emptyIsExplicit ? raw : raw || saved, (value: string) => { remember(key, value); setSaved(value); change(value) }] as const
}
export function belongsToOwner(task: TaskItem, owner: string) {
  if (!owner) return true
  const identity = task.assignee?.trim() || task.author?.trim()
  return !identity || identity.toLowerCase() === owner.toLowerCase()
}
export function scopeContains(task: TaskItem, scope: string, coding: Set<string>) {
  switch (scope) {
    case '': return true
    case SCOPE_GOALS: return task.project === GOAL_LOOPS_PROJECT
    case SCOPE_CODING: return !!task.project && coding.has(task.project)
    default: return task.project === scope
  }
}
export function mergeTaskResponse(tasks: TaskItem[] | null, response: TaskItem) {
  if (!tasks) return null
  const patches = new Map((response.reconciled ?? []).map(task => [task.id, task]))
  if (!patches.has(response.id)) patches.set(response.id, response)
  return tasks.map(task => patches.get(task.id) ?? task)
}
export function filterTaskCollection(input: { tasks: TaskItem[] | null; ready: TaskItem[] | null; results: TaskItem[] | null; query: string; status: string; scope: string; coding: Set<string>; list: string; owner: string; assigned: string; sort: string }) {
  const { tasks, ready, results, query, status, scope, coding, list, owner, assigned, sort } = input
  const source = query ? results : status === 'ready' ? ready : tasks
  if (!source) return null
  const ranks: Record<string, number> = { critical: 5, high: 4, medium: 3, low: 2, trivial: 1 }
  const due = (task: TaskItem) => { const value = task.due ? parseDueDate(task.due) : NaN; return Number.isNaN(value) ? Infinity : value }
  const updated = (task: TaskItem) => Date.parse(task.updated_at || task.created_at || '') || 0
  return source.filter(task => {
    if (!query && status !== 'all' && status !== 'ready' && !(status === 'done' ? TERMINAL.has(task.status) : task.status === status)) return false
    return scopeContains(task, scope, coding) && (!list || task.task_list_id === list) && (assigned !== ASSIGNED_MINE || belongsToOwner(task, owner))
  }).sort((left, right) => {
    const terminal = Number(TERMINAL.has(left.status)) - Number(TERMINAL.has(right.status))
    if (terminal) return terminal
    if (sort === 'due') return due(left) - due(right)
    if (sort === 'priority') return (ranks[right.priority ?? ''] ?? 3) - (ranks[left.priority ?? ''] ?? 3)
    return updated(right) - updated(left)
  })
}

export function useTaskCollection(query: string, filter: string) {
  const collection = useQuery('tasks', () => api.allTasks().then(result => result.tasks), { persist: false })
  const [tasks, setTasks] = useState<TaskItem[] | null>(null)
  const [owner, setOwner] = useState('')
  const [catalog, setCatalog] = useState<{ projects: ProjectItem[]; lists: TaskListItem[]; coding: Set<string> }>({ projects: [], lists: [], coding: new Set() })
  const [ready, setReady] = useState<TaskItem[] | null>(null)
  const [results, setResults] = useState<TaskItem[] | null>(null)
  const [readyError, setReadyError] = useState<unknown>(null)
  const [searchError, setSearchError] = useState<unknown>(null)
  const [retry, setRetry] = useState({ ready: 0, search: 0 })
  const [moveError, setMoveError] = useState('')
  const [selected, setSelected] = useState(new Set<string>())
  const [bulkBusy, setBulkBusy] = useState(false)
  const pending = useRef(new Map<string, object>())
  const optimistic = useRef(new Map<string, string>())
  const rows = useRef(tasks)
  rows.current = tasks
  const alive = useRef(true)
  const bulkLock = useRef(false)
  const errorTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    alive.current = true
    return () => { alive.current = false; pending.current.clear(); if (errorTimer.current) clearTimeout(errorTimer.current) }
  }, [])
  useEffect(() => {
    if (collection.data !== undefined) setTasks(collection.data.map(task => optimistic.current.has(task.id) ? { ...task, status: optimistic.current.get(task.id)! } : task))
  }, [collection.data])
  useEffect(() => { const timer = setInterval(collection.refresh, 12000); return () => clearInterval(timer) }, [collection.refresh])
  useEffect(() => {
    let current = true
    api.tasks({ limit: 1 }).then(result => { if (current) setOwner(result.owner ?? '') }).catch(() => {})
    Promise.all([api.projects(), api.taskLists(), api.uLoops({ kind: 'code' }).catch(() => [])]).then(([projects, lists, loops]) => {
      if (!current) return
      const names = new Map(projects.map(project => [project.id, project.name]))
      const coding = new Set<string>()
      for (const loop of loops) { const name = loop.tasks_project_id && names.get(loop.tasks_project_id); if (name) coding.add(name) }
      setCatalog({ projects, lists, coding })
    }).catch(() => { if (current) setCatalog({ projects: [], lists: [], coding: new Set() }) })
    return () => { current = false }
  }, [])
  useEffect(() => {
    if (filter !== 'ready') return
    let current = true
    setReadyError(null)
    api.readyTasks().then(data => { if (current) setReady(data) }).catch(error => { if (current) { setReady(null); setReadyError(error) } })
    return () => { current = false }
  }, [filter, tasks, retry.ready])
  useEffect(() => {
    if (!query) { setResults(null); setSearchError(null); return }
    let current = true
    setSearchError(null)
    const timer = setTimeout(() => {
      api.searchTasks({ query, limit: 100 }).then(data => { if (current) setResults(data.tasks) }).catch(error => { if (current) { setResults(null); setSearchError(error) } })
    }, 250)
    return () => { current = false; clearTimeout(timer) }
  }, [query, tasks, retry.search])
  const load = () => { invalidateKeys('tasks', true); collection.refresh() }
  const showError = (message: string) => {
    if (errorTimer.current) clearTimeout(errorTimer.current)
    setMoveError(message)
    if (message) errorTimer.current = setTimeout(() => { if (alive.current) setMoveError('') }, 6000)
  }
  const patchLocal = (updated: TaskItem) => { setTasks(current => mergeTaskResponse(current, updated)); invalidateKeys('tasks-all') }
  const moveTask = async (id: string, status: string) => {
    const before = rows.current?.find(task => task.id === id)
    if (!before || before.status === status || before.provider === 'project' || pending.current.has(id)) return
    const ticket = {}
    pending.current.set(id, ticket); optimistic.current.set(id, status); showError('')
    setTasks(current => current?.map(task => task.id === id ? { ...task, status } : task) ?? null)
    try {
      const updated = await api.updateTask(id, { status })
      if (alive.current && pending.current.get(id) === ticket) patchLocal(updated)
    } catch (error) {
      if (alive.current && pending.current.get(id) === ticket) {
        setTasks(current => current?.map(task => task.id === id ? { ...task, status: before.status } : task) ?? null)
        showError(`“${before.title}” → ${status.replace('_', ' ')}: ${error instanceof Error ? error.message : 'Could not update the task.'}`)
      }
    } finally { if (pending.current.get(id) === ticket) { pending.current.delete(id); optimistic.current.delete(id) } }
  }
  const clearSelection = () => setSelected(new Set())
  const toggleSelect = (id: string) => setSelected(current => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next })
  const runBulk = async (operation: 'update' | 'delete', patch?: Record<string, unknown>) => {
    if (!selected.size || bulkLock.current) return
    bulkLock.current = true; setBulkBusy(true)
    const items = [...selected].map(id => operation === 'delete' ? { id } : { id, ...patch })
    try {
      const result = await api.tasksBulk(operation, items)
      if (result.failed > 0) notify(`${result.failed} of ${result.total} ${operation === 'delete' ? 'deletions' : 'updates'} refused${Array.isArray(result.errors) && result.errors.length ? `: ${String(result.errors[0])}` : ''}`, 'error')
    } catch (error) { reportActionFailure(`${operation} ${items.length} task${items.length === 1 ? '' : 's'}`)(error) }
    finally { bulkLock.current = false; if (alive.current) { setBulkBusy(false); clearSelection(); load() } }
  }
  return { tasks, owner, ...catalog, ready, results, readyError, searchError, loadError: collection.error, load, patchLocal, moveTask, selected, toggleSelect, clearSelection, bulkBusy, runBulk, moveError, showError,
    retryReady: () => setRetry(value => ({ ...value, ready: value.ready + 1 })), retrySearch: () => setRetry(value => ({ ...value, search: value.search + 1 })) }
}
