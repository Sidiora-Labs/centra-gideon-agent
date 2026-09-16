import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ListRow } from './ListScaffold'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

function listRowTags(): Array<{ file: string; line: number; attrs: string }> {
  const out: Array<{ file: string; line: number; attrs: string }> = []
  for (const abs of walk(SRC)) {
    const text = readFileSync(abs, 'utf8')
    for (const m of text.matchAll(/<ListRow\b/g)) {
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
        attrs: text.slice(m.index! + m[0].length, end),
      })
    }
  }
  return out
}

describe('ListRow names the row, not its contents', () => {
  it('puts `label` on the button role', () => {
    render(<ListRow onClick={() => {}} label="Deploy pipeline"><span>lots of body text</span></ListRow>)
    const row = screen.getByRole('button', { name: 'Deploy pipeline' })
    expect(row.getAttribute('aria-label')).toBe('Deploy pipeline')
  })

  it('does not name a NON-interactive row (there is no button to name)', () => {
    const { container } = render(<ListRow label="unused"><span>static content</span></ListRow>)
    const div = container.querySelector('[aria-label]')
    expect(div, 'a row with no onClick is not a button and must not claim a name').toBeNull()
  })

  it('still exposes the row content to sighted readers', () => {
    render(<ListRow onClick={() => {}} label="Short name"><span>the visible summary</span></ListRow>)
    expect(screen.getByText('the visible summary')).toBeTruthy()
  })
})

describe('every interactive ListRow call site is named', () => {
  const tags = listRowTags()

  it('scans real call sites (not vacuously green)', () => {
    expect(tags.length, 'the matcher must find the tree\'s <ListRow> tags').toBeGreaterThan(10)
    expect(
      tags.filter((t) => /onClick/.test(t.attrs)).length,
      'and most of them are interactive',
    ).toBeGreaterThan(8)
  })

  it('has no clickable row without a label', () => {
    const unnamed = tags
      .filter((t) => /onClick/.test(t.attrs) && !/\blabel=/.test(t.attrs))
      .map((t) => `${t.file}:${t.line}`)
    expect(
      unnamed,
      'A clickable ListRow with no `label` takes its accessible name from its whole subtree —\n' +
        'measured up to 2001 characters for one inbox row. Pass the entity title:\n  ' +
        unnamed.join('\n  '),
    ).toEqual([])
  })
})
