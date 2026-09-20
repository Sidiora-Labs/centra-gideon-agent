import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ACTIVE_LOOP_STATUSES, shownCycle } from './loopStatus'

const SRC = join(process.cwd(), 'src')
const walk = (dir: string): string[] => readdirSync(dir).flatMap((name) => {
  const path = join(dir, name)
  return statSync(path).isDirectory() ? walk(path) : /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
})

describe('shownCycle', () => {
  it('counts the open cycle for every active status, including paused', () => {
    for (const status of ACTIVE_LOOP_STATUSES) expect(shownCycle(3, status), status).toBe(4)
    for (const status of ['intake', 'planning', 'complete', 'failed', 'stopped']) {
      expect(shownCycle(3, status), status).toBe(3)
    }
  })

  it('allows no inline copy of the open-cycle arithmetic', () => {
    const copies = walk(SRC).filter((path) => /total_?cycles\s*\+\s*1|totalCycles\s*\+\s*1/.test(readFileSync(path, 'utf8')))
    expect(copies.map((path) => path.slice(SRC.length + 1))).toEqual([])
  })

  it('keeps incognito reads and transcript persistence truthful', () => {
    const chat = readFileSync(join(SRC, 'features/ChatPage.tsx'), 'utf8')
    expect(chat).toContain("hint: 'Do not write to memory'")
    expect(chat).toContain('This chat is still saved to your history.')
    expect(chat).not.toMatch(/no memory (?:is )?read|stays out of your history/i)
  })
})
