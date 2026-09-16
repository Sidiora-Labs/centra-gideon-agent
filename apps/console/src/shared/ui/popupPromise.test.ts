import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'


const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) && !/\.(test|doc)\.tsx?$/.test(n) ? [p] : []
  })
const files = () => walk(SRC).map((abs) => ({ file: abs.slice(SRC.length + 1), src: readFileSync(abs, 'utf8') }))

describe('an aria-haspopup trigger delivers what it promises', () => {
  const EXEMPT = new Set(['shared/ui/composer/MarkdownInput.tsx'])

  const declarers = () => files()
    .map((f) => ({ ...f, kinds: [...f.src.matchAll(/aria-haspopup=["{]*['"]?(menu|listbox)['"]?/g)].map((m) => m[1]) }))
    .filter((f) => f.kinds.length && !EXEMPT.has(f.file))

  it('finds the population (not vacuously green)', () => {
    expect(declarers().length, 'the aria-haspopup census must not go empty').toBeGreaterThanOrEqual(3)
  })

  it('each declared popup type is actually rendered in the same file', () => {
    const broken: string[] = []
    for (const { file, src, kinds } of declarers()) {
      for (const kind of new Set(kinds)) {
        if (!new RegExp(`role="${kind}"`).test(src)) broken.push(`${file} promises ${kind} and renders none`)
      }
    }
    expect(broken, `a trigger promises a popup it does not deliver:\n${broken.join('\n')}`).toEqual([])
  })

  it('the two corrected triggers claim only a disclosure', () => {
    for (const rel of ['shared/ui/WidthPill.tsx', 'shared/ui/NotificationBell.tsx']) {
      const code = readFileSync(join(SRC, rel), 'utf8').replace(/\/\/.*$/gm, '')
      expect(code, `${rel} must not re-add a false menu promise`).not.toMatch(/aria-haspopup/)
      expect(code, `${rel} still reports open/closed`).toMatch(/aria-expanded=\{open\}/)
    }
  })
})

describe('a disclosure is never hover-only', () => {
  it('anything that opens on hover also opens on click', () => {
    const offenders: string[] = []
    for (const { file, src } of files()) {
      const opensOnHover = /onMouseEnter=\{\(\) => set(Open|Shown|Visible)\(true\)\}/.test(src)
      if (!opensOnHover) continue
      const opensOnClick = /onClick=\{\(\) => set(Open|Shown|Visible)\(\(?\w*\)? =>|onClick=\{\(\) => set(Open|Shown|Visible)\(/.test(src)
      if (!opensOnClick) offenders.push(file)
    }
    expect(offenders, `hover-only disclosure — unreachable without a pointer:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the width pill closes on Escape and returns focus to its trigger', () => {
    const src = readFileSync(join(SRC, 'shared/ui/WidthPill.tsx'), 'utf8')
    expect(src).toMatch(/e\.key === 'Escape'/)
    expect(src).toMatch(/btnRef\.current\?\.focus\(\)/)
    expect(src, 'and closes on an outside click').toMatch(/wrapRef\.current && !wrapRef\.current\.contains/)
  })
})
