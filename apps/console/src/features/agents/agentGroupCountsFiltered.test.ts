import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const FILE = join(import.meta.dirname, "AgentsListPage.tsx")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const src = () => strip(readFileSync(FILE, 'utf8'))

describe('agents group headers count the filtered list they sit above (#667)', () => {
  it('reads the real file (not vacuously green)', () => {
    const s = src()
    expect(s).toContain('GroupSection')
    expect(s).toContain('No matching agents')
  })

  it('the Native header counts shownNative, never the raw catalog', () => {
    const s = src()
    expect(s).toContain('count={shownNative.length}')
    expect(s).not.toContain('count={native.agents.length}')
  })

  it('a Discovered header counts its filtered items, never the raw group', () => {
    const s = src()
    expect(s).toContain('count={items.length}')
    expect(s).not.toContain('count={g.agents.length}')
  })

  it('a Discovered search miss reads as a miss, not an empty catalog', () => {
    const s = src()
    expect(s).toMatch(/n \? 'No matching agents\.' : 'No agents discovered\.'/)
  })
})
