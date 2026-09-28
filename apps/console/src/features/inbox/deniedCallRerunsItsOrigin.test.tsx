import { expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import type { InboxItem, Trigger, WorkflowRunDetailData } from '../../shared/data/api'
import { DeniedCallRerun, matchesTriggerReference, isRetryableWorkflowStep } from './DeniedCallRerun'

const trigger: Trigger = { kind: 'store', id: 'store:trigger-1', raw_id: 'trigger-1', name: 'Research', enabled: true, action: { provider: 'run-workflow', config: { workflow: 'research' } } }
const run: WorkflowRunDetailData = { run_id: 'run-1', workflow: 'research', status: 'running', spec_version: 1, nodes: [{ node_id: 'extract', instance_path: 'root.children[0]', state: 'running' }] }
const note: InboxItem = { id: 'note-1', channel: 'system', channel_name: 'System', message: 'Call blocked', sender_id: '', sender_name: '', classification: 'fyi', confidence: 'user', status: 'pending', item_kind: 'agent_request', refs: { auto_denied: true, chat: 'workflow:run-1:extract', run: 'run-1', node: 'extract' } }
const onChanged = () => document.dispatchEvent(new Event('attention-changed'))
const navigate = (path: string) => { window.location.hash = path }

it('matches the recorded trigger kind and ID without selecting a neighboring origin', () => {
  const event: Trigger = { ...trigger, kind: 'event', id: 'event:trigger-1' }
  expect(matchesTriggerReference('trigger-1', [trigger])?.rawId).toBe('trigger-1')
  expect(matchesTriggerReference('store:trigger-1', [event, trigger])?.kind).toBe('store')
  expect(matchesTriggerReference('event:trigger-1', [trigger, event])?.kind).toBe('event')
  expect(matchesTriggerReference('store:other', [trigger])).toBeNull()
  expect(matchesTriggerReference('event:trigger-1', [trigger])).toBeNull()
})

it('offers only the recorded step of a currently active workflow for rewind', () => {
  for (const status of ['running', 'paused', 'needs_input'] as const) expect(isRetryableWorkflowStep({ ...run, status }, 'extract')).toBe(true)
  expect(isRetryableWorkflowStep(run, 'publish')).toBe(false)
  expect(isRetryableWorkflowStep(run, '')).toBe(false)
  expect(isRetryableWorkflowStep(null, 'extract')).toBe(false)
  for (const status of ['complete', 'failed', 'cancelled'] as const) expect(isRetryableWorkflowStep({ ...run, status }, 'extract')).toBe(false)
})

it('does not present a pseudo-session as an available chat and preserves the exact run link', () => {
  const html = renderToStaticMarkup(<DeniedCallRerun item={note} onChanged={onChanged} navigate={navigate} />)
  expect(html).toContain('The exact chat session is unavailable')
  expect(html).not.toContain('Retry the exact call in this chat')
  expect(html).toContain('Open the recorded workflow run')
  const closed = renderToStaticMarkup(<DeniedCallRerun item={{ ...note, status: 'handled' }} onChanged={onChanged} navigate={navigate} />)
  expect(closed).not.toContain('Run this workflow step again')
  expect(closed).not.toContain('Retry the exact call in this chat')
})
