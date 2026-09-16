import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

describe('the terminal persistence toggle reports failure', () => {
  it('reverts AND reports through the page error surface', () => {
    const src = readFileSync(join(process.cwd(), "src/features/terminal/TerminalPage.tsx"), 'utf8')
    const at = src.indexOf('const togglePersist')
    expect(at, 'the toggle must exist').toBeGreaterThan(-1)
    const fn = src.slice(at, at + 700)
    expect(fn, 'the optimistic flip must still revert on failure').toMatch(/setPersist\(!next\)/)
    expect(fn, 'and the failure must speak').toMatch(/setError\(`Couldn't (\$\{next \? 'enable' : 'disable'\}|enable|disable) persistent sessions/)
  })
})
