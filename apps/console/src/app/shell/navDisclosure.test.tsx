import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import {
  STARTER_NAV_IDS, isDisclosed, readNavDisclosure, pinNavSurface, setNavMode, undisclosedCount,
} from './navDisclosure'
import { navigationItems } from './navigationModel'


vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))

vi.mock('./identity', async (orig) => {
  const real = await orig<typeof import('./identity')>()
  return {
    ...real,
    useIdentity: () => ({ name: 'Ada', onboarded: true, loaded: true, setName: async () => {}, clearName: async () => {} }),
  }
})

const ENVELOPES: Record<string, unknown> = {
  dashboardConfig: { user_name: 'Ada' },
  agents: { agents: [] },
  skillProposals: { proposals: [], lastReview: null },
}
vi.mock('../../shared/data/api', async (orig) => {
  const real = await orig<typeof import('../../shared/data/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => () => Promise.resolve(prop in ENVELOPES ? ENVELOPES[prop] : []),
  })
  return { ...real, api: stub }
})

const { App } = await import('./App')
const { ThemeProvider } = await import('./theme')
const { AppearanceProvider } = await import('./appearance')
const { PersonalityProvider } = await import('./personality')

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><App /></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const rail = () => document.querySelector<HTMLElement>('nav[data-tour="rail"]')!
const railLinks = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label'))

function setViewport(isMobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q) ? isMobile : false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  }))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  location.hash = '#/dashboard'
  setViewport(false)
})
afterEach(cleanup)

describe('the disclosure store', () => {
  it('an install with no record starts with familiar navigation', () => {
    expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: [] })
  })

  it('a record written by onboarding starts on the starter rail', () => {
    setNavMode('starter')
    expect(readNavDisclosure().mode).toBe('starter')
  })

  it('a corrupt or partial record falls back without throwing', () => {
    localStorage.setItem('nav-disclosure', '{not json')
    expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: [] })
    localStorage.setItem('nav-disclosure', '{"pinned":[1,"loops",null]}')
    expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: ['loops'] })
  })

  it('pinning is idempotent and survives a fresh read', () => {
    setNavMode('starter')
    pinNavSurface('tools')
    pinNavSurface('tools')
    expect(readNavDisclosure().pinned).toEqual(['tools'])
  })

  it('pinning does not silently flip an expert install back to starter', () => {
    setNavMode('expert')
    pinNavSurface('tools')
    expect(readNavDisclosure().mode).toBe('expert')
  })

  it('preserves a saved expert preference across fresh reads', () => {
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'expert', pinned: ['tools'] }))
    expect(readNavDisclosure()).toEqual({ mode: 'expert', pinned: ['tools'] })
  })

  it('isDisclosed: starter shows the starter set, pins, and app tiles — nothing else', () => {
    for (const id of STARTER_NAV_IDS) expect(isDisclosed(id, 'starter', [])).toBe(true)
    expect(isDisclosed('tools', 'starter', [])).toBe(false)
    expect(isDisclosed('dashboard', 'starter', [])).toBe(false)
    expect(isDisclosed('rooms', 'starter', [])).toBe(false)
    expect(isDisclosed('tools', 'starter', ['tools'])).toBe(true)
    expect(isDisclosed('app/shell/weather', 'starter', [])).toBe(true)
    expect(isDisclosed('tools', 'expert', [])).toBe(true)
  })

  it('undisclosedCount counts only what starter holds back', () => {
    const ids = [...STARTER_NAV_IDS, 'tools', 'learning', 'app/shell/weather']
    expect(undisclosedCount(ids, [])).toBe(2)
    expect(undisclosedCount(ids, ['tools'])).toBe(1)
  })
})

describe('the rail a fresh install sees', () => {
  it('shows the starter surfaces and holds the rest back', async () => {
    renderApp()
    const names = await waitFor(() => {
      const n = railLinks()
      expect(n.length).toBeGreaterThan(3)
      return n
    })
    for (const label of ['New conversation', 'Conversations', 'Projects', 'Files', 'Calendar', 'Notifications', 'Apps', 'Manage apps', 'Your account']) expect(names).toContain(label)
    for (const label of ['Home', 'Chat', 'Rooms', 'Learning', 'Tools', 'Terminal', 'Workflows']) expect(names).not.toContain(label)
    expect(rail().textContent).not.toMatch(/More/i)
    expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: [] })
  })

  it('labels the familiar groups in starter navigation', async () => {
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Apps'))
    expect(rail().textContent).toMatch(/Your space/i)
    expect(rail().textContent).toMatch(/Your apps/i)
  })

  it('says how many surfaces it is holding back, in the control\'s own name', async () => {
    renderApp()
    const more = await screen.findByRole('button', { name: /^Everything, show \d+ more surfaces$/ })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    expect(more.getAttribute('aria-label')).toBe(`Everything, show ${undisclosedCount(navigationItems().map(item => item.id), [])} more surfaces`)
    expect(more.className).not.toContain('outline-none')
    expect(more.closest('button')).toBe(more)
    more.focus()
    expect(more).toHaveFocus()
  })

  it('renders NO control once nothing is left to reveal', async () => {
    localStorage.setItem('nav-disclosure', JSON.stringify({
      mode: 'starter',
      pinned: navigationItems().map(item => item.id),
    }))
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Workflows'), { timeout: 15000 })
    expect(screen.queryByRole('button', { name: /^Everything, show/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /^Show fewer/ })).toBeNull()
  })
})

describe('apps collection and management routes', () => {
  it('renders the collection at apps and keeps management one click away', async () => {
    location.hash = '#/apps'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'More room for what you do.' }, { timeout: 15000 })).toBeInTheDocument()
    await userEvent.setup().click(within(screen.getByRole('navigation', { name: 'App categories' })).getByRole('button', { name: 'Manage apps' }))
    await waitFor(() => expect(location.hash).toBe('#/apps/manage'))
    expect(await screen.findByRole('heading', { name: 'Apps', level: 1 }, { timeout: 15000 })).toBeInTheDocument()
  })

  it('preserves legacy apps query links for installation and configuration', async () => {
    location.hash = '#/apps?view=store'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Apps', level: 1 }, { timeout: 15000 })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'More room for what you do.' })).toBeNull()
  })
})

describe('the visible Search launcher', () => {
  it('opens the command palette and routes a selected familiar destination', async () => {
    const user = userEvent.setup()
    renderApp()
    await user.click(within(rail()).getByRole('button', { name: 'Search' }))
    const search = await screen.findByLabelText('Search pages and actions')
    await user.type(search, 'Manage apps')
    await user.click(await screen.findByRole('option', { name: /^Manage apps/ }))
    await waitFor(() => expect(location.hash).toBe('#/apps/manage'))
    expect(screen.queryByRole('dialog', { name: 'Command palette' })).toBeNull()
  })
})

describe('a hidden surface is hidden from the RAIL, never from the app', () => {
  beforeEach(() => setNavMode('starter'))

  it('deep-linking a hidden surface renders it — and pins it into the rail', async () => {
    expect(isDisclosed('tools', 'starter', [])).toBe(false)
    location.hash = '#/tools'
    renderApp()

    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()

    await waitFor(() => expect(readNavDisclosure().pinned).toContain('tools'))

    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })

  it('the pin survives a reload', async () => {
    location.hash = '#/tools'
    renderApp()
    await waitFor(() => expect(readNavDisclosure().pinned).toContain('tools'))
    cleanup()

    location.hash = '#/dashboard'
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })

  it('the command palette offers every surface, including the hidden ones', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).not.toContain('Tools'))

    await user.keyboard('{Meta>}k{/Meta}')
    const search = await screen.findByLabelText('Search pages and actions')
    await user.type(search, 'Tools')
    const hit = await screen.findByRole('option', { name: /^Tools/ })
    await user.click(hit)

    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })
})

describe('expert mode', () => {
  it('the rail\'s own control expands everything, permanently', async () => {
    setNavMode('starter')
    const user = userEvent.setup()
    renderApp()
    const more = await screen.findByRole('button', { name: /^Everything, show \d+ more surfaces$/ })
    await user.click(more)

    await waitFor(() => {
      const names = railLinks()
      for (const label of ['Learning', 'Tools', 'Terminal', 'Workflows', 'Prompts', 'Agents']) {
        expect(names).toContain(label)
      }
    })
    expect(readNavDisclosure().mode).toBe('expert')
    expect(rail().textContent).toMatch(/Your space/i)
    expect(rail().textContent).toMatch(/Your apps/i)
    expect(await screen.findByRole('button', { name: /^Show fewer, hide \d+ surfaces$/ }))
      .toHaveAttribute('aria-expanded', 'true')
  })

  it('does not auto-pin, so "show fewer" still means something', async () => {
    setNavMode('expert')
    location.hash = '#/tools'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()
    expect(readNavDisclosure().pinned).toEqual([])
  })

  it('a saved expert preference shows every surface from the first paint', async () => {
    setNavMode('expert')
    renderApp()
    await waitFor(() => {
      const names = railLinks()
      for (const label of ['Home', 'New conversation', 'Learning', 'Tools', 'Terminal', 'Workflows', 'Your account']) {
        expect(names).toContain(label)
      }
    })
    expect(screen.queryByRole('button', { name: /^Everything, show/ })).toBeNull()
  })
})

describe('at a mobile viewport', () => {
  it('the control is in the drawer, named, and operable from the keyboard', async () => {
    setViewport(true)
    setNavMode('starter')
    const user = userEvent.setup()
    renderApp()
    await user.click(await screen.findByRole('button', { name: 'Expand sidebar' }))

    const more = await screen.findByRole('button', { name: /^Everything, show \d+ more surfaces$/ })
    expect(more).toHaveAttribute('aria-expanded', 'false')
    more.focus()
    expect(more).toHaveFocus()
    await user.keyboard('{Enter}')

    await waitFor(() => expect(readNavDisclosure().mode).toBe('expert'))
    await waitFor(() => expect(railLinks()).toContain('Tools'))
  })
})

describe('the control converges on the rail\'s own motion, so reduced-motion is inherited', () => {
  it('adds no bespoke animation', () => {
    const src = readFileSync(join(process.cwd(), "src/shared/ui/NavRail.tsx"), 'utf8')
    const at = src.indexOf('aria-expanded={disclosure.expanded}')
    expect(at, 'the disclosure control must still be in NavRail').toBeGreaterThan(-1)
    const block = src.slice(at - 400, at + 1600)
    expect(block, 'same tap spring as every nav item').toContain('transition={spring.spatialFast}')
    expect(block, 'the chevron rotates through the shared CSS transition').toContain("'transition-transform'")
    expect(block, 'no hand-rolled keyframe or duration').not.toMatch(/duration:|animate=\{\{|keyframes/)
  })
})

describe('the Appearance toggle', () => {
  it('is reachable at #/settings/design and changes the real rail', async () => {
    setNavMode('starter')
    const user = userEvent.setup()
    location.hash = '#/settings/design'
    renderApp()

    const sw = await screen.findByRole('switch', { name: 'Show every surface' }, { timeout: 15000 })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    await user.click(sw)

    await waitFor(() => expect(railLinks()).toContain('Workflows'), { timeout: 15000 })
    expect(readNavDisclosure().mode).toBe('expert')
    await user.click(await screen.findByRole('switch', { name: 'Show every surface' }))
    await waitFor(() => expect(railLinks()).not.toContain('Workflows'), { timeout: 15000 })
    expect(railLinks()).toContain('Your account')
  })
})
