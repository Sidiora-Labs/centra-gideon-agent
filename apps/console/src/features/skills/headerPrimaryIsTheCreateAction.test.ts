import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const PAGES = join(import.meta.dirname, "..")
const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.test\.tsx$/.test(n) ? [p] : []
  })

function headerControls(): Array<{ rel: string; tag: string }> {
  const out: Array<{ rel: string; tag: string }> = []
  for (const abs of walk(PAGES)) {
    const src = strip(readFileSync(abs, 'utf8'))
    for (const m of src.matchAll(/<HeaderControl\b[\s\S]{0,400}?\/>/g)) {
      out.push({ rel: abs.slice(abs.indexOf('/pages/') + 7), tag: m[0] })
    }
  }
  return out
}


function topBars(src: string): string[] {
  const out: string[] = []
  for (const m of src.matchAll(/<TopBar\b/g)) {
    let depth = 0
    for (let i = m.index! + m[0].length; i < src.length - 1; i++) {
      const c = src[i]
      if (c === '{') depth++
      else if (c === '}') depth--
      else if (c === '/' && src[i + 1] === '>' && depth === 0) { out.push(src.slice(m.index!, i + 2)); break }
    }
  }
  return out
}

const labelOf = (tag: string) => /label=(?:"([^"]+)"|\{`?([^`{}]*?)`?\})/.exec(tag)?.[1] ?? null

describe('a header control that renders primary also declares primary priority', () => {
  const controls = headerControls()

  it('the sweep found the population (vacuity floor)', () => {
    expect(controls.length, 'no HeaderControl parsed out of src/pages — the walk is wrong').toBeGreaterThan(25)
    expect(
      controls.filter((c) => /variant="primary"/.test(c.tag)).length,
      'no primary HeaderControl found, so the check below passes over nothing',
    ).toBeGreaterThan(8)
  })

  it('the TopBar slices actually contain the controls (vacuity floor)', () => {
    let inBars = 0
    for (const abs of walk(PAGES)) {
      const src = strip(readFileSync(abs, 'utf8'))
      for (const bar of topBars(src)) {
        inBars += [...bar.matchAll(/<HeaderControl\b/g)].length
      }
    }
    expect(
      inBars,
      `only ${inBars} of ${controls.length} HeaderControls were found inside a TopBar slice. The ` +
        `slicer is truncating, so every per-header check above is passing over nothing.`,
    ).toBeGreaterThan(controls.length * 0.7)
  })

  it('the two attributes never disagree', () => {
    const split = controls
      .filter((c) => /variant="primary"/.test(c.tag) && !/priority="primary"/.test(c.tag))
      .map((c) => `${c.rel}: ${labelOf(c.tag) ?? '(unlabelled)'}`)
    expect(
      split,
      'these render as the primary action but do not declare primary priority, so a narrow header ' +
        'may fold away the coral control and keep a secondary one:\n  ' + split.join('\n  '),
    ).toEqual([])
  })

  it('no single header renders the same label twice', () => {
    const dupes: string[] = []
    for (const abs of walk(PAGES)) {
      const src = strip(readFileSync(abs, 'utf8'))
      const rel = abs.slice(abs.indexOf('/pages/') + 7)
      for (const [i, bar] of topBars(src).entries()) {
        const labels = [...bar.matchAll(/<HeaderControl\b[\s\S]{0,400}?\/>/g)]
          .map((m) => labelOf(m[0]))
          .filter((l): l is string => !!l)
        const segLabels = [...bar.matchAll(/\bkey:\s*'[^']+',\s*label:\s*'([^']+)'/g)].map((m) => m[1])
        const seen = new Set<string>()
        for (const l of labels) {
          if (seen.has(l)) dupes.push(`${rel} TopBar#${i + 1}: "${l}" twice as a HeaderControl`)
          seen.add(l)
          if (segLabels.includes(l)) {
            dupes.push(`${rel} TopBar#${i + 1}: "${l}" is both a HeaderControl and a Segmented option`)
          }
        }
      }
    }
    expect(
      dupes,
      'one label, two controls in the SAME header — a user cannot predict which does what:\n  ' +
        dupes.join('\n  '),
    ).toEqual([])
  })

  it("skills' create action now matches the ten siblings", () => {
    const src = readFileSync(join(PAGES, 'skills/SkillsPage.tsx'), 'utf8')
    expect(src, 'New skill must carry both attributes on one control').toMatch(
      /label="New skill" variant="primary" priority="primary"/,
    )
    expect(
      strip(src),
      'the duplicate Browse HeaderControl must stay deleted — ModeToggle owns that action and also ' +
        'shows which mode you are in, which a button cannot',
    ).not.toMatch(/<HeaderControl[^>]*label="Browse"/)
  })
})
