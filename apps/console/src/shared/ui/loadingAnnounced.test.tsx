import { describe, it, expect } from 'vitest'
import type { ReactElement } from 'react'
import { render, screen } from '@testing-library/react'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { LoadingStatus, ListSkeleton, FormSkeleton, CardGridSkeleton, Loading } from './ListScaffold'

// region at all, and unfindable. It is one region now.

const SRC = join(process.cwd(), "src")
const walk = (d: string): string[] =>
  readdirSync(d).flatMap((n) => {
    const p = join(d, n)
    if (statSync(p).isDirectory()) return walk(p)
    return /\.tsx$/.test(n) && !/\.(test|doc)\.tsx$/.test(n) ? [p] : []
  })

describe('LoadingStatus is the announcement', () => {
  it('renders sr-only text a live region can announce', () => {
    render(<div role="status" aria-busy="true"><LoadingStatus /></div>)
    const region = screen.getByRole('status')
    expect(region.textContent?.trim()).toBe('Loading…')
    expect(region.querySelector('.sr-only'), 'invisible to sighted users').toBeTruthy()
  })

  it('names what is loading when it knows', () => {
    render(<div role="status" aria-busy="true"><LoadingStatus what="tasks" /></div>)
    expect(screen.getByRole('status').textContent?.trim()).toBe('Loading tasks…')
  })

  it('a status region is NOT named from its content — which is why this had to be text', () => {
    render(<div role="status" aria-busy="true"><LoadingStatus what="providers" /></div>)
    expect(screen.queryByRole('status', { name: 'Loading providers…' }), 'status takes no name from content').toBeNull()
    expect(screen.getByRole('status').textContent?.trim()).toBe('Loading providers…')
  })
})

describe('every shared skeleton says something', () => {
  const CASES: [string, () => ReactElement][] = [
    ['ListSkeleton', () => <ListSkeleton rows={2} />],
    ['FormSkeleton', () => <FormSkeleton sections={1} rows={1} />],
    ['CardGridSkeleton', () => <CardGridSkeleton cards={2} />],
  ]
  for (const [name, el] of CASES) {
    it(`${name} mounts a status region with text`, () => {
      const { container } = render(el())
      const region = container.querySelector('[role="status"][aria-busy="true"]')
      expect(region, `${name} must still be a busy status region`).toBeTruthy()
      expect(region!.textContent?.trim(), `${name} announced nothing`).toBe('Loading…')
    })
  }

  it('each passes `what` through to the announcement', () => {
    const { container } = render(<ListSkeleton rows={1} what="prompts" />)
    expect(container.querySelector('[role="status"]')!.textContent?.trim()).toBe('Loading prompts…')
  })

  it('the label is gone, because the announced text is the one that matters', () => {
    const src = readFileSync(join(SRC, 'shared/ui/ListScaffold.tsx'), 'utf8')
    expect(src, 'two strings for one region is drift').not.toMatch(/aria-busy="true" aria-label="Loading"/)
  })
})

describe('Loading — the other loading state — announces too', () => {
  it('is a live region whose visible text is the announcement', () => {
    render(<Loading what="workflows" />)
    const region = screen.getByRole('status')
    expect(region.getAttribute('aria-busy')).toBe('true')
    expect(region.textContent?.trim()).toBe('Loading workflows…')
    expect(region.querySelector('.sr-only'), 'no sr-only twin needed: the text is visible').toBeNull()
  })

  it('falls back to a bare Loading… when it does not know what', () => {
    render(<Loading />)
    expect(screen.getByRole('status').textContent?.trim()).toBe('Loading…')
  })

  it('every call site names what is loading', () => {
    const code = (abs: string) => readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    const sites = walk(SRC).filter((abs) => /<Loading\b/.test(code(abs)) && !abs.endsWith('ListScaffold.tsx'))
    expect(sites.length, 'Loading call sites').toBeGreaterThanOrEqual(6)
    const bare = sites.filter((abs) => /<Loading \/>/.test(code(abs))).map((a) => a.slice(SRC.length + 1))
    expect(bare, `these render a nameless Loading:\n${bare.join('\n')}`).toEqual([])
  })
})

describe('the census is closed: no busy region without an announcement', () => {
  it('every file with a busy region renders LoadingStatus', () => {
    const offenders = walk(SRC)
      .filter((abs) => /aria-busy="true"/.test(readFileSync(abs, 'utf8')))
      .filter((abs) => !/LoadingStatus/.test(readFileSync(abs, 'utf8')))
      .map((abs) => abs.slice(SRC.length + 1))
    expect(offenders, `these mark themselves busy and announce nothing:\n${offenders.join('\n')}`).toEqual([])
  })

  it('finds the population — the scan is not vacuous', () => {
    const files = walk(SRC).filter((abs) => /aria-busy="true"/.test(readFileSync(abs, 'utf8')))
    expect(files.length, 'files with a busy region').toBeGreaterThanOrEqual(7)
  })

  it('the hand-rolled regions kept their specific wording', () => {
    const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
    expect(read('features/ChatPage.tsx')).toMatch(/LoadingStatus what="conversation"/)
    expect(read('features/settings/ProvidersPanel.tsx')).toMatch(/LoadingStatus what="providers"/)
    expect(read('features/settings/ModelBackends.tsx')).toMatch(/LoadingStatus what="model providers"/)
    expect(read('features/workflows/NodeInspectorDrawer.tsx')).toMatch(/LoadingStatus what="node detail"/)
    expect(read('features/workflows/WorkspacePanel.tsx')).toMatch(/LoadingStatus what="the run’s workspace"/)
    expect(read('features/settings/AppsPanel.tsx')).toMatch(/LoadingStatus what="app settings"/)
  })
})

describe('the route-level and app-host loading states announce', () => {
  const SRC = join(process.cwd(), "src")
  const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
  const code = (rel: string) => read(rel)
    .replace(/\{\s*\/\*[\s\S]*?\*\/\s*\}/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^[ \t]*\/\/.*$/gm, '')

  it("the Suspense fallback for every route is a live region that says something", () => {
    const src = code('app/shell/App.tsx')
    const fallback = src.match(/function PageFallback\(\)[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fallback, 'PageFallback not found — this rail is measuring nothing').toContain('Loader2')
    expect(fallback, 'the region must be a live region').toMatch(/role="status"/)
    expect(fallback, 'aria-busy marks it as in-flight').toMatch(/aria-busy="true"/)
    expect(fallback, 'a live region announces its CONTENT, so it needs LoadingStatus text')
      .toMatch(/<LoadingStatus\b/)
  })

  it('the app-host page names what it is loading', () => {
    const src = code('features/apps/AppHostPage.tsx')
    expect(src).toMatch(/role="status"/)
    expect(src, '"Loading…" on a page that hosts someone else\'s UI does not say whose')
      .toMatch(/<LoadingStatus what="the app"/)
  })

  it('an aria-label is NOT accepted in place of announced text', () => {
    const fallback = code('app/shell/App.tsx').match(/function PageFallback\(\)[\s\S]*?\n\}/)?.[0] ?? ''
    expect(fallback).not.toMatch(/aria-label="Loading/)
  })

  it('the scanned source is real (guard against a vacuous pass)', () => {
    expect(code('app/shell/App.tsx')).toContain('function PageFallback')
    expect(code('app/shell/App.tsx')).not.toContain(['a', 'bare', 'spinning', 'icon'].join(' '))
  })
})
