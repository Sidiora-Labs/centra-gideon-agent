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

function inputTags(): Array<{ file: string; line: number; tag: string }> {
  const out: Array<{ file: string; line: number; tag: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<input\b/g)) {
      let depth = 0
      for (let i = m.index! + m[0].length; i < text.length; i++) {
        const ch = text[i]
        if (ch === '{') depth++
        else if (ch === '}') depth--
        else if (ch === '>' && depth === 0) {
          out.push({
            file: abs.slice(SRC.length + 1),
            line: text.slice(0, m.index).split('\n').length,
            tag: text.slice(m.index!, i + 1),
          })
          break
        }
      }
    }
  }
  return out
}

const tags = inputTags()
const fileInputs = tags.filter((t) => /type=["']file["']/.test(t.tag))

describe('the file-input escape hatch stays an escape hatch', () => {
  it('scans real <input> tags (not vacuously green)', () => {
    expect(tags.length, 'the matcher must find the tree\'s <input> tags').toBeGreaterThan(100)
    expect(fileInputs.length, 'and the file inputs among them').toBeGreaterThanOrEqual(6)
    expect(
      tags.some((t) => t.tag.includes('\n') && t.tag.includes('=>')),
      'the matcher must span multi-line tags with arrow-function attributes',
    ).toBe(true)
  })

  it('every file input is hidden, so it needs no name', () => {
    const visible = fileInputs
      .filter((t) => !/\bhidden\b/.test(t.tag) && !/className=["'][^"']*\bhidden\b/.test(t.tag) && !/sr-only/.test(t.tag))
      .filter((t) => !/aria-label|aria-labelledby/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      visible,
      'A file input that is NOT hidden is reachable and must carry a name (or be hidden and\n' +
        'driven by a labelled button, which is the pattern the other 6 use):\n  ' + visible.join('\n  '),
    ).toEqual([])
  })
})

describe('an on-demand edit input carries its own name', () => {
  const autoFocused = tags.filter((t) => /\bautoFocus\b/.test(t.tag))

  it('finds the on-demand inputs (not vacuously green)', () => {
    expect(autoFocused.length, 'expected the inline rename/edit inputs').toBeGreaterThanOrEqual(18)
  })

  it('every autoFocus input is named', () => {
    const nameless = autoFocused
      .filter((t) => !/aria-label|aria-labelledby|\bid=|placeholder=/.test(t.tag))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      nameless,
      'An autoFocus input takes focus with no user action, so its name is the ONLY thing\n' +
        'telling a screen-reader user where they landed. These have none:\n  ' + nameless.join('\n  '),
    ).toEqual([])
  })
})

