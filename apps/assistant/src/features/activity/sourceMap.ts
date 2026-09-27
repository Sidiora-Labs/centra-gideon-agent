import type { OwnerScope } from '../../shared/auth.web'
import { createShellRoute } from '../../shared/shell/shellRoutes'
import type {
  ActivityEntry, ActivityIdentity, ActivityMapped, ActivityOutcome,
  ActivityRelatedIds, ActivitySourceKind, ActivitySourceRecords, NativeId,
} from './types'

export function nativeId<K extends ActivitySourceKind>(kind: K, value: unknown): NativeId<K> | null {
  void kind
  return typeof value === 'string' && value.trim() !== '' ? value as NativeId<K> : null
}

export function activityKey<K extends ActivitySourceKind>(scope: OwnerScope, kind: K,
  id: NativeId<K>): string {
  if (!scope.cacheKey || !id) throw new TypeError('An owner scope and native ID are required')
  return JSON.stringify([scope.cacheKey, kind, id])
}

export function activityIdentity<K extends ActivitySourceKind>(scope: OwnerScope, kind: K,
  id: NativeId<K>): ActivityIdentity<K> {
  return { ownerScopeKey: scope.cacheKey, sourceKind: kind, sourceId: id,
    key: activityKey(scope, kind, id) }
}

export function activityTime(value: unknown): string | number | null {
  if (typeof value === 'number') return Number.isFinite(value) && value > 0 ? value : null
  if (typeof value === 'string' && value.trim() && Number.isFinite(Date.parse(value))) return value
  return null
}

const TASK_STATUSES: Readonly<Record<string, ActivityOutcome>> = {
  open: 'queued', in_progress: 'working', blocked: 'blocked', done: 'complete',
  cancelled: 'cancelled', skipped: 'skipped',
}
const WORKFLOW_STATUSES: Readonly<Record<string, ActivityOutcome>> = {
  draft: 'queued', running: 'working', paused: 'paused', needs_input: 'waiting_input',
  complete: 'complete', failed: 'failed', cancelled: 'cancelled', escalated: 'escalated',
}
const TRIGGER_STATUSES: Readonly<Record<string, ActivityOutcome>> = {
  pending: 'queued', queued: 'queued', running: 'working', blocked: 'blocked',
  paused: 'paused', needs_input: 'waiting_input', needs_approval: 'waiting_approval',
  skipped: 'skipped', cancelled: 'cancelled', failed: 'failed', error: 'failed',
  complete: 'complete', completed: 'complete', success: 'complete',
}

export function nativeStatus(kind: ActivitySourceKind, native: unknown,
  details?: Readonly<{ itemKind?: string; approvalId?: string }>): Readonly<{ native: string | null; outcome: ActivityOutcome }> {
  const value = typeof native === 'string' && native.trim() ? native : null
  if (kind === 'task') return { native: value, outcome: value ? TASK_STATUSES[value] ?? 'unknown' : 'unknown' }
  if (kind === 'workflow_run') return { native: value, outcome: value ? WORKFLOW_STATUSES[value] ?? 'unknown' : 'unknown' }
  if (kind === 'trigger_run') return { native: value,
    outcome: value ? TRIGGER_STATUSES[value] ?? (value.startsWith('skipped_') ? 'skipped' : 'unknown') : 'unknown' }
  if (kind === 'approval') return { native: value, outcome: 'waiting_approval' }
  if (kind === 'artifact') return { native: value, outcome: 'available' }
  if (kind === 'notification') return { native: value,
    outcome: value === 'acknowledged' ? 'acknowledged' : value === 'notice' ? 'notice' : 'unknown' }
  if (kind === 'chat_session') return { native: value,
    outcome: value === 'waiting_approval' ? 'waiting_approval'
      : value === 'stopping' ? 'stopping' : value === 'working' ? 'working' : 'unknown' }
  if (kind === 'inbox_item') {
    if ((value === 'pending' || value === 'seen') && details?.approvalId) {
      return { native: value, outcome: 'waiting_approval' }
    }
    if ((value === 'pending' || value === 'seen') &&
      (details?.itemKind === 'needs_input' || details?.itemKind === 'agent_request' ||
        details?.itemKind === 'proposal' || details?.itemKind === 'user_note')) {
      return { native: value, outcome: 'waiting_input' }
    }
    const outcomes: Readonly<Record<string, ActivityOutcome>> = {
      pending: 'notice', seen: 'seen', sent: 'sent', handled: 'handled',
      dismissed: 'dismissed', filtered: 'filtered',
    }
    return { native: value, outcome: value ? outcomes[value] ?? 'unknown' : 'unknown' }
  }
  return { native: value, outcome: 'unknown' }
}

function fromRefs(refs: unknown): ActivityRelatedIds {
  if (!refs || typeof refs !== 'object') return {}
  const values = refs as Record<string, unknown>
  return {
    taskId: nativeId('task', values.task ?? values.task_id) ?? undefined,
    workflowRunId: nativeId('workflow_run', values.workflow_run ?? values.run_id) ?? undefined,
    triggerRunId: nativeId('trigger_run', values.trigger_run) ?? undefined,
    approvalId: nativeId('approval', values.approval) ?? undefined,
    inboxItemId: nativeId('inbox_item', values.inbox_item) ?? undefined,
    artifactId: nativeId('artifact', values.artifact ?? values.artifact_slug) ?? undefined,
    chatSessionId: nativeId('chat_session', values.session ?? values.session_id) ?? undefined,
    notificationId: nativeId('notification', values.notification) ?? undefined,
  }
}

type MapOptions = Readonly<{
  latestEventId?: string | null
  related?: ActivityRelatedIds
}>

export function mapActivitySource<K extends ActivitySourceKind>(scope: OwnerScope, kind: K,
  record: ActivitySourceRecords[K], options: MapOptions = {}): ActivityMapped<K> {
  const item = record as ActivitySourceRecords[ActivitySourceKind]
  let id: NativeId<K> | null = null
  let occurredAt: string | number | null = null
  let title = ''
  let summary: string | null = null
  let status: ActivityEntry['status'] = { native: null, outcome: 'unknown' }
  let actionability: ActivityEntry['actionability'] = 'open'
  let related: ActivityRelatedIds = options.related ?? {}

  switch (kind) {
    case 'task': {
      const task = item as ActivitySourceRecords['task']
      id = nativeId(kind, task.id)
      occurredAt = activityTime(task.updated_at ?? task.created_at)
      title = task.title
      summary = task.description ?? null
      status = nativeStatus(kind, task.status)
      related = { ...related, taskId: nativeId('task', task.id) ?? undefined }
      break
    }
    case 'workflow_run': {
      const run = item as ActivitySourceRecords['workflow_run']
      id = nativeId(kind, run.id)
      occurredAt = activityTime(run.completed_at ?? run.started_at ?? run.created_at)
      title = run.workflow_name
      summary = run.error_message ?? null
      status = nativeStatus(kind, run.status)
      related = { ...related, workflowRunId: nativeId('workflow_run', run.id) ?? undefined }
      break
    }
    case 'trigger_run': {
      const run = item as ActivitySourceRecords['trigger_run']
      id = nativeId(kind, run.run_id) ?? nativeId(kind, run.id)
      occurredAt = activityTime(run.finished_at ?? run.started_at)
      title = run.job_name ?? 'Scheduled run'
      summary = run.summary ?? run.error ?? null
      status = nativeStatus(kind, run.status)
      related = { ...related, triggerRunId: id as NativeId<'trigger_run'> | null ?? undefined }
      break
    }
    case 'chat_session': {
      const chat = item as ActivitySourceRecords['chat_session']
      const key = 'sessionId' in chat ? chat.sessionId : chat.key
      id = nativeId(kind, key)
      occurredAt = 'last_ts' in chat ? activityTime(chat.last_ts) : null
      title = chat.title
      const approval = 'pending_approval' in chat && chat.pending_approval
      const stopping = 'stopping' in chat && chat.stopping
      const running = chat.running
      status = nativeStatus(kind, approval ? 'waiting_approval' : stopping ? 'stopping' : running ? 'working' : null)
      related = { ...related, chatSessionId: id as NativeId<'chat_session'> | null ?? undefined }
      break
    }
    case 'inbox_item': {
      const inbox = item as ActivitySourceRecords['inbox_item']
      id = nativeId(kind, inbox.id)
      occurredAt = activityTime(inbox.created_at ?? inbox.ts)
      title = inbox.message
      summary = inbox.context_summary ?? null
      const refs = fromRefs(inbox.refs)
      related = { ...refs, ...related, inboxItemId: nativeId('inbox_item', inbox.id) ?? undefined }
      status = nativeStatus(kind, inbox.status,
        { itemKind: inbox.item_kind, approvalId: related.approvalId })
      actionability = status.outcome === 'waiting_approval' ? 'review'
        : status.outcome === 'waiting_input' ? 'reply' : 'open'
      break
    }
    case 'approval': {
      const approval = item as ActivitySourceRecords['approval']
      id = nativeId(kind, approval.id)
      occurredAt = activityTime(approval.ts)
      title = approval.tool
      summary = approval.tool_purpose ?? null
      status = nativeStatus(kind, 'pending')
      actionability = 'review'
      related = { chatSessionId: nativeId('chat_session', approval.session) ?? undefined,
        ...related, approvalId: nativeId('approval', approval.id) ?? undefined }
      break
    }
    case 'notification': {
      const notification = item as ActivitySourceRecords['notification']
      id = nativeId(kind, notification.id)
      occurredAt = activityTime(notification.ts)
      title = notification.title
      summary = notification.body
      status = nativeStatus(kind, notification.acked ? 'acknowledged' : 'notice')
      actionability = 'none'
      related = { ...related, notificationId: id as NativeId<'notification'> | null ?? undefined }
      break
    }
    case 'artifact': {
      const artifact = item as ActivitySourceRecords['artifact']
      id = nativeId(kind, artifact.slug)
      occurredAt = activityTime(artifact.updated_at ?? artifact.created_at)
      title = artifact.name
      summary = artifact.description
      status = nativeStatus(kind, artifact.kind)
      related = { ...related, artifactId: nativeId('artifact', artifact.slug) ?? undefined }
      break
    }
  }

  if (!id) return { availability: 'unavailable', sourceKind: kind, reason: 'missing_native_id' }
  const destination = createShellRoute(kind === 'chat_session' ? 'chat' : 'activity', {
    view: 'detail', record: { kind, id },
    ...(kind === 'chat_session' ? { sessionId: id } : {}),
  }) as ActivityEntry['destination']['route']
  return { availability: 'available', entry: {
    identity: activityIdentity(scope, kind, id), latestEventId: options.latestEventId ?? null,
    occurredAt, progressPercent: null, status, actionability, title, summary, related,
    destination: { ownerScopeKey: scope.cacheKey, permission: 'unchecked', route: destination },
  } }
}
