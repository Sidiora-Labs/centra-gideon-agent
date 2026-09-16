import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const notified: string[] = []

function mockNotify() {
  notified.length = 0
  vi.doMock('./appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (m: string) => { notified.push(m) },
  }))
}

function setClipboard(v: unknown) {
  Object.defineProperty(navigator, 'clipboard', { value: v, configurable: true, writable: true })
}
const realClipboard = (navigator as { clipboard?: unknown }).clipboard

beforeEach(() => { vi.resetModules() })
afterEach(() => { setClipboard(realClipboard) })

describe('🔑 the mechanism: the optional chain took the handlers with it', () => {
  it('proves `clipboard?.writeText(x).then(a).catch(b)` runs NEITHER a nor b', () => {
    setClipboard(undefined)
    let thenRan = false
    let catchRan = false
    const nav = navigator as { clipboard?: { writeText(s: string): Promise<void> } }
    const result = nav.clipboard?.writeText('x').then(() => { thenRan = true }).catch(() => { catchRan = true })
    expect(result, 'the whole chain collapses to undefined').toBeUndefined()
    expect(thenRan, 'so no "Copied" is ever shown').toBe(false)
    expect(catchRan, 'and there is nothing to catch — this is the silence').toBe(false)
  })
})

describe('copyText says whether the copy landed', () => {
  it('returns false and REPORTS when there is no clipboard API', async () => {
    mockNotify()
    setClipboard(undefined)
    const { copyText } = await import('./clipboard')
    expect(await copyText('secret-code', 'the pairing code')).toBe(false)
    expect(notified).toHaveLength(1)
    expect(notified[0], 'names what failed').toContain('the pairing code')
    expect(notified[0], 'and offers the manual way out').toMatch(/copy it manually/i)
    expect(notified[0], 'and names the cause a LAN user can act on').toMatch(/https:\/\/ or localhost/)
  })

  it('returns false and REPORTS when the write is refused', async () => {
    mockNotify()
    setClipboard({ writeText: () => Promise.reject(new Error('Document is not focused')) })
    const { copyText } = await import('./clipboard')
    expect(await copyText('x', 'the code')).toBe(false)
    expect(notified[0], "carries the browser's own reason").toContain('Document is not focused')
  })

  it('returns true and actually writes when it works', async () => {
    mockNotify()
    const written: string[] = []
    setClipboard({ writeText: (s: string) => { written.push(s); return Promise.resolve() } })
    const { copyText } = await import('./clipboard')
    expect(await copyText('hello', 'the code')).toBe(true)
    expect(written, 'the value reaches the clipboard').toEqual(['hello'])
    expect(notified, 'and nothing is said on success').toHaveLength(0)
  })

  it('borrows the app’s one failure sentence rather than inventing a second', async () => {
    mockNotify()
    setClipboard(undefined)
    const { copyText } = await import('./clipboard')
    await copyText('x', 'the link')
    expect(notified[0]).toMatch(/^Couldn't copy the link: /)
  })
})

describe('THE RATCHET: no surface writes to the clipboard directly', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx?$/.test(n) ? [p] : []
  })
  const strip = (s: string) => s
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '))
    .replace(/^(\s*)\/\/.*$/gm, '$1')

  const sites = walk(SRC).flatMap((abs) => {
    const rel = abs.slice(SRC.length + 1)
    if (/\.(test|doc)\.tsx?$/.test(rel)) return []
    const lines = strip(readFileSync(abs, 'utf8')).split('\n')
    return lines.flatMap((ln, i) => (/navigator\.clipboard/.test(ln) ? [`${rel}:${i + 1}`] : []))
  })

  it('the ONLY module touching navigator.clipboard is app/clipboard.ts', () => {
    const strays = sites.filter((s) => !s.startsWith('app/shell/clipboard.ts:'))
    expect(strays, `these bypass copyText:\n${strays.join('\n')}`).toEqual([])
  })

  it('VACUITY: the helper is really adopted, not adopted-by-deletion', () => {
    const importers = walk(SRC).filter((abs) => !/\.(test|doc)\.tsx?$/.test(abs))
      .filter((abs) => /from '[^']*app\/shell\/clipboard'/.test(strip(readFileSync(abs, 'utf8'))))
    expect(importers.length, 'files importing copyText').toBeGreaterThanOrEqual(8)
    expect(sites.length, 'app/shell/clipboard.ts still owns exactly one write').toBe(1)
  })
})

describe('the site that claimed success now gates on the result', () => {
  const code = readFileSync(join(process.cwd(), "src/features/ChatPage.tsx"), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('copyLink only sets "Copied" when the write landed', () => {
    const body = code.match(/async function copyLink\(\)[\s\S]*?\n  \}/)?.[0] ?? ''
    expect(body, 'found copyLink').not.toBe('')
    expect(body, 'the result is checked').toMatch(/if \(!\(await copyText\([\s\S]*?\)\)\) return/)
    expect(body, 'and the old swallow is gone').not.toMatch(/catch \{/)
    expect(body.indexOf('return'), 'the guard comes first').toBeLessThan(body.indexOf('setLinkCopied(true)'))
  })
})
