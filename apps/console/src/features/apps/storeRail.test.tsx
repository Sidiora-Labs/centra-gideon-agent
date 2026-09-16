import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, within, cleanup, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { artGradient, artStops, artHash } from './appArt'


const SRC = join(process.cwd(), "src")

function code(abs: string): string {
  return readFileSync(abs, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')
}

const NOTES = {
  name: 'notes', displayName: 'Notes', description: 'Take notes', version: '1.0.0',
  icon: 'ClipboardList', heroUrl: 'https://example.test/notes.png', author: 'core',
  source: '', sourceKind: 'bundled', isProvider: false, providerType: '', tags: ['productivity'],
}
const TIMER = {
  name: 'timer', displayName: 'Timer', description: 'Count down', version: '1.0.0',
  icon: '', author: 'core',
  source: '', sourceKind: 'bundled', isProvider: false, providerType: '', tags: ['utility'],
}
const LEDGER = {
  name: 'ledger', displayName: 'Ledger', description: 'Money in, money out', version: '0.2.0',
  icon: 'Database', author: 'me',
  source: '/srv/apps/ledger', sourceKind: 'local', isProvider: false, providerType: '', tags: ['productivity'],
}

const CATALOG = {
  bundled: [NOTES, TIMER],
  gitSources: [], localSources: ['/srv/apps'],
  localApps: [LEDGER], remoteApps: [], gitApps: [],
}

function mockApi() {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      apps: () => Promise.resolve([]),
      appCatalog: () => Promise.resolve(CATALOG),
    },
  }))
}

function makeRouter(search = 'view=store') {
  const params = new URLSearchParams(search)
  return {
    get query(): Record<string, string> { return Object.fromEntries(params.entries()) },
    setQuery(patch: Record<string, string | null | undefined>) {
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === '') params.delete(k)
        else params.set(k, v)
      }
    },
    search() { return params.toString() },
  }
}

async function mount(router: ReturnType<typeof makeRouter>) {
  const { AppsSection } = await import('./AppsSection')
  const r = render(<AppsSection query={router.query} setQuery={router.setQuery} navigate={() => {}} />)
  await waitFor(() => expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy())
  return r
}

function setNarrow() {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q),
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }))
}

function cards(): string[] {
  return screen.getAllByRole('button', { name: /— details$/ })
    .map((b) => (b.getAttribute('aria-label') ?? '').replace(/ — details$/, ''))
    .sort()
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); mockApi() })
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('the rail is rendered by the page a user reaches', () => {
  it('#/apps is routed to AppsSection, the component every test below mounts', () => {
    const app = readFileSync(join(SRC, 'app/shell/App.tsx'), 'utf8')
    expect(app, "the shell's own route table").toMatch(/case 'apps': return <AppsSection/)
  })

  it('AppsSection renders the rail in the Store view — and NOT in the Library view', async () => {
    const r = await mount(makeRouter('view=store'))
    expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy()
    r.unmount()
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'library' }} setQuery={() => {}} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByRole('heading', { level: 1, name: 'Apps' })).toBeTruthy())
    expect(screen.queryByRole('navigation', { name: 'Categories and sources' })).toBeNull()
  })
})

describe('a rail selection filters the grid and survives a reload', () => {
  it('a category writes ?stag=, and a fresh mount from that URL is still filtered', async () => {
    const router = makeRouter('view=store')
    const r = await mount(router)

    expect(cards()).toEqual(['Ledger', 'Notes', 'Timer'])
    expect(router.search(), 'no category in the URL yet').not.toMatch(/stag/)
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail).getByRole('button', { name: /^All apps/ }).getAttribute('aria-pressed')).toBe('true')

    await userEvent.click(within(rail).getByRole('button', { name: /^Productivity/ }))

    const recorded = router.search()
    expect(recorded, 'the selection is IN the URL, not only in component state').toMatch(/stag=productivity/)

    r.unmount()
    const reloaded = makeRouter(recorded)
    await mount(reloaded)

    expect(cards(), 'the grid comes back filtered').toEqual(['Ledger', 'Notes'])
    const rail2 = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail2).getByRole('button', { name: /^Productivity/ }).getAttribute('aria-pressed')).toBe('true')
    expect(within(rail2).getByRole('button', { name: /^All apps/ }).getAttribute('aria-pressed')).toBe('false')
  })

  it('a source writes ?ssrc=, and a fresh mount from that URL is still filtered', async () => {
    const router = makeRouter('view=store')
    const r = await mount(router)
    expect(cards()).toEqual(['Ledger', 'Notes', 'Timer'])

    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    await userEvent.click(within(rail).getByRole('button', { name: /^Built-in/ }))

    const recorded = router.search()
    expect(recorded, 'the source key is the divider key, in the URL').toMatch(/ssrc=builtin/)

    r.unmount()
    await mount(makeRouter(recorded))
    expect(cards(), 'only the Built-in group survives').toEqual(['Notes', 'Timer'])
    const rail2 = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail2).getByRole('button', { name: /^Built-in/ }).getAttribute('aria-pressed')).toBe('true')
  })

  it('an unknown ?stag= yields the no-match empty state, not a silent full grid', async () => {
    await mount(makeRouter('view=store&stag=nope'))
    expect(screen.queryAllByRole('button', { name: /— details$/ })).toHaveLength(0)
    expect(screen.getByRole('heading', { name: 'No matching apps' })).toBeTruthy()
  })
})

describe('the rail and the dropdown are one filter at two widths', () => {
  it('wide: the rail is present and the dropdown does NOT repeat its two dimensions', async () => {
    await mount(makeRouter('view=store'))
    expect(screen.getByRole('navigation', { name: 'Categories and sources' })).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const outside = (t: string) => screen.queryAllByText(t).filter((el) => !rail.contains(el))
    expect(outside('Sort by'), 'the menu is open and populated').toHaveLength(1)
    expect(outside('Categories'), 'the rail owns it at this width').toHaveLength(0)
    expect(outside('Sources'), 'the rail owns it at this width').toHaveLength(0)
  })

  it('narrow: the rail is gone and the dropdown carries Categories + Sources', async () => {
    setNarrow()
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={{ view: 'store' }} setQuery={() => {}} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Filter & sort' })).toBeTruthy())
    expect(screen.queryByRole('navigation', { name: 'Categories and sources' }), 'no rail below the threshold').toBeNull()
    await userEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    expect(screen.getByText('Categories'), 'the fallback carries the category dimension').toBeTruthy()
    expect(screen.getByText('Sources'), 'and the source dimension').toBeTruthy()
    expect(screen.getByRole('button', { name: /^Productivity/ })).toBeTruthy()
  })

  it('narrow keeps the dimensions REACHABLE — the fallback filters too', async () => {
    setNarrow()
    const router = makeRouter('view=store')
    const { AppsSection } = await import('./AppsSection')
    render(<AppsSection query={router.query} setQuery={router.setQuery} navigate={() => {}} />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Filter & sort' })).toBeTruthy())
    await userEvent.click(screen.getByRole('button', { name: 'Filter & sort' }))
    await userEvent.click(screen.getByRole('button', { name: /^Utility/ }))
    expect(router.search(), 'the dropdown writes the same URL key the rail does').toMatch(/stag=utility/)
  })
})

describe('every card is banner-topped, with or without hero art', () => {
  it('a hero app shows its image; a hero-less app shows generated token art', async () => {
    const { container } = await mount(makeRouter('view=store'))
    const banners = [...container.querySelectorAll<HTMLElement>('[data-art]')]
    expect(banners, 'one banner per card — no card is bannerless').toHaveLength(3)

    const kinds = banners.map((b) => b.dataset.art).sort()
    expect(kinds, 'both paths are exercised by this fixture').toEqual(['generated', 'generated', 'hero'])

    const hero = banners.find((b) => b.dataset.art === 'hero')!
    expect(hero.querySelector('img')?.getAttribute('src')).toBe(NOTES.heroUrl)

    const generated = banners.find((b) => b.dataset.art === 'generated')!
    expect(generated.querySelector('img'), 'no broken image element on the fallback path').toBeNull()
    expect(generated.style.background, 'a real gradient, not an empty slot').toMatch(/linear-gradient/)
    expect(generated.style.background, 'built from scheme tokens').toMatch(/var\(--color-/)
    expect(generated.style.background, 'and never a literal color').not.toMatch(/#[0-9a-f]{3,8}/i)
  })

  it('the icon avatar is rendered even when the manifest declares no icon', async () => {
    const { container } = await mount(makeRouter('view=store&stag=utility'))
    expect(cards()).toEqual(['Timer'])
    expect(container.querySelectorAll('[data-art]')).toHaveLength(1)
    expect(container.querySelector('.ring-surface-container'), 'the avatar tile is present').toBeTruthy()
  })
})

describe('the rail is operable from the accessibility tree', () => {
  it('exactly one entry per block is pressed, and it is the selected one', async () => {
    await mount(makeRouter('view=store&stag=utility&ssrc=builtin'))
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const pressed = within(rail).getAllByRole('button', { pressed: true })
      .map((b) => b.textContent)
    expect(pressed, 'the two selected entries, and nothing else').toEqual(['Utility1', 'Built-in2'])
    cleanup()
    await mount(makeRouter('view=store'))
    const rail2 = screen.getByRole('navigation', { name: 'Categories and sources' })
    expect(within(rail2).getAllByRole('button', { pressed: true }).map((b) => b.textContent))
      .toEqual(['All apps3', 'All sources3'])
  })

  it('every entry is a real button — reachable by Tab, operable by Enter', async () => {
    const router = makeRouter('view=store')
    await mount(router)
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const productivity = within(rail).getByRole('button', { name: /^Productivity/ })
    for (const b of within(rail).getAllByRole('button')) {
      expect(b.getAttribute('tabindex'), `${b.textContent} must stay a tab stop`).not.toBe('-1')
    }
    productivity.focus()
    await userEvent.keyboard('{Enter}')
    expect(router.search(), 'Enter activates it — no click needed').toMatch(/stag=productivity/)
  })

  it('Tab walks every entry of both blocks, in reading order', async () => {
    await mount(makeRouter('view=store'))
    const rail = screen.getByRole('navigation', { name: 'Categories and sources' })
    const entries = within(rail).getAllByRole('button', { name: /\d$|^Add source$|apps$|sources$/ })
    expect(entries.length, 'All apps + 2 categories + All sources + 2 sources + Add source').toBe(7)

    within(rail).getByRole('button', { name: /^All apps/ }).focus()
    const walked: string[] = [(document.activeElement?.textContent ?? '').trim()]
    for (let i = 1; i < entries.length; i++) {
      await userEvent.tab()
      walked.push((document.activeElement?.textContent ?? '').trim())
    }
    expect(walked, 'every entry is reached, in DOM order, with no gap').toEqual(
      ['All apps3', 'Productivity2', 'Utility1', 'All sources3', 'Built-in2', 'apps1', 'Add source'])
  })

  it('the pressed state is not carried by a class alone', async () => {
    const src = code(join(SRC, 'features/apps/StoreSideRail.tsx'))
    expect(src, 'aria-pressed is bound to the live selection, not hardcoded')
      .toMatch(/pressed=\{value === o\.key\}/)
    expect(code(join(SRC, 'shared/ui/FilterRow.tsx')), 'FilterRow publishes the prop').toMatch(/aria-pressed=\{pressed\}/)
    expect(src, 'no visually-hidden text inside the button to pollute its accessible name').not.toMatch(/sr-only/)
  })
})

describe('generated card art is deterministic and token-only', () => {
  it('the same app always draws the same art, and different apps differ', () => {
    expect(artGradient('notes')).toBe(artGradient('notes'))
    expect(artHash('notes')).not.toBe(artHash('timer'))
    const names = ['notes', 'timer', 'ledger', 'inbox', 'weather', 'kanban']
    expect(new Set(names.map(artGradient)).size, 'not all one wash').toBeGreaterThan(1)
  })

  it('the two stops are always distinct tokens', () => {
    for (let i = 0; i < 500; i++) {
      const { from, to } = artStops(`app-${i}`)
      expect(to, `app-${i}`).not.toBe(from)
    }
  })

  it('carries no literal color, in any name', () => {
    for (const n of ['a', 'zz', 'com.example.app', '']) {
      expect(artGradient(n)).not.toMatch(/#[0-9a-f]{3,8}/i)
      expect(artGradient(n)).toMatch(/color-mix\(in srgb, var\(--color-/)
    }
  })
})
