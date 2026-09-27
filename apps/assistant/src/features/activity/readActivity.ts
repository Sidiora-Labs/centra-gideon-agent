import type { OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type {
  Artifact, InboxItem, NotificationItem, PendingApproval, ScheduleRun, TaskItem,
  WorkflowRunSummary,
} from '../../../../console/src/shared/data/api'
import { mapActivitySource } from './sourceMap'
import type { ActivityEntry, ActivitySourceKind } from './types'

export const ACTIVITY_SOURCES = [
  'task', 'workflow_run', 'trigger_run', 'inbox_item', 'approval', 'notification', 'artifact', 'chat_session',
] as const satisfies readonly ActivitySourceKind[]

export type ActivityReadSource = (typeof ACTIVITY_SOURCES)[number]
export type SourcePhase = 'idle' | 'loading' | 'ready' | 'empty' | 'unavailable' | 'failed' | 'denied'
export type SourceCoverage = 'complete' | 'paged' | 'bounded' | 'missing_ids' | 'no_read_route'
export type ActivitySourceState = Readonly<{
  source: ActivityReadSource
  phase: SourcePhase
  freshness: 'unknown' | 'current' | 'stale'
  entries: readonly ActivityEntry[]
  error: string | null
  total: number | null
  nextOffset: number | null
  coverage: SourceCoverage
  omittedWithoutId: number
}>
export type ActivitySnapshot = Readonly<{
  ownerScopeKey: string | null
  sources: Readonly<Record<ActivityReadSource, ActivitySourceState>>
  entries: readonly ActivityEntry[]
}>

const PAGE_LIMIT = 20
const UNPAGED_LIMIT = 100

export function emptyActivitySnapshot(scope: OwnerScope | null): ActivitySnapshot {
  const sources = {} as Record<ActivityReadSource, ActivitySourceState>
  for (const source of ACTIVITY_SOURCES) {
    sources[source] = {
      source, phase: 'idle', freshness: 'unknown', entries: [], error: null,
      total: null, nextOffset: null, coverage: source === 'chat_session' ? 'no_read_route' : 'complete',
      omittedWithoutId: 0,
    }
  }
  return { ownerScopeKey: scope?.cacheKey ?? null, sources, entries: [] }
}

export function activityEntries(sources: ActivitySnapshot['sources']): readonly ActivityEntry[] {
  return Object.values(sources).flatMap(source => source.entries).sort((a, b) => {
    const time = (value: ActivityEntry['occurredAt']) =>
      typeof value === 'number' ? (value < 1e12 ? value * 1000 : value)
        : value ? Date.parse(value) : Number.NaN
    const left = time(a.occurredAt)
    const right = time(b.occurredAt)
    if (Number.isNaN(left) && Number.isNaN(right)) return a.identity.key.localeCompare(b.identity.key)
    if (Number.isNaN(left)) return 1
    if (Number.isNaN(right)) return -1
    return right - left || a.identity.key.localeCompare(b.identity.key)
  })
}

export function withActivitySource(snapshot: ActivitySnapshot, next: ActivitySourceState): ActivitySnapshot {
  const sources = { ...snapshot.sources, [next.source]: next }
  return { ownerScopeKey: snapshot.ownerScopeKey, sources, entries: activityEntries(sources) }
}

function rows<T>(value: unknown): T[] {
  if (!Array.isArray(value)) throw new TypeError('Gideon returned an invalid Activity list')
  return value as T[]
}

function page<T>(value: unknown, field: string): { rows: T[]; total: number } {
  if (!value || typeof value !== 'object') throw new TypeError('Gideon returned an invalid Activity page')
  const response = value as Record<string, unknown>
  if (!Number.isSafeInteger(response.total) || Number(response.total) < 0) {
    throw new TypeError('Gideon returned an invalid Activity total')
  }
  return { rows: rows<T>(response[field]), total: Number(response.total) }
}

async function nativeRows(source: ActivityReadSource, offset: number, signal?: AbortSignal):
  Promise<{ records: unknown[]; total: number | null; paged: boolean }> {
  const params = `limit=${PAGE_LIMIT}&offset=${offset}`
  switch (source) {
    case 'task': {
      const result = page<TaskItem>(await gatewayJson<unknown>(`/api/tasks?${params}&mine=1`, { signal }), 'tasks')
      return { records: result.rows, total: result.total, paged: true }
    }
    case 'workflow_run': {
      const result = page<WorkflowRunSummary>(await gatewayJson<unknown>(`/api/workflows/runs?${params}&mine=1`, { signal }), 'runs')
      return { records: result.rows, total: result.total, paged: true }
    }
    case 'trigger_run': {
      const result = page<ScheduleRun>(await gatewayJson<unknown>(`/api/triggers/history?${params}`, { signal }), 'runs')
      return { records: result.rows, total: result.total, paged: true }
    }
    case 'inbox_item': return { records: rows<InboxItem>(await gatewayJson<unknown>('/api/inbox/open', { signal })), total: null, paged: false }
    case 'approval': return { records: rows<PendingApproval>(await gatewayJson<unknown>('/api/approvals', { signal })), total: null, paged: false }
    case 'notification': {
      const result = await gatewayJson<{ notifications: NotificationItem[] }>('/api/notifications', { signal })
      return { records: rows<NotificationItem>(result.notifications), total: null, paged: false }
    }
    case 'artifact': {
      const result = await gatewayJson<{ artifacts: Artifact[] }>('/api/artifacts', { signal })
      return { records: rows<Artifact>(result.artifacts), total: null, paged: false }
    }
    case 'chat_session': return { records: [], total: null, paged: false }
  }
}

function failedSource(previous: ActivitySourceState, error: unknown): ActivitySourceState {
  const unavailable = error instanceof GatewayError && (error.status === 404 || error.status === 501 ||
    error.code === 'not_configured' || error.code === 'provider_unavailable')
  const denied = error instanceof GatewayError && error.status === 403
  return { ...previous, phase: denied ? 'denied' : unavailable ? 'unavailable' : 'failed',
    freshness: previous.freshness === 'current' || previous.freshness === 'stale' ? 'stale' : 'unknown',
    error: error instanceof Error ? error.message : String(error) }
}

export async function readActivitySource(scope: OwnerScope, source: ActivityReadSource,
  previous: ActivitySourceState, offset = 0, signal?: AbortSignal): Promise<ActivitySourceState> {
  if (source === 'chat_session') return { ...previous, phase: 'unavailable', coverage: 'no_read_route',
    freshness: 'unknown', error: 'Conversation activity has no read route in this projection.' }
  try {
    const result = await nativeRows(source, offset, signal)
    const window = result.paged ? result.records : result.records.slice(0, UNPAGED_LIMIT)
    const mapped = window.map(record => mapActivitySource(scope, source, record as never))
    const entries = mapped.flatMap(value => value.availability === 'available' ? [value.entry] : [])
    const omittedWithoutId = mapped.length - entries.length
    const nextOffset = result.paged && result.records.length > 0 &&
      (result.records.length === PAGE_LIMIT || (result.total !== null && offset + result.records.length < result.total))
      ? offset + result.records.length : null
    const combined = offset > 0 ? [...previous.entries, ...entries] : entries
    const coverage: SourceCoverage = omittedWithoutId ? 'missing_ids'
      : nextOffset !== null ? 'paged'
        : !result.paged && result.records.length > UNPAGED_LIMIT ? 'bounded' : 'complete'
    return { source, phase: combined.length ? 'ready' : 'empty', freshness: 'current', entries: combined,
      error: null, total: result.total, nextOffset, coverage,
      omittedWithoutId: (offset > 0 ? previous.omittedWithoutId : 0) + omittedWithoutId }
  } catch (error) {
    return failedSource(previous, error)
  }
}
