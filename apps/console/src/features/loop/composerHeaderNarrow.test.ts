import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const SRC = readFileSync(join(process.cwd(), "src/features/loop/LoopComposer.tsx"), 'utf8')

const HEADER = SRC.slice(SRC.indexOf('const headerControls ='), SRC.indexOf('return (', SRC.indexOf('const headerControls =')))

const segmentedTags = () => {
  const out: string[] = []
  for (const m of HEADER.matchAll(/<Segmented\b/g)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < HEADER.length; i++) {
      const ch = HEADER[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (ch === '>' && depth === 0) { out.push(HEADER.slice(m.index!, i + 1)); break }
    }
  }
  return out
}

describe('every dial in the composer header can collapse', () => {
  const tags = segmentedTags()

  it('finds the header dials (not vacuously green)', () => {
    expect(tags.length, 'the matcher must find the Segmented dials').toBeGreaterThanOrEqual(3)
  })

  it('gives each one a collapse strategy', () => {
    const mute = tags.filter((t) => !/collapse=/.test(t)).map((t) => /ariaLabel="([^"]+)"/.exec(t)?.[1] ?? t.slice(0, 40))
    expect(
      mute,
      `dial(s) with no collapse strategy — they cannot shrink and push the row under the shell corner: ${mute.join(', ')}`,
    ).toEqual([])
  })

  it.each(['Granularity', 'Mode', 'Project kind'])('%s collapses to a menu', (label) => {
    const tag = tags.find((t) => t.includes(`ariaLabel="${label}"`))
    expect(tag, `${label} dial must exist`).toBeTruthy()
    expect(tag!).toMatch(/collapse="menu"/)
  })
})

describe('the scratch toggle keeps ONE name at every width', () => {
  it('names itself explicitly rather than relying on visible text that hides', () => {
    expect(SRC).toMatch(/aria-label="Scratch \(auto-clean when done\)"/)
  })

  it('hides only the QUALIFIER, and only below lg', () => {
    expect(SRC).toMatch(/Scratch<span className="hidden lg:inline"> \(auto-clean when done\)<\/span>/)
  })

  it('reads the real file (not vacuously green)', () => {
    expect(SRC).toMatch(/const headerControls =/)
    expect(SRC.length).toBeGreaterThan(4000)
  })
})
