import { useEffect, useRef, useState } from 'react'
import { api, type TaskItem, type TaskComment, type ProjectItem, type TaskListItem } from '../../shared/data/api'
import { reportingWrite } from '../../app/shell/reportingWrite'
import { depMap, prereqIds, wouldCycle } from './dag'
import { isExitComplete } from './taskMeta'

export type TaskDraft = Partial<TaskItem> & { title: string; depends_on?: string[]; project_id?: string }
export function emptyDraft(): TaskDraft {
  return { title: '', description: '', status: 'open', priority: 'medium', labels: [], task_list_id: '', assignee: '', due: '', exit_criteria: [], action_plan: [], notes: [], research_notes: [], execution_notes: [], agent_instructions_template: '', depends_on: [] }
}
export function toDraft(task: TaskItem): TaskDraft {
  const draft = { ...emptyDraft(), ...task, depends_on: prereqIds(task) }
  for (const field of ['labels', 'exit_criteria', 'action_plan', 'notes', 'research_notes', 'execution_notes'] as const) {
    Object.assign(draft, { [field]: task[field] ?? [] })
  }
  return draft
}
export function draftToPayload(draft: TaskDraft): Record<string, unknown> {
  const defaults = emptyDraft()
  const payload: Record<string, unknown> = Object.fromEntries(Object.keys(defaults).filter(key => key !== 'depends_on').map(key => [key, draft[key as keyof TaskDraft] ?? defaults[key as keyof TaskDraft]]))
  payload.title = draft.title.trim()
  payload.dependencies = (draft.depends_on ?? []).map(depends_on_task_id => ({ depends_on_task_id, dependency_type: 'BLOCKS' }))
  if (!draft.task_list_id && draft.project_id) payload.project_id = draft.project_id
  return payload
}
export function chooseTaskProject(projects: ProjectItem[], lists: TaskListItem[], taskListId: string, activeProject: string | null | undefined): string {
  const existing = lists.find(list => list.id === taskListId)?.project_id
  return existing || projects.find(project => project.id === activeProject)?.id || projects.find(project => project.is_builtin)?.id || projects[0]?.id || ''
}
export function dependencyCandidates(tasks: TaskItem[], selfId: string | undefined, selected: string[], query: string) {
  const graph = depMap(tasks)
  if (selfId) graph.set(selfId, selected)
  const excluded = new Set([selfId, ...selected])
  const needle = query.trim().toLocaleLowerCase()
  const candidates: { task: TaskItem; cyclic: boolean }[] = []
  for (const task of tasks) {
    if (excluded.has(task.id) || !task.title.toLocaleLowerCase().includes(needle)) continue
    candidates.push({ task, cyclic: !!selfId && wouldCycle(graph, selfId, task.id) })
    if (candidates.length === 40) break
  }
  return candidates
}
export function taskChecklistPatch(task: TaskItem, kind: 'exit' | 'step', index: number): Record<string, unknown> {
  if (kind === 'step') return { action_plan: (task.action_plan ?? []).map((entry, position) => position === index ? { ...entry, completed: !entry.completed } : entry) }
  return { exit_criteria: (task.exit_criteria ?? []).map((entry, position) => {
    if (position !== index) return entry
    const met = !isExitComplete(entry)
    return { ...entry, met, status: met ? 'complete' : 'incomplete' }
  }) }
}
export class TaskRequestLease {
  private generation = 0
  private pending: object | null = null
  reset() { this.generation++; this.pending = null }
  begin() { if (this.pending) return null; const ticket = { generation: this.generation }; this.pending = ticket; return ticket }
  owns(ticket: object) { return this.pending === ticket }
  finish(ticket: object) { if (!this.owns(ticket)) return false; this.pending = null; return true }
}
export function useTaskOperation(identity: string) {
  const [lease] = useState(() => new TaskRequestLease())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { lease.reset(); setBusy(false); setError(''); return () => lease.reset() }, [identity, lease])
  async function run<Result>(operation: () => Promise<Result>, success: (result: Result) => void, fallback: string) {
    const ticket = lease.begin()
    if (!ticket) return
    setBusy(true); setError('')
    try { const result = await operation(); if (lease.owns(ticket)) success(result) }
    catch (failure) { if (lease.owns(ticket)) setError(failure instanceof Error ? failure.message : fallback) }
    finally { if (lease.finish(ticket)) setBusy(false) }
  }
  return { busy, error, setError, run }
}
export function useTaskCommentThread(taskId: string, provider?: string) {
  const [comments, setComments] = useState<TaskComment[] | null>(null)
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState(false)
  const generation = useRef(0)
  const pending = useRef(false)
  const reload = async (revision: number) => {
    const entries = await api.taskComments(taskId, provider).catch(() => [])
    if (revision === generation.current) setComments(entries)
  }
  useEffect(() => {
    const revision = ++generation.current
    pending.current = false; setSending(false); setComments(null); setDraft('')
    void reload(revision)
    return () => { generation.current++ }
  }, [taskId, provider])
  const send = async () => {
    const body = draft.trim()
    if (!body || pending.current) return
    const revision = generation.current
    const sentDraft = draft
    pending.current = true; setSending(true)
    try {
      if (!await reportingWrite('post this comment', () => api.addTaskComment(taskId, body, provider))) return
      if (revision !== generation.current) return
      setDraft(current => current === sentDraft ? '' : current)
      await reload(revision)
    } finally { if (revision === generation.current) { pending.current = false; setSending(false) } }
  }
  const remove = async (commentId: string) => {
    const revision = generation.current
    if (await reportingWrite('delete this comment', () => api.deleteTaskComment(taskId, commentId, provider))) await reload(revision)
  }
  return { comments, draft, setDraft, sending, send, remove }
}
