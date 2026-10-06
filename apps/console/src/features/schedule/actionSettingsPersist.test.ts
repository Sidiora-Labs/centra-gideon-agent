import { describe, expect, it } from 'vitest'
import { draftToPayload, toDraft } from './ScheduleForm'

describe('schedule action settings', () => {
  it('shows and retains the agent reach when only cadence changes', () => {
    const draft = toDraft({ id: 'review', name: 'Review', message: 'Review files', enabled: true,
      schedule: 'every hour', every_secs: 3600,
      action: { provider: 'invoke-agent', config: { capability: 'research', may_change: ['src/**'], max_turns: 7 } },
    })
    const payload = draftToPayload({ ...draft, intervalValue: 2 })
    expect(payload).toMatchObject({ capability: 'research', may_change: ['src/**'], max_turns: 7 })
    expect(draftToPayload({ ...draft, max_turns: '' }).max_turns).toBeNull()
  })
})
