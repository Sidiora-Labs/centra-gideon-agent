import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { scheduleWhenMet } from './schedule/scheduleMeta'

const source = (path: string) => readFileSync(join(process.cwd(), 'src/features', path), 'utf8')

describe('five controls advertise only implemented behavior', () => {
  it('converts one-shot local datetimes to epoch seconds through one shared path', () => {
    const local = '2026-08-10T09:00'
    expect(scheduleWhenMet({ kind: 'at', at: local, cron: '', intervalValue: 1, intervalUnit: 'h' }))
      .toEqual({ at: Math.floor(new Date(local).getTime() / 1000) })
    expect(source('schedule/scheduleMeta.ts')).not.toMatch(/key: 'at'.*soon: true/)
    expect(source('schedule/ScheduleForm.tsx')).toContain('scheduleWhenMet(d)')
    expect(source('triggers/TriggerCreatePage.tsx')).toContain('scheduleWhenMet(sched)')
  })

  it('makes no terminal persistence claim without server capability evidence', () => {
    const terminal = source('terminal/TerminalPage.tsx')
    expect(terminal).toContain('setPersistAvailable(r.persist_available)')
    expect(terminal).toContain('persistAvailable === true')
    expect(terminal).toContain('persistAvailable === false')
  })

  it('uses the lifecycle restore path on mount and cancel', () => {
    const lifecycle = source('triggers/LifecycleDetail.tsx')
    expect(lifecycle).toContain('useEffect(() => restore(), [hook.id])')
    expect(lifecycle).toContain('restore(); setEditing(false)')
  })

  it('limits settings-home suppression counting to skills', () => {
    const settings = source('settings/settingsWidgets.tsx')
    expect(settings).toContain("r.producer_kind === 'skill_synthesis' && r.suppressed")
    expect(settings).toContain('other sources get a retire proposal')
  })

  it('has no model_editable producer', () => {
    const handlers = readFileSync(join(process.cwd(), '../../runtime/gideon/interfaces/dashboard/handlers/agents.py'), 'utf8')
    expect(handlers).not.toContain('model_editable')
  })
})
