import { describe, expect, it } from 'vitest'
import { adaptConversationMessages } from '../../shared/conversation/turnAdapter'
import type { ConversationMessage } from '../../shared/conversation/types'
import { resultRecordRoute } from './ResultCard'

describe('typed conversation turn adaptation', () => {
  it('retains live producer, source, tool, approval, and result identities and statuses', () => {
    const messages: ConversationMessage[] = [
      { id: 'live-tool', role: 'tool', content: 'Search records', meta: {
        tool_call_id: 'call-91', producer_kind: 'agent', producer_id: 'gideon', source_id: 'search-7',
        done: true, ok: false, status: 'rejected', output: 'Access denied',
      } },
      { id: 'live-approval', role: 'permission', content: 'delete_task', meta: {
        approval_id: 'approval-14', producer_kind: 'tool', producer_id: 'task.delete',
        source_id: 'task-6', status: 'waiting_approval', tool_input: '{"task":"task-6"}',
      } },
      { id: 'hydrated-answer', role: 'assistant', content: 'The task is ready.', meta: {
        results: [{ result_id: 'result-81', producer_kind: 'workflow', producer_id: 'run-15', source_id: 'event-8',
          status: 'completed', title: 'Review task', summary: 'One task needs attention.',
          record: { kind: 'task', id: 'task-6' } }],
        citations: [{ id: 'source-3', label: 'Project notes', preview: 'A source excerpt' }],
      } },
    ]
    const [tool, approval, answer] = adaptConversationMessages(messages)
    expect(tool.segments[0]).toMatchObject({ kind: 'tool', id: 'call-91', producerId: 'gideon',
      producerKind: 'agent', sourceId: 'search-7', status: 'rejected', lifecycle: 'failed', output: 'Access denied' })
    expect(approval.segments[0]).toMatchObject({ kind: 'approval', id: 'approval-14', producerId: 'task.delete',
      sourceId: 'task-6', status: 'waiting_approval', input: '{"task":"task-6"}' })
    expect(answer.segments).toContainEqual(expect.objectContaining({ kind: 'result', id: 'result-81',
      producerId: 'run-15', producerKind: 'workflow', sourceId: 'event-8', status: 'completed',
      record: { kind: 'task', id: 'task-6' } }))
    expect(answer.segments).toContainEqual(expect.objectContaining({ kind: 'citation', id: 'source-3', label: 'Project notes' }))
    expect(resultRecordRoute({ kind: 'task', id: 'task-6' })).toMatchObject({ destination: 'activity', view: 'detail',
      placement: { id: 'tasks' }, record: { kind: 'task', id: 'task-6' } })
  })

  it('keeps unknown producers and broken or untyped targets visible without fabricating a link', () => {
    const [unknown] = adaptConversationMessages([{ id: 'unknown-result', role: 'assistant', content: '', meta: {
      results: [{ result_id: 'result-unknown', producer_kind: 'external-runner', producer_id: 'runner-5',
        source_id: 'source-2', status: 'unknown', title: 'External output',
        record: { kind: 'unregistered', id: 'private-4' } }],
    } }])
    expect(unknown.segments[0]).toMatchObject({ kind: 'result', id: 'result-unknown', producerKind: 'external-runner',
      producerId: 'runner-5', sourceId: 'source-2', status: 'unknown', title: 'External output' })
    expect(unknown.segments[0]).not.toHaveProperty('record')
    const [stale] = adaptConversationMessages([{ id: 'stale-result', role: 'assistant', content: '', meta: {
      result: { id: 'stale', title: 'Removed task', record_kind: 'task', record_id: 'task-removed', status: 'stale' },
    } }])
    expect(stale.segments[0]).toMatchObject({ kind: 'result', status: 'stale', record: { kind: 'task', id: 'task-removed' } })
  })

  it('does not infer a successful tool result when producer status is absent', () => {
    const [turn] = adaptConversationMessages([{ id: 'uncertain', role: 'tool', content: 'Read file', meta: { tool_call_id: 'call-x' } }])
    expect(turn.segments[0]).toMatchObject({ kind: 'tool', id: 'call-x', lifecycle: 'unknown' })
  })
})
