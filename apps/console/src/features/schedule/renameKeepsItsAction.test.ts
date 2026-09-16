
import { describe, expect, it } from 'vitest'
import { deriveMode } from './scheduleMeta'
import { draftToPayload, toDraft } from './ScheduleForm'
import type { ScheduleJob } from '../../shared/data/api'

function notifyJob(overrides: Partial<ScheduleJob> = {}): ScheduleJob {
  return {
    id: 'sched-1',
    name: 'Photographer — nudge Kaur gallery delivery',
    message: '',
    enabled: true,
    schedule: 'At 10:00 on Wednesday',
    cron_expr: '0 10 * * 3',
    action: {
      provider: 'notify',
      config: { title_template: 'Kaur gallery due — $now', body_template: '', kind: 'info' },
    },
    ...overrides,
  } as ScheduleJob
}

describe('deriveMode reads the action provider', () => {
  it('reports a non-agent provider as other, not agent', () => {
    expect(deriveMode(notifyJob())).toBe('other')
  })

  it.each([
    ['invoke-agent', 'agent'],
    ['run-script', 'script'],
    ['bash', 'command'],
  ])('maps %s to %s', (provider, mode) => {
    expect(deriveMode(notifyJob({ action: { provider, config: {} } }))).toBe(mode)
  })

  it('treats a row with no action at all as a legacy agent row', () => {
    const legacy = notifyJob()
    delete (legacy as { action?: unknown }).action
    expect(deriveMode(legacy)).toBe('agent')
  })

  it('still honours the legacy script/command fields first', () => {
    expect(deriveMode(notifyJob({ script: 'jobs/x.py:run' }))).toBe('script')
    expect(deriveMode(notifyJob({ command: 'echo hi' }))).toBe('command')
  })
})

describe('the edit payload describes no action it cannot edit', () => {
  it('omits message for a notify trigger, so nothing fabricates an action from it', () => {
    const draft = toDraft(notifyJob())
    expect(draft.mode).toBe('other')

    const body = draftToPayload({ ...draft, name: 'Photographer — nudge Kaur (renamed)' })

    expect('message' in body).toBe(false)
    expect('agent' in body).toBe(false)
    expect('model' in body).toBe(false)
    expect(body.name).toBe('Photographer — nudge Kaur (renamed)')
    expect(body.cron).toBe('0 10 * * 3')
  })

  it('still sends message and agent fields in agent mode', () => {
    const draft = toDraft(notifyJob({ action: { provider: 'invoke-agent', config: {} } }))
    expect(draft.mode).toBe('agent')

    const body = draftToPayload({ ...draft, message: 'Summarize my unread mail' })

    expect(body.message).toBe('Summarize my unread mail')
    expect('agent' in body).toBe(true)
  })

  it('keeps an EMPTY agent prompt as an agent edit', () => {
    const draft = toDraft(notifyJob({ action: { provider: 'invoke-agent', config: {} } }))
    const body = draftToPayload({ ...draft, message: '' })
    expect('message' in body).toBe(true)
    expect(body.message).toBe('')
  })
})
