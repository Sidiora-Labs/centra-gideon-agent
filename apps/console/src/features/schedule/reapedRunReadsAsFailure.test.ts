import { scheduleToTrigger, triggerStatusMeta } from '../triggers/triggerMeta'
import { namedOwner } from '../../shared/testing/sourceOwners'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { lastRunMeta, statusMeta } from './scheduleMeta'


const TRIGGER_HEALTH = ['ok', 'degraded', 'parked', 'failing'] as const

describe('lastRunMeta reconciles the reaper disagreement (#685)', () => {
  it('the measured record: health=degraded over a success run row reads degraded, warn-toned', () => {
    const m = lastRunMeta('success', 'degraded')
    expect(m.label).toBe('degraded')
    expect(m.tone).toBe('var(--color-warning)')
  })

  it('every non-ok TriggerHealth member dominates a success run row — none reads ok or never-run', () => {
    for (const h of TRIGGER_HEALTH) {
      if (h === 'ok') continue
      const m = lastRunMeta('success', h)
      expect(m.label, `health=${h} must not fall through`).not.toBe('never run')
      expect(m.label, `health=${h} must not read as success`).not.toBe('ok')
      expect(m.tone, `health=${h} must not be ok-green`).not.toBe('var(--color-ok)')
    }
  })

  it('the legacy error value (pre-TriggerHealth last_status) keeps its danger shape', () => {
    const m = lastRunMeta('success', 'error')
    expect(m.label).toBe('error')
    expect(m.tone).toBe('var(--color-danger)')
  })

  it('an ok health defers to the run row exactly as before', () => {
    expect(lastRunMeta('launched', 'ok').label).toBe('launched')
    expect(lastRunMeta('ran_late', 'ok').label).toBe('ran late')
    expect(lastRunMeta('skipped_quiet_hours', 'ok').label).toBe('quiet hours')
    expect(lastRunMeta(null, null).label).toBe('never run')
    expect(lastRunMeta(null, 'ok').label).toBe(statusMeta('ok').label)
  })

  it('mirror drift: the backend enum has exactly the members this rail enumerates', () => {
    const py = readFileSync(
      join(import.meta.dirname, "../../../../../runtime/gideon/automation/triggers/models.py"),
      'utf8',
    )
    const enumBody = py.split('class TriggerHealth')[1]?.split('class ')[0] ?? ''
    const members = [...enumBody.matchAll(/^\s+[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1])
    expect(members.sort()).toEqual([...TRIGGER_HEALTH].sort())
  })
})

describe('both renderers consume the reconciler, not the bare chain', () => {
  const read = (rel: string) =>
    readFileSync(join(import.meta.dirname, "..", rel), 'utf8')

  it('the detail badge', () => {
    const s = read('schedule/ScheduleDetail.tsx')
    expect(s).toContain('lastRunMeta(job.last_run_status, job.last_status)')
    expect(s).not.toContain('statusMeta(job.last_run_status || job.last_status)')
  })

  it("the list's schedule rows", () => {
    const s = read('triggers/TriggersListPage.tsx')
    expect(s).toMatch(/import \{[^}]*\btriggerStatusMeta\b[^}]*\} from '.\/triggerMeta'/)
    expect(s).toContain('const sd = triggerStatusMeta(t)')
    expect(s).toContain('{sd.label}')
    const metaSource = read('triggers/triggerMeta.ts')
    expect(namedOwner(metaSource, 'scheduleToTrigger')).toContain('health: metadata.health ?? j.last_status ?? null')
    expect(namedOwner(metaSource, 'triggerStatusMeta')).toContain('lastRunMeta(')
    for (const [health, label, tone] of [['degraded', 'degraded', 'var(--color-warning)'], ['failing', 'failing', 'var(--color-danger)'], ['parked', 'parked', 'var(--color-info)'], ['error', 'error', 'var(--color-danger)'], ['ok', 'ok', 'var(--color-ok)']]) {
      const trigger = scheduleToTrigger({ id: 'job-1', name: 'Daily', message: '', enabled: true, schedule: 'every 1h', last_run_ts: 1, last_run_status: 'success', last_status: health, last_error: 'Observed result' })
      const actual = triggerStatusMeta(trigger)
      expect(actual.label, `health=${health} must reconcile actual schedule input`).toBe(label)
      expect(actual.tone).toBe(tone)
      expect(actual.reason).toBe('Observed result')
      expect(triggerStatusMeta({ ...trigger, state: 'quarantined' }).label).toBe('quarantined')
    }
  })
})
