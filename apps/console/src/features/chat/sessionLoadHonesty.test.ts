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

const IMPERATIVE_EXEMPT = new Set(['features/settings/MemoryPanel.tsx'])

const callSites = walk(SRC).flatMap((abs) => {
  const src = readFileSync(abs, 'utf8')
  const rel = abs.slice(SRC.length + 1)
  return [...src.matchAll(/api\.chatSessions\([^)]*\)[\s\S]{0,60}/g)].map((m) => ({
    file: rel,
    line: src.slice(0, m.index!).split('\n').length,
    frag: m[0].replace(/\s+/g, ' '),
  }))
})

describe('every reader of the chat-session list', () => {
  it('is found by the census (not vacuously green)', () => {
    expect(callSites.length, 'the matcher must find the chatSessions() readers').toBeGreaterThanOrEqual(4)
    expect(callSites.map((c) => c.file)).toContain('features/ChatPage.tsx')
    expect(callSites.map((c) => c.file)).toContain('features/dashboard/DashboardPage.tsx')
  })

  it('never discards the rejection', () => {
    const swallowed = callSites
      .filter((c) => /\.catch\(/.test(c.frag))
      .filter((c) => !IMPERATIVE_EXEMPT.has(c.file))
    expect(
      swallowed.map((c) => `${c.file}:${c.line}`),
      'a swallowed rejection makes a 500 indistinguishable from "you have no chats"',
    ).toEqual([])
  })
})

describe('the two chat-history surfaces', () => {
  const src = readFileSync(join(SRC, 'features/ChatPage.tsx'), 'utf8')

  it('capture the error from the hook', () => {
    expect((src.match(/error: sessionsError/g) ?? []).length, 'both readers must capture the error').toBe(2)
  })

  it('render the shared LoadError, with a retry, for both', () => {
    const uses = src.match(/<LoadError what="chats" error=\{sessionsError\} onRetry=\{refreshSessions\} \/>/g) ?? []
    expect(uses.length, 'the page and the side panel each need the branch').toBe(2)
  })

  it('tests the error BEFORE the loading and empty branches', () => {
    expect(src).toMatch(/data === undefined && sessionsError \?/)
    expect(src).toMatch(/sessions === null && sessionsError \?/)
  })

  it('leaves the empty state as a non-alert, so "you have none" does not interrupt', () => {
    expect(src).toMatch(/<EmptyState icon=\{MessageSquare\} title="No chats yet"/)
  })
})
