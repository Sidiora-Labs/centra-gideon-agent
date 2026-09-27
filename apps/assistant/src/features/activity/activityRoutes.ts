import type { OwnerScope } from '../../shared/auth.web'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import type {
  Artifact, InboxItem, NotificationItem, PendingApproval, ScheduleRun,
} from '../../../../console/src/shared/data/api'

export type ActivityDetailKind = 'trigger_run' | 'inbox_item' | 'approval' | 'notification' | 'artifact'
export type ActivityDetailRecord =
  | { kind: 'trigger_run'; id: string; record: ScheduleRun }
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
  const id = kind === 'trigger_run' ? value.run_id ?? value.id
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
      const run = await collectionRecord<ScheduleRun>('/api/triggers/history?limit=100&offset=0', 'runs', kind, id, signal)
      if (!run.job_id || !(run.run_id ?? run.id)) {
        throw new GatewayError('This trigger record has no native per-run detail route.', 501)
      }
      const job = encodeURIComponent(run.job_id)
      const runId = encodeURIComponent(run.run_id ?? run.id!)
      const detail = await gatewayJson<{ run: ScheduleRun }>(
        `/api/triggers/schedule:${job}/history/${runId}`, { signal })
      return { kind, id, record: detail.run }
    }
    case 'inbox_item': return { kind, id, record: await collectionRecord<InboxItem>(
      '/api/inbox?mine=1', undefined, kind, id, signal) }
    case 'approval': return { kind, id, record: await collectionRecord<PendingApproval>(
      '/api/approvals', undefined, kind, id, signal) }
    case 'notification': return { kind, id, record: await collectionRecord<NotificationItem & { id: string }>(
      '/api/notifications', 'notifications', kind, id, signal) }
    case 'artifact': return { kind, id, record: await gatewayJson<Artifact>(`/api/artifacts/${encoded}`, { signal }) }
  }
}

export async function readActivityDetail(scope: OwnerScope, kind: ActivityDetailKind, id: string,
  signal?: AbortSignal): Promise<ActivityDetailRead> {
  if (!scope.ownerId || !scope.cacheKey) return { state: 'denied', message: 'Sign in to the record owner account to open this item.' }
  try {
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
