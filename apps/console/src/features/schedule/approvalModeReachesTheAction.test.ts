
import { describe, expect, it } from 'vitest'
import { draftToPayload, emptyDraft } from './ScheduleForm'

describe('approval_mode rides only the mode that can carry it', () => {
  it('is sent in agent mode, where the wire builds an invoke-agent action', () => {
    const body = draftToPayload({ ...emptyDraft(), mode: 'agent', approval_mode: 'auto' })
    expect(body.approval_mode).toBe('auto')
  })

  it('sends an explicit empty string when the switch is off, not nothing', () => {
    const body = draftToPayload({ ...emptyDraft(), mode: 'agent', approval_mode: '' })
    expect('approval_mode' in body).toBe(true)
    expect(body.approval_mode).toBe('')
  })

  it.each(['other', 'script', 'command'] as const)(
    'omits it in %s mode rather than sending a field the wire discards',
    (mode) => {
      const body = draftToPayload({ ...emptyDraft(), mode, approval_mode: 'auto' })
      expect('approval_mode' in body).toBe(false)
    },
  )

  it('still sends the delivery fields it is drawn next to', () => {
    const body = draftToPayload({
      ...emptyDraft(),
      mode: 'other',
      silent: true,
      strict_schedule: true,
      skip_dates: ['2027-12-25'],
      timezone: 'America/Los_Angeles',
    })
    expect(body.silent).toBe(true)
    expect(body.strict_schedule).toBe(true)
    expect(body.skip_dates).toEqual(['2027-12-25'])
    expect(body.timezone).toBe('America/Los_Angeles')
  })
})
