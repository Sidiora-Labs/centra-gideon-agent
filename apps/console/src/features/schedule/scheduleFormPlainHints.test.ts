import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


describe('schedule form hints', () => {
  it('no hint prints a config key at the user', () => {
    const src = readFileSync(join(process.cwd(), "src/features/schedule/ScheduleForm.tsx"), 'utf8')
    const hints = [...src.matchAll(/hint="([^"]*)"/g)].map((m) => m[1])
    expect(hints.length, 'the scan must find the hints').toBeGreaterThanOrEqual(2)
    for (const h of hints) {
      expect(h, `a hint must not carry a config key: "${h}"`).not.toMatch(/\w+_\w+=|approval_mode/)
    }
    expect(src).toContain('hint="Run tools without asking for approval each time."')
  })
})
