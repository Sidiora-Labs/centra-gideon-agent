import { describe, it, expect } from 'vitest'
import { scheduleToTrigger } from './triggerMeta'
import { statusMeta } from '../schedule/scheduleMeta'
import type { ScheduleJob } from '../../shared/data/api'


const job = (over: Partial<ScheduleJob>): ScheduleJob => ({
  id: 'schedule:test', name: 'test', enabled: true, schedule: 'At 08:00 AM',
  ...(over as object),
} as ScheduleJob)

describe('a trigger that never ran claims no outcome', () => {
  it('does not inherit job HEALTH as a run outcome when nothing ran', () => {
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: null, run_count: 0 } as never))
    expect(t.lastStatus, "job health is not a run outcome").toBeNull()
    expect(statusMeta(t.lastStatus).label).toBe('never run')
    expect(statusMeta(t.lastStatus).tone).toBe('var(--color-on-surface-low)')
  })

  it('still uses job health once a run HAS happened', () => {
    const t = scheduleToTrigger(job({ last_status: 'ok', last_run_ts: 1786000000, run_count: 3 } as never))
    expect(t.lastStatus).toBe('ok')
    expect(statusMeta(t.lastStatus).tone).toBe('var(--color-ok)')
  })

  it('always prefers the per-run status over job health', () => {
    const t = scheduleToTrigger(job({ last_run_status: 'failed', last_status: 'ok', last_run_ts: 1786000000 } as never))
    expect(t.lastStatus, 'the run record wins — that is the T7 rule this extends').toBe('failed')
    expect(statusMeta(t.lastStatus).tone).toBe('var(--color-danger)')
  })

  it('reports a per-run status even with no timestamp', () => {
    const t = scheduleToTrigger(job({ last_run_status: 'skipped_overlap', last_status: 'degraded', last_run_ts: null } as never))
    expect(t.lastStatus).toBe('skipped_overlap')
  })

  it('stays null when the backend offers nothing at all', () => {
    const t = scheduleToTrigger(job({ last_status: null, last_run_status: null, last_run_ts: null } as never))
    expect(t.lastStatus).toBeNull()
    expect(statusMeta(t.lastStatus).label).toBe('never run')
  })

  it('an error health on a never-run job is also withheld', () => {
    const t = scheduleToTrigger(job({ last_status: 'error', last_run_ts: null } as never))
    expect(t.lastStatus).toBeNull()
  })
})
