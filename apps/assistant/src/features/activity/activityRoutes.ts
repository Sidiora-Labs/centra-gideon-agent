import { readOwnerSession, type OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type {
  Artifact, InboxItem, NotificationItem, PendingApproval, ScheduleRun, Trigger,
} from '../../../../console/src/shared/data/api'

export type ActivityTriggerProjection = {
  id?: string
  trigger_id: string
  run_id?: string
  job_id?: string
  job_name?: string
  status?: string
  outcome?: string
  reason?: string
  started_at?: string | number
  finished_at?: string | number
  summary?: string
  error?: string
  counters?: Record<string, unknown>
  incomplete?: boolean
  trigger_source?: Pick<Trigger, 'id' | 'raw_id' | 'kind' | 'name'>
  source: 'native_run' | 'hook_summary' | 'event_summary'
}

export type ActivityDetailKind = 'trigger_run' | 'inbox_item' | 'approval' | 'notification' | 'artifact'
export type ActivityDetailRecord =
  | { kind: 'trigger_run'; id: string; record: ActivityTriggerProjection & ScheduleRun }
  | { kind: 'inbox_item'; id: string; record: InboxItem }
  | { kind: 'approval'; id: string; record: PendingApproval }
  | { kind: 'notification'; id: string; record: NotificationItem & { id: string } }
  | { kind: 'artifact'; id: string; record: Artifact }
export type ActivityDetailRead =
  | { state: 'ready'; value: ActivityDetailRecord }
  | { state: 'missing' | 'denied' | 'unavailable'; message: string }

function idOf(kind: ActivityDetailKind, record: unknown): string | undefined {
  if (!record || typeof record !== 'object') return undefined
  const value = record as Record<string, unknown>
  const id = kind === 'trigger_run' ? [value.run_id, value.id].find(candidate => typeof candidate === 'string' && candidate.trim())
    : kind === 'artifact' ? value.slug : value.id
  return typeof id === 'string' && id.trim() ? id : undefined
}

async function collectionRecord<T>(path: string, field: string | undefined, kind: ActivityDetailKind,
  id: string, signal?: AbortSignal): Promise<T> {
  const payload = await gatewayJson<unknown>(path, { signal })
  const rows = field ? (payload as Record<string, unknown>)?.[field] : payload
  if (!Array.isArray(rows)) throw new TypeError('Gideon returned an invalid record collection')
  const record = rows.find(value => idOf(kind, value) === id)
  if (!record) throw new GatewayError('This record is no longer available.', 404)
  return record as T
}

async function nativeDetail(kind: ActivityDetailKind, id: string, signal?: AbortSignal): Promise<ActivityDetailRecord> {
  const encoded = encodeURIComponent(id)
  switch (kind) {
    case 'trigger_run': {
      let offset = 0
      let total = 0
      let run: ScheduleRun | undefined
      do {
        const page = await gatewayJson<{ runs?: unknown; total?: unknown }>(
          `/api/triggers/history?limit=100&offset=${offset}`, { signal })
        if (!Array.isArray(page.runs) || typeof page.total !== 'number' || !Number.isSafeInteger(page.total)
          || page.total < 0) throw new TypeError('Gideon returned an invalid trigger history page')
        total = page.total
        run = page.runs.find(value => idOf(kind, value) === id) as ScheduleRun | undefined
        if (run) break
        offset += 100
      } while (offset < total)
      if (!run) throw new GatewayError('This record is no longer available.', 404)
      const triggerId = (run as ScheduleRun & { trigger_id?: unknown }).trigger_id
      const runId = [run.run_id, run.id].find(value => typeof value === 'string' && value.trim())
      if (typeof triggerId !== 'string' || !triggerId.trim()) {
        throw new GatewayError('This trigger record has no native detail identity.', 501)
      }
      const sourceKind = triggerId.split(':', 1)[0]
      if (sourceKind === 'schedule' || sourceKind === 'store') {
        if (typeof runId !== 'string') throw new GatewayError('This trigger record has no native per-run identity.', 501)
        const detail = await gatewayJson<{ run: ScheduleRun & { trigger_id?: string } }>(
          `/api/triggers/${encodeURIComponent(triggerId)}/history/${encodeURIComponent(runId)}`, { signal })
        if ((detail.run.run_id ?? detail.run.id) !== runId) {
          throw new GatewayError('The native trigger run identity did not match the requested record.', 404)
        }
        return { kind, id, record: { ...detail.run, trigger_id: triggerId, run_id: runId, source: 'native_run' } }
      }
      if (sourceKind !== 'lifecycle' && sourceKind !== 'event') {
        throw new GatewayError('This trigger record has an unsupported native source.', 501)
      }
      const sources = await gatewayJson<{ triggers?: unknown }>('/api/triggers', { signal })
      if (!Array.isArray(sources.triggers)) throw new TypeError('Gideon returned an invalid trigger collection')
      const trigger = sources.triggers.find(value => Boolean(value && typeof value === 'object'
        && (value as Record<string, unknown>).id === triggerId)) as Trigger | undefined
      if (!trigger || trigger.kind !== sourceKind) throw new GatewayError('This trigger source is no longer available.', 404)
      return { kind, id, record: { ...run, trigger_id: triggerId, run_id: typeof runId === 'string' ? runId : undefined,
        trigger_source: { id: trigger.id, raw_id: trigger.raw_id, kind: trigger.kind, name: trigger.name },
        source: sourceKind === 'event' ? 'event_summary' : 'hook_summary' } }
    }
    case 'inbox_item': return { kind, id, record: await collectionRecord<InboxItem>(
      '/api/inbox?mine=1', undefined, kind, id, signal) }
    case 'approval': return { kind, id, record: await collectionRecord<PendingApproval>(
      '/api/approvals', undefined, kind, id, signal) }
    case 'notification': return { kind, id, record: await collectionRecord<NotificationItem & { id: string }>(
      '/api/notifications', 'notifications', kind, id, signal) }
    case 'artifact': {
      const record = await gatewayJson<Artifact>(`/api/artifacts/${encoded}`, { signal })
      if (record.slug !== id) throw new GatewayError('The requested artifact no longer exists.', 404)
      return { kind, id, record }
    }
  }
}

export async function readActivityDetail(scope: OwnerScope, kind: ActivityDetailKind, id: string,
  signal?: AbortSignal): Promise<ActivityDetailRead> {
  if (!scope.ownerId || !scope.cacheKey) return { state: 'denied', message: 'Sign in to the record owner account to open this item.' }
  try {
    if (typeof location === 'undefined' || scope.runtimeOrigin !== location.origin) {
      return { state: 'denied', message: 'Sign in to the record owner account to open this item.' }
    }
    const session = await readOwnerSession(signal)
    if (session.user !== scope.ownerId) {
      return { state: 'denied', message: 'Sign in to the record owner account to open this item.' }
    }
    const value = await nativeDetail(kind, id, signal)
    if (value.id !== id) return { state: 'missing', message: 'The requested native record no longer exists.' }
    return { state: 'ready', value }
  } catch (error) {
    if (signal?.aborted) throw error
    if (error instanceof GatewayError && (error.status === 401 || error.status === 403)) {
      return { state: 'denied', message: 'Your account cannot access this record.' }
    }
    if (error instanceof GatewayError && error.status === 404) {
      return { state: 'missing', message: 'This record was removed or is no longer available to your account.' }
    }
    if (error instanceof GatewayError && error.status === 501) {
      return { state: 'unavailable', message: error.message }
    }
    return { state: 'unavailable', message: error instanceof Error ? error.message : 'Gideon could not load this record.' }
  }
}
