import { describe, expect, expectTypeOf, it } from 'vitest'
import type { OwnerScope } from '../../shared/auth.web'
import type { ShellRoute } from '../../shared/shell/shellRoutes'
import type { ChatDetail } from '../../shared/conversation/types'
import type {
  Artifact, InboxItem, NotificationItem, PendingApproval, ScheduleRun,
  TaskItem, WorkflowRunSummary,
} from '../../../../console/src/shared/data/api'
import type { ActivityEntry, ActivityRelatedIds, NativeId } from './types'
import { activityKey, mapActivitySource, nativeId, nativeStatus } from './sourceMap'

const ownerA: OwnerScope = {
  runtimeOrigin: 'https://gideon.example', ownerId: 'one', cacheKey: '["https://gideon.example","one"]',
}
const ownerB: OwnerScope = {
  runtimeOrigin: 'https://gideon.example', ownerId: 'two', cacheKey: '["https://gideon.example","two"]',
}

const task: TaskItem = {
  id: 'same-id', title: 'Prepare result', status: 'blocked',
  created_at: '2026-09-26T12:00:00Z',
}
const workflow: WorkflowRunSummary = {
  id: 'same-id', workflow_name: 'Prepare result', status: 'needs_input', spec_version: 1,
  created_at: '2026-09-26T12:00:00Z',
}

function entry<K extends Parameters<typeof mapActivitySource>[1]>(mapped: ReturnType<typeof mapActivitySource<K>>) {
  expect(mapped.availability).toBe('available')
  if (mapped.availability !== 'available') throw new Error('Expected an Activity entry')
  return mapped.entry
}

describe('Activity source identity', () => {
  it('keeps account, source record, event, and related IDs separate', () => {
    const taskA = entry(mapActivitySource(ownerA, 'task', task, {
      latestEventId: 'event-1', related: { workflowRunId: nativeId('workflow_run', 'same-id') ?? undefined },
    }))
    const taskARefresh = entry(mapActivitySource(ownerA, 'task', { ...task, status: 'done' }, {
      latestEventId: 'event-2',
    }))
    const taskB = entry(mapActivitySource(ownerB, 'task', task))
    const workflowA = entry(mapActivitySource(ownerA, 'workflow_run', workflow))

    expect(taskA.identity.key).toBe(taskARefresh.identity.key)
    expect(taskA.latestEventId).toBe('event-1')
    expect(taskARefresh.latestEventId).toBe('event-2')
    expect(taskA.identity.sourceId).toBe('same-id')
    expect(taskA.related.taskId).toBe('same-id')
    expect(taskA.related.workflowRunId).toBe('same-id')
    expect(taskA.identity.key).not.toBe(taskB.identity.key)
    expect(taskA.identity.key).not.toBe(workflowA.identity.key)
    expect(JSON.parse(taskA.identity.key)).toEqual([ownerA.cacheKey, 'task', 'same-id'])
    expect(taskA.destination.ownerScopeKey).toBe(ownerA.cacheKey)
    expect(taskA.destination.route.record).toEqual({ kind: 'task', id: 'same-id' })
    expect(workflowA.destination.route.record).toEqual({ kind: 'workflow_run', id: 'same-id' })
    expectTypeOf(taskA.destination.route).toMatchTypeOf<ShellRoute>()
    expectTypeOf<ActivityRelatedIds['taskId']>().not.toEqualTypeOf<ActivityRelatedIds['workflowRunId']>()
    expectTypeOf<ActivityEntry<'task'>['identity']['sourceId']>().not.toEqualTypeOf<NativeId<'workflow_run'>>()
  })

  it('retains distinct native task and workflow outcomes, including unknown task status', () => {
    const taskCases = [
      ['open', 'queued'], ['in_progress', 'working'], ['blocked', 'blocked'],
      ['done', 'complete'], ['cancelled', 'cancelled'], ['skipped', 'skipped'],
      ['surprising_status', 'unknown'],
    ] as const
    for (const [status, outcome] of taskCases) {
      const mapped = entry(mapActivitySource(ownerA, 'task', { ...task, status }))
      expect(mapped.status).toEqual({ native: status, outcome })
    }

    const runCases = [
      ['draft', 'queued'], ['running', 'working'], ['paused', 'paused'],
      ['needs_input', 'waiting_input'], ['complete', 'complete'],
      ['failed', 'failed'], ['cancelled', 'cancelled'], ['escalated', 'escalated'],
    ] as const
    for (const [status, outcome] of runCases) {
      const mapped = entry(mapActivitySource(ownerA, 'workflow_run', { ...workflow, status }))
      expect(mapped.status).toEqual({ native: status, outcome })
    }
    expect(nativeStatus('workflow_run', 'future_state').outcome).toBe('unknown')
    expect(nativeStatus('trigger_run', 'skipped_by_policy').outcome).toBe('skipped')
    expect(nativeStatus('trigger_run', 'failed').outcome).toBe('failed')
  })

  it('preserves approval, inbox, session, and artifact links without merging their identities', () => {
    const approval: PendingApproval = {
      id: 'review-1', source: 'tool', tool: 'send', session: 'chat-1', ts: 1_779_000_000,
    }
    const inbox: InboxItem = {
      id: 'inbox-1', channel: 'assistant', channel_name: 'Assistant', message: 'Review send',
      sender_id: 'system', sender_name: 'Gideon', classification: 'needs_reply', confidence: 'high',
      status: 'pending', item_kind: 'agent_request', refs: {
        approval: 'review-1', task_id: 'task-1', run_id: 'run-1', artifact_slug: 'result-1', session: 'chat-1',
      },
    }
    const chat: ChatDetail = { key: 'chat-1', title: 'The conversation', running: true, messages: [] }
    const artifact: Artifact = {
      slug: 'result-1', name: 'Result', kind: 'markdown', source: 'chat', description: 'Output',
      tags: [], version: 1, created_at: '2026-09-26T12:00:00Z', updated_at: '2026-09-26T12:00:00Z',
      events: [], source_path: 'result.md', readonly: true,
    }
    const approvalEntry = entry(mapActivitySource(ownerA, 'approval', approval))
    const inboxEntry = entry(mapActivitySource(ownerA, 'inbox_item', inbox))
    const chatEntry = entry(mapActivitySource(ownerA, 'chat_session', chat))
    const artifactEntry = entry(mapActivitySource(ownerA, 'artifact', artifact))

    expect(approvalEntry.status.outcome).toBe('waiting_approval')
    expect(approvalEntry.related.chatSessionId).toBe('chat-1')
    expect(inboxEntry.status.outcome).toBe('waiting_approval')
    expect(inboxEntry.identity.sourceId).toBe('inbox-1')
    expect(inboxEntry.related).toMatchObject({ approvalId: 'review-1', inboxItemId: 'inbox-1',
      taskId: 'task-1', workflowRunId: 'run-1', artifactId: 'result-1', chatSessionId: 'chat-1' })
    expect(inboxEntry.identity.key).not.toBe(approvalEntry.identity.key)
    expect(nativeStatus('inbox_item', 'pending', { itemKind: 'system' }).outcome).toBe('notice')
    expect(chatEntry.destination.route.destination).toBe('chat')
    expect(artifactEntry.related.artifactId).toBe('result-1')
  })

  it('omits records without native IDs and leaves unknown date and progress unknown', () => {
    const notification: NotificationItem = {
      kind: 'system', title: 'Result ready', body: 'Open the result', ts: '2026-09-26T12:00:00Z', acked: false,
    }
    const run: ScheduleRun = { job_id: 'schedule-1', job_name: 'Daily summary', status: 'running' }
    expect(mapActivitySource(ownerA, 'notification', notification)).toEqual({
      availability: 'unavailable', sourceKind: 'notification', reason: 'missing_native_id',
    })
    expect(mapActivitySource(ownerA, 'trigger_run', run)).toEqual({
      availability: 'unavailable', sourceKind: 'trigger_run', reason: 'missing_native_id',
    })
    const identifiedRun = entry(mapActivitySource(ownerA, 'trigger_run', { ...run, run_id: 'trigger-1' }))
    const identifiedNotice = entry(mapActivitySource(ownerA, 'notification', { ...notification, id: 'notice-1' }))
    const unknownTask = entry(mapActivitySource(ownerA, 'task', {
      ...task, status: 'unexpected', created_at: 'invalid', updated_at: undefined,
    }))
    expect(identifiedRun.identity.sourceId).toBe('trigger-1')
    expect(identifiedRun.occurredAt).toBeNull()
    expect(identifiedRun.progressPercent).toBeNull()
    expect(identifiedNotice.actionability).toBe('none')
    expect(identifiedNotice.identity.key).not.toBe(activityKey(ownerA, 'inbox_item', nativeId('inbox_item', 'notice-1')!))
    expect(unknownTask.occurredAt).toBeNull()
    expect(unknownTask.progressPercent).toBeNull()
    expect(unknownTask.status).toEqual({ native: 'unexpected', outcome: 'unknown' })
  })
})
