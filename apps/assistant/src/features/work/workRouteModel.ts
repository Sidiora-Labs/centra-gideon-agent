import { createShellRoute, type ShellDestination, type ShellReturnContext, type ShellRoute } from '../../shared/shell/shellRoutes'
import type { WorkKind } from './workClient'

export type WorkDestination = Readonly<{
  id: string
  label: string
  kind: WorkKind
  destination: ShellDestination
  subview?: string
  intent?: 'edit' | 'invoke' | 'availability' | 'unsupported_execution' | 'unsupported_edit'
}>

export const WORK_DESTINATIONS: readonly WorkDestination[] = Object.freeze([
  { id: 'tasks', label: 'Tasks', kind: 'task', destination: 'activity' },
  { id: 'tasks/new', label: 'Create a task', kind: 'task', destination: 'activity' },
  { id: 'tasks/graph', label: 'Dependencies', kind: 'task', destination: 'activity' },
  { id: 'projects', label: 'Projects', kind: 'project', destination: 'apps' },
  { id: 'projects/detail', label: 'Project workspace', kind: 'project', destination: 'apps' },
  { id: 'workflows', label: 'Workflows', kind: 'workflow', destination: 'activity' },
  { id: 'workflows/definition', label: 'Workflow builder', kind: 'workflow', destination: 'activity' },
  { id: 'workflows/run', label: 'Workflow run', kind: 'workflow_run', destination: 'activity' },
  { id: 'triggers', label: 'Schedules and triggers', kind: 'trigger', destination: 'activity' },
  { id: 'triggers/new', label: 'Create an automation', kind: 'trigger', destination: 'activity' },
  { id: 'loops', label: 'Ongoing work', kind: 'loop', destination: 'activity' },
  { id: 'loops/new', label: 'Start ongoing work', kind: 'loop', destination: 'activity' },
  { id: 'loops/run', label: 'Loop controls', kind: 'loop', destination: 'activity' },
  { id: 'rooms', label: 'Rooms', kind: 'room', destination: 'activity' },
  { id: 'rooms/new', label: 'Create a room', kind: 'room', destination: 'activity' },
  { id: 'rooms/conversation', label: 'Room conversation', kind: 'room', destination: 'activity' },
  { id: 'agents', label: 'Agents', kind: 'agent', destination: 'activity' },
  { id: 'agents/new', label: 'Create an agent', kind: 'agent', destination: 'activity' },
  { id: 'agents/inspect', label: 'Agent controls', kind: 'agent', destination: 'activity' },
  { id: 'experiments', label: 'Experiments', kind: 'experiment', destination: 'activity' },
  { id: 'experiments/replay', label: 'Run replay', kind: 'experiment', destination: 'activity' },
  { id: 'skills', label: 'Skills', kind: 'skill', destination: 'activity' },
  { id: 'skills', subview: '/detail', label: 'Skill details', kind: 'skill', destination: 'activity' },
  { id: 'skills', subview: '/edit', label: 'Edit skill', kind: 'skill', destination: 'activity', intent: 'edit' },
  { id: 'skills', subview: '/execution', label: 'Skill execution', kind: 'skill', destination: 'activity', intent: 'unsupported_execution' },
  { id: 'skills', subview: '/availability', label: 'Skill availability', kind: 'skill', destination: 'activity', intent: 'availability' },
  { id: 'tools', label: 'Tools', kind: 'tool', destination: 'activity' },
  { id: 'tools', subview: '/detail', label: 'Tool details', kind: 'tool', destination: 'activity' },
  { id: 'tools', subview: '/edit', label: 'Edit tool', kind: 'tool', destination: 'activity', intent: 'unsupported_edit' },
  { id: 'tools', subview: '/execution', label: 'Tool execution', kind: 'tool', destination: 'activity', intent: 'invoke' },
  { id: 'tools', subview: '/invoke', label: 'Run tool', kind: 'tool', destination: 'activity', intent: 'invoke' },
  { id: 'tools', subview: '/availability', label: 'Tool availability', kind: 'tool', destination: 'activity', intent: 'availability' },
])

export function findWorkDestination(route: ShellRoute): WorkDestination | null {
  const placement = route.placement
  if (placement) {
    const page = WORK_DESTINATIONS.find(entry => entry.id === placement.id
      && entry.subview === placement.subview && entry.destination === route.destination)
    if (!page || (route.record && route.record.kind !== page.kind)) return null
    return page
  }
  if (!route.record || route.destination !== 'activity') return null
  const base = WORK_DESTINATIONS.find(entry => entry.kind === route.record?.kind && !entry.subview
    && entry.destination === route.destination && (route.record?.kind === 'workflow_run'
      ? entry.id === 'workflows/run' : !entry.id.includes('/')))
  return base ?? null
}

export function createWorkRoute(id: string, recordId?: string,
  returnTo?: ShellReturnContext, subview?: string): ShellRoute {
  const page = WORK_DESTINATIONS.find(entry => entry.id === id && entry.subview === subview)
  if (!page) throw new TypeError('Unknown Work destination')
  if (subview && !recordId) throw new TypeError('This Work destination needs a native ID')
  return createShellRoute(page.destination, {
    view: recordId ? 'detail' : 'workspace',
    placement: { id, ...(subview ? { subview } : {}) },
    ...(recordId ? { record: { kind: page.kind, id: recordId } } : {}),
    ...(returnTo ? { returnTo } : {}),
  })
}

export function workReturnRoute(context: ShellReturnContext): ShellRoute {
  return createShellRoute(context.destination, {
    view: context.record ? 'detail' : context.placement ? 'workspace' : 'list',
    record: context.record,
    placement: context.placement,
    sessionId: context.sessionId,
  })
}
