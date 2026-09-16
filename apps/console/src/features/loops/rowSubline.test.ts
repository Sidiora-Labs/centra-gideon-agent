import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { hasDistinctName } from './loopPhases'


describe('hasDistinctName — does the goal line earn its space', () => {
  it('is false when the name is the top of the goal (the auto-named case)', () => {
    expect(hasDistinctName(
      'zz45 Design a dispatcher-console design system for our bus-r',
      'zz45 Design a dispatcher-console design system for our bus-routing tool: high-contrast tokens',
    )).toBe(false)
  })

  it('is true for a loop named by hand', () => {
    expect(hasDistinctName('RAIDZ2 vs dRAID homelab report', 'Compare RAIDZ2 and dRAID for a 8-bay homelab')).toBe(true)
    expect(hasDistinctName('Morning bus tier options analysis', 'Work out whether staggering the morning tiers')).toBe(true)
  })

  it('ignores a trailing ellipsis the backend added', () => {
    expect(hasDistinctName('Design a dispatcher console…', 'Design a dispatcher console for the depot')).toBe(false)
  })

  it('is case- and trailing-space-insensitive', () => {
    expect(hasDistinctName('  design a THING  ', 'Design a thing that works')).toBe(false)
  })

  it('is false for no name at all, because the title then IS the goal', () => {
    expect(hasDistinctName('', 'Ship the thing')).toBe(false)
    expect(hasDistinctName('   ', 'Ship the thing')).toBe(false)
  })

  it('a name that merely SHARES words is still distinct', () => {
    expect(hasDistinctName('Bus tier report', 'Analyse the bus tiers and write it up')).toBe(true)
  })
})

describe('the row uses the rule, and keeps its geometry', () => {
  const src = readFileSync(join(process.cwd(), "src/features/loops/LoopsListPage.tsx"), 'utf8')
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('renders the second line only when it has something to say', () => {
    expect(code).toMatch(/\{\(latestText \|\| goalEarnsItsLine\) && \(/)
    expect(code, 'the goal is still the fallback when the loop has its own name')
      .toMatch(/latestText \? <span className="text-on-surface-var">↳ \{latestText\}<\/span> : c\.goal/)
  })

  it('pins the text block height so a shorter row does not shift the list', () => {
    expect(code).toMatch(/min-h-\[2\.875rem\]/)
  })

  it('stops hand-slicing the title, so CSS truncates it with an ellipsis', () => {
    expect(code, 'the title is the name, else the whole goal').toMatch(/const title = c\.name \|\| c\.goal/)
    expect(code, 'no JS slice of the goal remains').not.toMatch(/c\.goal\.slice\(0, 70\)/)
  })

  it('the row hit target is named from the same title, bounded by the shared helper', () => {
    expect(code).toMatch(/<RowHitTarget label=\{rowSubject\(\[title\]\)\} \/>/)
  })
})
