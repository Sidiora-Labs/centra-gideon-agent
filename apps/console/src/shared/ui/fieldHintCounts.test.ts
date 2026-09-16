import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(import.meta.dirname, "../..")

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const abs = join(dir, entry.name)
    if (entry.isDirectory()) sourceFiles(abs, out)
    else if (entry.name.endsWith('.tsx') && !entry.name.includes('.test.')) out.push(abs)
  }
  return out
}

function sliceTags(src: string, name: string): string[] {
  const open = new RegExp(`<${name}\\b(?![A-Za-z])`, 'g')
  const out: string[] = []
  for (const m of src.matchAll(open)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < src.length; i++) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '>' && depth === 0) { out.push(src.slice(m.index! + m[0].length, i)); break }
    }
  }
  return out
}

const propsOf = (name: string, files: string[]): string[] =>
  files.flatMap((abs) => sliceTags(readFileSync(abs, 'utf8'), name))

const files = sourceFiles(SRC)
const hinted = (name: string) => propsOf(name, files).filter((p) => /\bhint=/.test(p)).length

const PUBLISHERS = [
  { name: 'Field', measured: 120, floor: 100 },
  { name: 'Row', measured: 77, floor: 65 },
  { name: 'NumberRow', measured: 39, floor: 30 },
] as const

const FORWARDERS = [
  { name: 'ToggleRow', measured: 25, floor: 20 },
  { name: 'EnumRow', measured: 3, floor: 2 },
  { name: 'CheckList', measured: 3, floor: 2 },
  { name: 'TextRow', measured: 2, floor: 1 },
  { name: 'StrListField', measured: 2, floor: 1 },
] as const

describe('the scan slices a tag at ITS OWN closing angle bracket', () => {
  const slice = sliceTags

  it('an arrow function in the props does not end the tag', () => {
    const src = '<Row onChange={(v) => set(v)} hint="a sentence">x</Row>'
    expect(slice(src, 'Row')).toHaveLength(1)
    expect(slice(src, 'Row')[0], 'the hint must be inside the slice').toMatch(/hint="a sentence"/)
  })

  it('a comparison in the props does not end the tag', () => {
    const src = '<Row label={n > 3 ? "many" : "few"} hint="counts">x</Row>'
    expect(slice(src, 'Row')[0]).toMatch(/hint="counts"/)
  })

  it('a hint AFTER a brace-nested angle bracket is still counted', () => {
    const src = '<Field right={<Tag on={a > b} />} hint="after the nesting">y</Field>'
    expect(slice(src, 'Field')[0]).toMatch(/hint="after the nesting"/)
  })

  it('the name match is exact — Row must not swallow RowGroup', () => {
    const src = '<RowGroup><Row hint="inner">z</Row></RowGroup>'
    expect(slice(src, 'Row'), 'RowGroup is a different component').toHaveLength(1)
  })
})

describe('the hint contract covers as many publishers as its docstring claims', () => {
  it('the scan reads a real tree (vacuity floor)', () => {
    expect(files.length, 'the .tsx sweep found nothing — the scan root is wrong').toBeGreaterThan(200)
  })

  it('every publisher still carries its population', () => {
    const low: string[] = []
    for (const { name, measured, floor } of PUBLISHERS) {
      const n = hinted(name)
      if (n < floor) low.push(`${name}: ${n} hinted call sites, floor ${floor} (was ${measured} on 2026-08-28)`)
    }
    expect(
      low,
      `a hint publisher lost coverage, or the depth-tracking scan stopped matching its JSX:\n  ${low.join('\n  ')}`,
    ).toEqual([])
  })

  it('every forwarding wrapper still forwards', () => {
    const low: string[] = []
    for (const { name, measured, floor } of FORWARDERS) {
      const n = hinted(name)
      if (n < floor) low.push(`${name}: ${n}, floor ${floor} (was ${measured})`)
    }
    expect(low, `a wrapper stopped forwarding a hint:\n  ${low.join('\n  ')}`).toEqual([])
  })

  it("forms.tsx's docstring states a total in the right neighbourhood, and dates it", () => {
    const doc = readFileSync(join(SRC, 'shared/ui/forms.tsx'), 'utf8')
    const claimed = Number(doc.match(/\*\*(\d+)\*\* hinted publishers render today/)?.[1])
    expect(claimed, 'forms.tsx no longer states a publisher total').toBeGreaterThan(0)
    const actual = [...PUBLISHERS, ...FORWARDERS].reduce((sum, p) => sum + hinted(p.name), 0)
    expect(
      Math.abs(claimed - actual) / actual,
      `forms.tsx claims ${claimed} hinted publishers; the scan counts ${actual}. Re-derive the ` +
        `docstring's numbers from this test's per-name counts and re-date the line.`,
    ).toBeLessThan(0.05)
    expect(doc, 'the count must be dated, so a reader knows its vintage').toMatch(/Recounted \*\*20\d\d-\d\d-\d\d\*\*/)
  })

  it("settingsUI's Row comment agrees with the scan, which is where it drifted 10% low", () => {
    const ui = readFileSync(join(SRC, 'features/settings/settingsUI.tsx'), 'utf8')
    const claimed = Number(ui.match(/\((\d+) hinted rows/)?.[1])
    expect(claimed, "settingsUI's Row comment no longer states a count").toBeGreaterThan(0)
    expect(
      Math.abs(claimed - hinted('Row')) / hinted('Row'),
      `settingsUI says ${claimed} hinted rows; the scan counts ${hinted('Row')}.`,
    ).toBeLessThan(0.05)
  })
})
