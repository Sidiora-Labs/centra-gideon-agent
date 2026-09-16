import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { ChipInput } from '../ui/forms'


const SRC = join(process.cwd(), "src")

const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

const strip = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

interface IconButton { line: number; tag: string; body: string; named: boolean; titled: boolean }

function iconOnlyButtons(src: string): IconButton[] {
  const out: IconButton[] = []
  const re = /<button\b/g
  let m: RegExpExecArray | null
  while ((m = re.exec(src)) !== null) {
    let depth = 0
    let end = -1
    for (let i = m.index + 1; i < src.length; i++) {
      const ch = src[i]
      if (ch === '{') depth++
      else if (ch === '}') depth--
      else if (depth === 0 && ch === '>') { end = i + 1; break }
    }
    if (end === -1) continue
    const close = src.indexOf('</button>', end)
    if (close === -1) continue
    const tag = src.slice(m.index, end)
    const body = src.slice(end, close).trim()
    if (!/^<[A-Z]\w*[^>]*\/>$/.test(body)) continue
    out.push({
      line: src.slice(0, m.index).split('\n').length,
      tag,
      body,
      named: /aria-label|aria-labelledby/.test(tag),
      titled: /\btitle=/.test(tag),
    })
  }
  return out
}

describe('the ChipInput remove button names the chip it removes', () => {
  it('two chips get two DIFFERENT names', () => {
    const { container } = render(<ChipInput values={['alpha', 'beta']} onChange={() => {}} />)
    const names = [...container.querySelectorAll('button')].map((b) => b.getAttribute('aria-label'))
    expect(names).toEqual(['Remove alpha', 'Remove beta'])
    expect(new Set(names).size).toBe(names.length)
  })

  it('a chip with no remove name would be announced as bare "button"', () => {
    expect(strip(readFileSync(join(SRC, 'shared/ui/forms.tsx'), 'utf8')))
      .toMatch(/aria-label=\{`Remove \$\{v\}`\}/)
  })
})

describe('the two singleton icon buttons', () => {
  it('the intent editor close button is named', () => {
    expect(strip(readFileSync(join(SRC, 'features/knowledge/KnowledgeListPage.tsx'), 'utf8')))
      .toMatch(/aria-label="Close the intent editor"/)
  })

  it('the task comment send button is named', () => {
    expect(strip(readFileSync(join(SRC, 'features/tasks/TaskDetail.tsx'), 'utf8')))
      .toMatch(/aria-label="Post comment"/)
  })
})

describe('the rail: no icon-only button ships without a name', () => {
  const scanned = walk(SRC).map((abs) => ({
    rel: abs.slice(SRC.length + 1),
    buttons: iconOnlyButtons(strip(readFileSync(abs, 'utf8'))),
  }))

  it('every icon-only button has an aria-label or a title', () => {
    const offenders = scanned.flatMap(({ rel, buttons }) =>
      buttons.filter((b) => !b.named && !b.titled)
        .map((b) => `${rel}:${b.line}  ${b.body}`))
    expect(
      offenders,
      `An icon-only button with neither aria-label nor title is announced as just "button":\n  ` +
        offenders.join('\n  '),
    ).toEqual([])
  })

  it('the rail is not vacuously green — it finds the buttons it guards', () => {
    const all = scanned.flatMap((s) => s.buttons)
    expect(all.length, 'the scanner must find the tree\'s icon-only buttons').toBeGreaterThan(40)

    const named = (rel: string, needle: RegExp) =>
      scanned.find((s) => s.rel === rel)?.buttons.some((b) => b.named && needle.test(b.tag)) ?? false
    expect(named('shared/ui/forms.tsx', /Remove \$\{v\}/)).toBe(true)
    expect(named('features/knowledge/KnowledgeListPage.tsx', /Close the intent editor/)).toBe(true)
    expect(named('features/tasks/TaskDetail.tsx', /Post comment/)).toBe(true)

    const sample = `<button type="button" onClick={() => f()}><X size={12} /></button>`
    const s2 = iconOnlyButtons(sample)
    expect(s2.length).toBe(1)
    expect(s2[0].named).toBe(false)
    expect(s2[0].titled).toBe(false)
  })

  it('titled-but-unlabelled buttons are a known, counted population', () => {
    const titledOnly = scanned.flatMap(({ rel, buttons }) =>
      buttons.filter((b) => !b.named && b.titled).map((b) => `${rel}:${b.line}`))
    expect(titledOnly.length, `title-only icon buttons:\n  ${titledOnly.join('\n  ')}`).toBe(9)
  })
})
