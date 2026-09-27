import type { OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type { RouteAvailability } from '../../shared/shell/routeState.web'
import { createShellRoute, type ShellReturnContext, type ShellRoute } from '../../shared/shell/shellRoutes'

export type CodeRecord = { kind: 'project' | 'snapshot' | 'code-project'; id: string }
export type WorkspaceProject = { project: { id: string; name: string; workspace_dir: string }; detection: { types: string[] } }
export type SavedContext = { id: string; project_id: string; workspace: string; branch: string | null; dirty: boolean; terminal_ids: string[]; task_ids: string[]; captured_at: string; revision: number }
export type LiveComparison = { snapshot: SavedContext; live: { branch: string | null; dirty: boolean }; branch_matches: boolean | null; surviving_terminal_ids: string[]; missing_terminal_ids: string[]; surviving_task_ids: string[]; missing_task_ids: string[] }
export type CodeRun = { id: string; kind: string; name: string; project_id?: string; workspace_dir?: string; status: string }
export type CodeSelection = { record: CodeRecord; project?: WorkspaceProject; context?: SavedContext; run?: CodeRun }
export type LiveTerminal = { session_id: string; cwd: string; alive: boolean; shell?: string }
export type LiveTask = { id: string; title: string; status: string }
type OwnerProject = { id: string; name: string; workspace_dir?: string }

const projectBase = '/api/capabilities/workspace/projects'
const contextBase = '/api/capabilities/workspace'

export function codeRecord(route: ShellRoute): CodeRecord | null {
  if (route.destination !== 'apps' || !route.placement) return null
  const kind = route.record?.kind
  const id = route.record?.id
  if (!kind || !id) return null
  if (route.placement.id === 'projects/detail' && kind === 'project') return { kind, id }
  if (route.placement.id === 'capabilities/workspace/context' && kind === 'snapshot') return { kind, id }
  if (route.placement.id === 'code/workspace' && kind === 'code-project') return { kind, id }
  return null
}

export function codeRoute(record: CodeRecord, returnTo?: ShellReturnContext): ShellRoute {
  const placement = record.kind === 'project' ? 'projects/detail'
    : record.kind === 'snapshot' ? 'capabilities/workspace/context' : 'code/workspace'
  return createShellRoute('apps', { view: 'workspace', record, placement: { id: placement }, returnTo })
}

export function codeIndexRoute(returnTo?: ShellReturnContext): ShellRoute {
  return createShellRoute('apps', { view: 'workspace', placement: { id: 'projects' }, returnTo })
}

export function codeReturnRoute(route: ShellRoute): ShellRoute {
  const context = route.returnTo
  return context ? createShellRoute(context.destination, {
    view: context.record ? 'detail' : 'list', record: context.record,
    placement: context.placement, sessionId: context.sessionId,
  }) : createShellRoute('chat')
}

export async function readWorkspaceProjects(): Promise<WorkspaceProject[]> {
  const [registered, owned] = await Promise.allSettled([
    gatewayJson<WorkspaceProject[]>(projectBase), gatewayJson<{ projects: OwnerProject[] }>('/api/projects'),
  ])
  if (registered.status === 'rejected' && owned.status === 'rejected') throw registered.reason
  const rows = registered.status === 'fulfilled' ? registered.value : []
  if (owned.status === 'fulfilled') {
    const known = new Set(rows.map(item => item.project.id))
    for (const project of owned.value.projects) {
      if (project.workspace_dir && !known.has(project.id)) rows.push({
        project: { id: project.id, name: project.name, workspace_dir: project.workspace_dir }, detection: { types: [] },
      })
    }
  }
  return rows
}

async function readWorkspaceProject(id: string): Promise<WorkspaceProject> {
  try {
    const registered = await gatewayJson<WorkspaceProject>(`${projectBase}/${encodeURIComponent(id)}`)
    if (registered.project.id !== id) throw new Error('Project identity changed')
    return registered
  } catch (error) {
    if (!(error instanceof GatewayError) || error.status !== 404) throw error
    const project = await gatewayJson<OwnerProject>(`/api/projects/${encodeURIComponent(id)}`)
    if (project.id !== id) throw new Error('Project identity changed')
    return { project: { id: project.id, name: project.name, workspace_dir: project.workspace_dir ?? '' }, detection: { types: [] } }
  }
}

export async function readSavedContexts(): Promise<SavedContext[]> {
  return gatewayJson<SavedContext[]>(contextBase)
}

export async function readLiveTerminals(workspace: string): Promise<LiveTerminal[]> {
  const inventory = await gatewayJson<{ sessions: LiveTerminal[] }>('/api/terminal/sessions')
  return inventory.sessions.filter(item => item.alive && item.cwd === workspace)
}

export async function readProjectTasks(projectId: string): Promise<LiveTask[]> {
  const lists = await gatewayJson<{ task_lists: { id: string }[] }>(
    `/api/task-lists?project_id=${encodeURIComponent(projectId)}`)
  const tasks: LiveTask[] = []
  for (const list of lists.task_lists) {
    let offset = 0
    let total = 0
    do {
      const page = await gatewayJson<{ tasks: LiveTask[]; total: number }>(
        `/api/tasks?task_list_id=${encodeURIComponent(list.id)}&limit=500&offset=${offset}`)
      tasks.push(...page.tasks)
      offset += page.tasks.length
      total = page.total
      if (!page.tasks.length) break
    } while (offset < total)
  }
  return tasks
}

export async function readCodeSelection(record: CodeRecord): Promise<CodeSelection> {
  if (record.kind === 'project') {
    const project = await readWorkspaceProject(record.id)
    return { record, project }
  }
  if (record.kind === 'snapshot') {
    const context = await gatewayJson<SavedContext>(`${contextBase}/${encodeURIComponent(record.id)}`)
    if (context.id !== record.id) throw new Error('Saved context identity changed')
    const project = await readWorkspaceProject(context.project_id)
      .catch(error => { if (error instanceof GatewayError && error.status === 404) return undefined; throw error })
    if (project && project.project.id !== context.project_id) throw new Error('Saved context project identity changed')
    return { record, context, project }
  }
  const run = await gatewayJson<CodeRun>(`/api/loops/${encodeURIComponent(record.id)}`)
  if (run.id !== record.id || run.kind !== 'code') throw new Error('Code project identity changed')
  const project = run.project_id
    ? await readWorkspaceProject(run.project_id)
      .catch(error => { if (error instanceof GatewayError && error.status === 404) return undefined; throw error })
    : undefined
  if (project && project.project.id !== run.project_id) throw new Error('Code project owner changed')
  return { record, run, project }
}

export async function resolveCodeRoute(scope: OwnerScope, route: ShellRoute): Promise<RouteAvailability> {
  if (route.destination !== 'apps' || !route.placement || !['projects', 'code', 'code/workspace', 'projects/detail', 'capabilities/workspace/context'].includes(route.placement.id)) return 'unavailable'
  if (!scope.ownerId || (typeof window !== 'undefined' && scope.runtimeOrigin !== window.location.origin)) return 'unavailable'
  const record = codeRecord(route)
  if (!record) return route.record ? 'missing' : ['projects', 'code'].includes(route.placement.id) ? 'available' : 'unavailable'
  try { await readCodeSelection(record); return 'available' }
  catch (error) {
    if (error instanceof GatewayError && error.status === 404) return 'missing'
    if (error instanceof GatewayError && error.status === 403 && !error.authRequired) return 'denied'
    return 'unavailable'
  }
}

export async function captureSavedContext(project: WorkspaceProject, references: { terminal_ids: string[]; task_ids: string[] }, requestId: string): Promise<SavedContext> {
  return gatewayJson<SavedContext>(contextBase, { method: 'POST', body: {
    project_id: project.project.id, workspace: project.project.workspace_dir,
    terminal_ids: references.terminal_ids, task_ids: references.task_ids, request_id: requestId,
  } })
}

export async function reconcileSavedContext(context: SavedContext): Promise<LiveComparison> {
  return gatewayJson<LiveComparison>(`${contextBase}/${encodeURIComponent(context.id)}/reconcile`, { method: 'POST', body: {} })
}

export async function deleteSavedContext(context: SavedContext): Promise<void> {
  await gatewayJson(`${contextBase}/${encodeURIComponent(context.id)}?revision=${context.revision}`, { method: 'DELETE' })
}
