import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const FILE = join(process.cwd(), "src/features/triggers/WeekGridView.tsx")
const src = () => readFileSync(FILE, 'utf8')

describe('week-grid empty-state copy (#686)', () => {
  it('reads the real file (not vacuously green)', () => {
    expect(src()).toContain('No fires this week')
  })

  it('does not claim cron triggers are unprojected — S103 plots them', () => {
    const s = src()
    expect(s).not.toContain('A cron-expression trigger is not projected')
    expect(s).not.toContain('Only enabled interval schedules are plotted')
  })

  it('names all projected clock kinds and only the true empty-state causes (#561)', () => {
    const s = src()
    expect(s).toContain('one-shot, interval, and cron alike')
    expect(s).toContain('A disabled trigger has no fires')
    expect(s).not.toContain('a one-shot is not projected here yet')
  })
})
