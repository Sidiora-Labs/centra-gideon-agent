import type { OwnerScope } from '../../shared/auth.web'
import { gatewayJson, GatewayError } from '../../shared/transport.web'
import type {
  AgentDef, ExperimentCampaign, Loop, ProjectItem, SavedAgent, SkillItem,
  TaskItem, ToolItem, Trigger, WorkflowDefSummary, WorkflowRunDetailData, WorkflowRunSummary,
} from '../../../../console/src/shared/data/api'
import type { Room } from '../../../../console/src/features/rooms/roomsApi'

export type WorkKind = 'task' | 'project' | 'workflow' | 'workflow_run' | 'trigger' |
  'loop' | 'room' | 'agent' | 'skill' | 'tool' | 'experiment'

export type WorkRecords = {
  task: TaskItem
  project: ProjectItem
  workflow: WorkflowDefSummary
  workflow_run: WorkflowRunSummary | WorkflowRunDetailData
  trigger: Trigger
  loop: Loop
  room: Room
  agent: SavedAgent | AgentDef
  skill: SkillItem
  tool: ToolItem
  experiment: ExperimentCampaign
}

export type WorkIdentity<K extends WorkKind = WorkKind> = Readonly<{
  ownerScopeKey: OwnerScope['cacheKey']
  kind: K
  id: string
  revision: string | number | null
}>

export type WorkEntry<K extends WorkKind = WorkKind> = Readonly<{
  identity: WorkIdentity<K>
  title: string
  status: string | null
  record: WorkRecords[K]
}>

export type WorkRead<T> =
  | Readonly<{ state: 'ready'; value: T; checkedAt: number }>
  | Readonly<{ state: 'empty'; value: T; checkedAt: number }>
  | Readonly<{ state: 'stale'; value: T; checkedAt: number; reason: string }>
  | Readonly<{ state: 'unavailable' | 'denied' | 'failed'; reason: string }>

const names: Record<WorkKind, string> = {
  task: 'task', project: 'project', workflow: 'workflow', workflow_run: 'workflow run',
  trigger: 'trigger', loop: 'loop', room: 'room', agent: 'agent', skill: 'skill',
  tool: 'tool', experiment: 'experiment',
}

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

function field(value: unknown, key: string): unknown {
  return object(value)?.[key]
}

function nonempty(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function nativeId(kind: WorkKind, record: unknown): string | null {
  if (kind === 'tool') {
    const provider = nonempty(field(record, 'provider'))
    const name = nonempty(field(record, 'name'))
    return provider && name ? JSON.stringify([provider, name]) : null
  }
  if (kind === 'workflow' || kind === 'agent' || kind === 'skill') {
    return nonempty(field(record, 'name'))
  }
  if (kind === 'workflow_run') return nonempty(field(record, 'run_id')) ?? nonempty(field(record, 'id'))
  if (kind === 'trigger') return nonempty(field(record, 'id'))
  return nonempty(field(record, 'id'))
}

function nativeTitle(kind: WorkKind, record: unknown, id: string): string {
  return nonempty(field(record, 'title')) ?? nonempty(field(record, 'name')) ?? id
}

export function workEntry<K extends WorkKind>(scope: OwnerScope, kind: K, record: WorkRecords[K]): WorkEntry<K> {
  const id = nativeId(kind, record)
  if (!id || !scope.cacheKey) throw new TypeError(`A native ${names[kind]} ID and owner are required`)
  const revision = field(record, 'revision') ?? field(record, 'version') ?? field(record, 'updated_at')
  return Object.freeze({
    identity: Object.freeze({ ownerScopeKey: scope.cacheKey, kind, id,
      revision: typeof revision === 'string' || typeof revision === 'number' ? revision : null }),
    title: nativeTitle(kind, record, id),
    status: nonempty(field(record, 'status')) ?? nonempty(field(record, 'state')),
    record,
  })
}

export function classifyWorkError(error: unknown): 'unavailable' | 'denied' | 'failed' {
  if (error instanceof GatewayError) {
    if (error.status === 401 || error.status === 403) return 'denied'
    if (error.status === 404 || error.status === 410 || error.status === 501 || error.status === 503) return 'unavailable'
  }
  return 'failed'
}

function reason(error: unknown): string {
  return error instanceof Error && error.message.trim() ? error.message : 'The record could not be read.'
}

function requireRows<T>(payload: unknown, property?: string): T[] {
  const rows = property ? field(payload, property) : payload
  if (!Array.isArray(rows)) throw new TypeError('Gideon returned an invalid catalogue')
  return rows as T[]
}

function enc(id: string): string {
  if (!id.trim()) throw new TypeError('A native record ID is required')
  return encodeURIComponent(id)
}

const cacheKey = (...parts: string[]) => JSON.stringify(parts)

async function nativeList<K extends WorkKind>(kind: K, signal?: AbortSignal): Promise<WorkRecords[K][]> {
  switch (kind) {
    case 'task': return requireRows<WorkRecords[K]>(await gatewayJson('/api/tasks', { signal }), 'tasks')
    case 'project': return requireRows<WorkRecords[K]>(await gatewayJson('/api/projects', { signal }), 'projects')
    case 'workflow': return requireRows<WorkRecords[K]>(await gatewayJson('/api/workflows', { signal }), 'defs')
    case 'workflow_run': return requireRows<WorkRecords[K]>(await gatewayJson('/api/workflows/runs', { signal }), 'runs')
    case 'trigger': return requireRows<WorkRecords[K]>(await gatewayJson('/api/triggers', { signal }), 'triggers')
    case 'loop': return requireRows<WorkRecords[K]>(await gatewayJson('/api/loops', { signal }), 'loops')
    case 'room': return requireRows<WorkRecords[K]>(await gatewayJson('/api/rooms', { signal }), 'rooms')
    case 'agent': return requireRows<WorkRecords[K]>(await gatewayJson('/api/agents', { signal }), 'agents')
    case 'skill': return requireRows<WorkRecords[K]>(await gatewayJson('/api/skills', { signal }))
    case 'tool': return requireRows<WorkRecords[K]>(await gatewayJson('/api/tools', { signal }), 'tools')
    case 'experiment': return requireRows<WorkRecords[K]>(await gatewayJson('/api/experiments/campaigns', { signal }), 'campaigns')
  }
}

async function nativeDetail<K extends WorkKind>(kind: K, id: string, signal?: AbortSignal): Promise<WorkRecords[K]> {
  const path = enc(id)
  switch (kind) {
    case 'task': return gatewayJson(`/api/tasks/${path}`, { signal })
    case 'project': return gatewayJson(`/api/projects/${path}`, { signal })
    case 'workflow': {
      const response = await gatewayJson<{ definition: WorkRecords[K] }>(`/api/workflows/${path}`, { signal })
      return response.definition
    }
    case 'workflow_run': return gatewayJson(`/api/workflows/runs/${path}`, { signal })
    case 'loop': return gatewayJson(`/api/loops/${path}`, { signal })
    case 'room': {
      const response = await gatewayJson<{ room: WorkRecords[K] }>(`/api/rooms/${path}`, { signal })
      return response.room
    }
    case 'agent': return gatewayJson(`/api/agents/detail/${path}`, { signal })
    case 'skill':
    case 'tool':
    case 'trigger': {
      const rows = await nativeList(kind, signal)
      const found = rows.find(row => nativeId(kind, row) === id)
      if (!found) throw new GatewayError(`${names[kind]} is no longer available`, 404)
      return found as WorkRecords[K]
    }
    case 'experiment': return gatewayJson(`/api/experiments/campaigns/${path}`, { signal })
  }
}

export class WorkClient {
  private readonly cache = new Map<string, { value: unknown; checkedAt: number }>()

  constructor(readonly scope: OwnerScope) {
    if (!scope.cacheKey || !scope.ownerId) throw new TypeError('An authenticated owner is required')
  }

  clear(): void { this.cache.clear() }

  private async read<T>(key: string, load: () => Promise<T>, empty: (value: T) => boolean): Promise<WorkRead<T>> {
    try {
      const value = await load()
      const checkedAt = Date.now()
      this.cache.set(key, { value, checkedAt })
      return empty(value) ? { state: 'empty', value, checkedAt } : { state: 'ready', value, checkedAt }
    } catch (error) {
      const state = classifyWorkError(error)
      const cached = this.cache.get(key)
      if (state === 'failed' && cached) return { state: 'stale', value: cached.value as T,
        checkedAt: cached.checkedAt, reason: reason(error) }
      return { state, reason: reason(error) }
    }
  }

  list<K extends WorkKind>(kind: K, signal?: AbortSignal): Promise<WorkRead<WorkEntry<K>[]>> {
    return this.read(cacheKey(kind, 'list'), async () => (await nativeList(kind, signal))
      .map(record => workEntry(this.scope, kind, record)), rows => rows.length === 0)
  }

  detail<K extends WorkKind>(kind: K, id: string, signal?: AbortSignal): Promise<WorkRead<WorkEntry<K>>> {
    return this.read(cacheKey(kind, 'detail', id), async () => {
      const entry = workEntry(this.scope, kind, await nativeDetail(kind, id, signal))
      if (entry.identity.id !== id) throw new TypeError('Gideon returned a different record')
      return entry
    }, () => false)
  }

  skillContent(id: string, signal?: AbortSignal): Promise<WorkRead<string>> {
    return this.read(cacheKey('skill', 'content', id), async () => {
      const result = await gatewayJson<{ content: string }>(`/api/skills/${enc(id)}`, { signal })
      if (typeof result.content !== 'string') throw new TypeError('Gideon returned invalid skill content')
      return result.content
    }, content => content.length === 0)
  }

  async saveSkill(id: string, content: string): Promise<void> {
    const result = await gatewayJson<{ ok: boolean }>(`/api/skills/${enc(id)}`, { method: 'PUT', body: { content } })
    if (result.ok !== true) throw new Error('Gideon did not confirm the skill update')
    this.cache.delete(cacheKey('skill', 'content', id))
    this.cache.delete(cacheKey('skill', 'detail', id))
  }

  async invokeTool(id: string, provider: string, args: Record<string, unknown>,
    confirmRisk = false): Promise<{ ok: boolean; output?: string; error?: string }> {
    let parts: unknown
    try { parts = JSON.parse(id) } catch { throw new TypeError('Invalid native tool identity') }
    if (!Array.isArray(parts) || parts.length !== 2 || parts[0] !== provider ||
      typeof parts[1] !== 'string' || !parts[1]) throw new TypeError('Invalid native tool identity')
    const result = await gatewayJson<{ ok: boolean; output?: string; error?: string }>('/api/tools/invoke', {
      method: 'POST', body: { tool: parts[1], provider, arguments: args,
        ...(confirmRisk ? { confirm_risk: 'destructive' } : {}) },
    })
    if (typeof result.ok !== 'boolean') throw new TypeError('Gideon returned an invalid tool result')
    return result
  }
}
