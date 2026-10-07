import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { jsxTags, literalHintInstances } from '../testing/jsxContracts'


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

const files = sourceFiles(SRC)
const hintCounts = new Map<string, number>()
const hinted = (name: string) => {
  if (!hintCounts.has(name)) hintCounts.set(name, files.reduce((sum, path) => {
    const source = readFileSync(path, 'utf8')
    if (!source.includes(`<${name}`)) return sum
    return sum + literalHintInstances(source, name)
  }, 0))
  return hintCounts.get(name)!
}

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

  it('mapped capability hints count actual literal instances, not template sites', () => {
    expect(literalHintInstances("const xs = [{hint:'one'}, {hint:'two'}, {}] as const; xs.map(x => <CheckList hint={x.hint} />)", 'CheckList')).toBe(2)
    expect(literalHintInstances("const xs = [{hint:''}, {}]; xs.map(x => <CheckList hint={x.hint} />)", 'CheckList')).toBe(0)
    const form = readFileSync(join(SRC, 'features/agents/AgentForm.tsx'), 'utf8')
    expect(literalHintInstances(form, 'CheckList')).toBe(3)
    expect(jsxTags(form, ['Field']).some(site => site.attributes.get('hint') === '{hint}')).toBe(true)
  })

  it('native publishers bind the hint identity to their real controls', () => {
    const forms = readFileSync(join(SRC, 'shared/ui/forms.tsx'), 'utf8')
    expect(forms).toMatch(/<FieldHintProvider value=\{hintId\}/)
    expect(forms).toMatch(/<p id=\{hintId\}/)
    const ui = readFileSync(join(SRC, 'features/settings/settingsUI.tsx'), 'utf8')
    expect(ui).toMatch(/<Row label=\{label\} hint=\{hint\}/)
    expect(ui).toMatch(/<Field label=\{label\} hint=\{hint\}/)
  })
})
