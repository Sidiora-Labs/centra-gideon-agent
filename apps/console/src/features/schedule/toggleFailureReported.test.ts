import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

describe('the schedule enable/disable toggle reports failure', () => {
  it('toggle() catches and routes through setErr', () => {
    const src = readFileSync(join(process.cwd(), "src/features/schedule/ScheduleDetail.tsx"), 'utf8')
    const at = src.indexOf('async function toggle()')
    expect(at, 'the toggle must exist').toBeGreaterThan(-1)
    const fn = src.slice(at, src.indexOf('async function', at + 10))
    expect(fn, 'a failed enable/disable must reach setErr').toMatch(/catch\s*\(e\)\s*\{\s*setErr\(/)
    expect(fn, 'and still call the API').toContain('api.enableSchedule(')
  })
})
