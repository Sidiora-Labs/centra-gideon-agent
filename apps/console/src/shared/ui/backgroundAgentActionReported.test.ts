import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const widget = readFileSync(join(SRC, 'shared/ui/SystemWidget.tsx'), 'utf8')
const code = widget.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

describe('the background-agent monitor reports a failed cancel or clear', () => {
  it('parses its subject (a rail that reads nothing asserts nothing)', () => {
    expect(widget.length, 'shared/ui/SystemWidget.tsx did not read').toBeGreaterThan(2_000)
    expect(code, 'the RunningAgents monitor is still here').toMatch(/function RunningAgents\(/)
    expect(code, 'and it still calls both writes').toMatch(/api\.cancelSpawnedAgent\(/)
    expect(code).toMatch(/api\.clearSpawnedAgents\(/)
  })

  it('neither write is swallowed', () => {
    const empty = [...code.matchAll(/catch\s*(?:\([^)]*\))?\s*\{\s*\}/g)]
    expect(
      empty.length,
      'an empty catch around a write in this file tells the user nothing — the row simply comes ' +
        'back still running, which is what a click that never landed looks like',
    ).toBe(0)
    expect(code).toMatch(/reportingWrite\([\s\S]{0,80}api\.cancelSpawnedAgent/)
    expect(code).toMatch(/reportingWrite\([\s\S]{0,80}api\.clearSpawnedAgents/)
  })

  it('the cancel message names WHICH agent, from the row that already has it', () => {
    expect(code, 'the row threads its own task line into the handler').toMatch(
      /cancel\(a\.id,\s*firstLine\(a\.task\)\)/,
    )
    expect(code, 'and the handler spends it on the sentence').toMatch(
      /const cancel = async \(id: string, task: string\)/,
    )
    expect(code).toMatch(/reportingWrite\(`cancel [^`]*\$\{task\}/)
  })

  it('the refetch is gated on the write landing', () => {
    const gated = [...code.matchAll(/const ok = await reportingWrite\([\s\S]{0,140}?if \(ok\) load\(\)/g)]
    expect(
      gated.length,
      'both cancel and clear must skip the refetch on failure — re-rendering the same running row ' +
        'reads as "nothing happened, twice"',
    ).toBe(2)
  })

  it('the poll that keeps the list fresh is untouched', () => {
    expect(code, 'still polls every 4s while the card is open').toMatch(
      /window\.setInterval\(load, 4000\)/,
    )
  })
})
