import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function genericTags(): Array<{ file: string; line: number; tag: string }> {
  const out: Array<{ file: string; line: number; tag: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<(div|span|section|p)\b/g)) {
      let depth = 0
      let end = -1
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) { end = i; break }
      }
      if (end === -1) continue
      out.push({
        file: abs.slice(SRC.length + 1),
        line: text.slice(0, m.index).split('\n').length,
        tag: text.slice(m.index!, end + 1),
      })
    }
  }
  return out
}

describe('a generic element never carries a discarded name', () => {
  const tags = genericTags()

  it('scans real JSX tags (not vacuously green)', () => {
    expect(tags.length, 'the matcher must find the tree\'s generic tags').toBeGreaterThan(2000)
    expect(
      tags.some((t) => t.tag.includes('\n') && t.tag.includes('{')),
      'the matcher must span multi-line tags with braced attribute values',
    ).toBe(true)
  })

  it('has no aria-label on a role-less div/span/section/p', () => {
    const offenders = tags
      .filter((t) => /\saria-label=/.test(t.tag) && !/\srole=/.test(t.tag))
      .filter((t) => !/\saria-hidden/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      offenders,
      'ARIA prohibits naming a generic element — the browser DISCARDS these labels and axe\n' +
        'reports aria-prohibited-attr (serious). Give the element the role that matches what\n' +
        'it is: `status` for a busy/loading region, `group` for a labelled set of controls,\n' +
        '`img` for a graphic whose label is its only text.\n  ' + offenders.join('\n  '),
    ).toEqual([])
  })

  it('every aria-busy region is a status region', () => {
    const unnamed = tags
      .filter((t) => /\saria-busy=/.test(t.tag) && /\saria-label=/.test(t.tag))
      .filter((t) => !/role="status"/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      unnamed,
      'A labelled aria-busy region needs role="status" or its label is discarded:\n  ' +
        unnamed.join('\n  '),
    ).toEqual([])
  })
})
