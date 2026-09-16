import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'


const boom = () => Promise.reject(new Error('session is busy'))
const session = { id: 's1', title: 'bash', cwd: '/w' }

const notified: string[] = []
function mockDeps(over: Record<string, unknown>) {
  notified.length = 0
  vi.doMock('../../app/shell/appSdk', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    notify: (msg: string) => { notified.push(msg) },
  }))
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      terminals: () => Promise.resolve([session]),
      createTerminal: () => Promise.resolve(session),
      deleteTerminal: () => Promise.resolve({ ok: true }),
      terminalPersist: () => Promise.resolve({ persist: false }),
      ...over,
    },
  }))
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); localStorage.clear() })

describe('the terminal close reports a failure instead of dropping the tab', () => {
  it('keeps the tab and tells the user when the session will not close', async () => {
    mockDeps({ deleteTerminal: boom })
    const { TerminalDrawer } = await import('./TerminalDrawer')
    render(<TerminalDrawer open onClose={() => {}} onOpenFull={() => {}} />)
    const close = await waitFor(() => screen.getByRole('button', { name: /close .*(session|bash)/i }))
    fireEvent.click(close)
    await waitFor(() => expect(notified.some((m) => /close the terminal session/i.test(m))).toBe(true))
    expect(screen.getAllByRole('tab'), 'the session is still open, so its tab stays').toHaveLength(1)
  })

  it('closes the tab normally when the session really does close', async () => {
    mockDeps({})
    const { TerminalDrawer } = await import('./TerminalDrawer')
    render(<TerminalDrawer open onClose={() => {}} onOpenFull={() => {}} />)
    const close = await waitFor(() => screen.getByRole('button', { name: /close .*(session|bash)/i }))
    fireEvent.click(close)
    await waitFor(() => expect(screen.queryAllByRole('tab')).toHaveLength(0))
    expect(notified, 'a successful close says nothing').toEqual([])
  })
})

describe('no surface flips local state on a write it discarded', () => {
  const SRC = join(process.cwd(), "src")
  const walk = (d: string): string[] => readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\./.test(n) ? [p] : []
  })
  const codeOf = (f: string) => readFileSync(f, 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

  it('the two terminal closes await the delete and bail on rejection', () => {
    for (const rel of ['features/terminal/TerminalPage.tsx', 'features/terminal/TerminalDrawer.tsx']) {
      const code = codeOf(join(SRC, rel))
      const at = code.indexOf('const closeSession')
      expect(at, `${rel} must still have closeSession`).toBeGreaterThan(-1)
      const fn = code.slice(at, at + 700)
      expect(fn, `${rel}: the delete must not swallow`).not.toMatch(/deleteTerminal\([^)]*\)\.catch\(\(\)\s*=>\s*\{\s*\}\)/)
      expect(fn, `${rel}: a rejection must stop before the tab is dropped`).toMatch(/catch[\s\S]{0,220}?return\b/)
    }
  })

  it('each surface reports through a channel that is actually visible to it', () => {
    const drawer = codeOf(join(SRC, 'features/terminal/TerminalDrawer.tsx'))
    const at = drawer.indexOf('const closeSession')
    expect(drawer.slice(at, at + 700), 'the Drawer must not use its open-gated error state')
      .not.toMatch(/setError\(/)
    expect(drawer.slice(at, at + 700), 'and must notify instead').toMatch(/notify\(/)

    const page = codeOf(join(SRC, 'features/terminal/TerminalPage.tsx'))
    const pAt = page.indexOf('const closeSession')
    expect(page.slice(pAt, pAt + 700), 'TerminalPage renders InlineError ungated, so it sets it')
      .toMatch(/setError\(/)
  })

  it("the widget save toggle obeys the rule its own sibling states", () => {
    const code = codeOf(join(SRC, 'shared/ui/widget/WidgetFrame.tsx'))
    const at = code.indexOf('const toggleSave')
    const fn = code.slice(at, at + 900)
    expect(fn, 'the writes must not be swallowed').not.toMatch(/\.catch\(\(\)\s*=>\s*\{\s*\}\)/)
    expect(fn, 'and the flag moves only after the write returns')
      .toMatch(/await api\.deleteArtifact\([^)]*\)\s*setSaved\(false\)/)
    expect(code, 'pin still rolls back').toMatch(/setPinned\(false\)/)
  })

  it('the family has no unnamed members left', () => {
    const found: string[] = []
    for (const f of walk(SRC)) {
      const code = codeOf(f)
      for (const m of code.matchAll(/await api\.(\w+)\([^;]{0,200}?\.catch\(\(\)\s*=>\s*\{\s*\}\)/g)) {
        if (/^(get|list|fetch)/.test(m[1])) continue
        found.push(`${f.slice(SRC.length + 1)}:${m[1]}`)
      }
    }
    expect(found.sort()).toEqual([
      'features/ChatPage.tsx:sideOpen',
      'features/knowledge/KnowledgeDetailPage.tsx:deleteKnowledgeAnnotation',
      'app/shell/identity.tsx:saveDashboardConfig',
      'app/shell/identity.tsx:saveDashboardConfig',
    ].sort())
  })
})
