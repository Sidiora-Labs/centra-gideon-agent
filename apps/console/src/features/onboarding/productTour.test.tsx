import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, render, screen, waitFor, cleanup, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { readNavDisclosure, setNavMode } from '../../app/shell/navDisclosure'
import { requestProductTour, consumeProductTourRequest } from './tourLaunch'
import { PRODUCT_TOUR_STOPS } from './ProductTour'


const STOP_BUDGET_MS = 10_000
const WALK_SLACK_MS = 20_000
vi.setConfig({ testTimeout: STOP_BUDGET_MS * PRODUCT_TOUR_STOPS.length + WALK_SLACK_MS })

vi.mock('../../shared/data/useChatSocket', () => ({ useChatSocket: () => {} }))
vi.mock('../../shared/ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../../shared/ui/DegradedChip', () => ({ DegradedChip: () => null }))
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-import</button>
  ),
}))
vi.mock('./EssentialsStep', () => ({
  EssentialsStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-essentials</button>
  ),
}))
vi.mock('./TryOneStep', () => ({
  TryOneStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-try</button>
  ),
}))
vi.mock('../settings/settingsWidgets', () => ({ SETTINGS_WIDGETS: [] }))

const calls: string[] = []
const REJECT = Symbol('reject')
const ENVELOPES: Record<string, unknown> = {}
function resetEnvelopes() {
  for (const k of Object.keys(ENVELOPES)) delete ENVELOPES[k]
  Object.assign(ENVELOPES, {
    dashboardConfig: { user_name: 'Ada' },
    saveDashboardConfig: { ok: true },
    agents: { agents: [] },
    onboarding: { needs_model: true, has_model_provider: false, has_chat_binding: false },
    saveOnboardingState: { ok: true, state: {} },
    discover: { enabled: false, visible_count: 0, areas: [] },
    doctor: { ok: true, capabilities: {} },
    skillProposals: { proposals: [], lastReview: null },
    modelsLoaded: {
      loaded: [], providers: [],
      pressure: { total_mb: 0, used_mb: 0, available_mb: 0, used_pct: 0, warn_pct: 90, warn: false, source: 'unavailable' },
    },
  })
}

vi.mock('../../shared/data/api', async (orig) => {
  const real = await orig<typeof import('../../shared/data/api')>()
  const stub = new Proxy({}, {
    get: (_t, prop: string) => (..._args: unknown[]) => {
      calls.push(prop)
      const v = prop in ENVELOPES ? ENVELOPES[prop] : []
      return v === REJECT ? Promise.reject(new Error('stubbed failure')) : Promise.resolve(v)
    },
  })
  return { ...real, api: stub }
})

const { App } = await import('../../app/shell/App')
const { ThemeProvider } = await import('../../app/shell/theme')
const { AppearanceProvider } = await import('../../app/shell/appearance')
const { PersonalityProvider } = await import('../../app/shell/personality')
const { IdentityProvider } = await import('../../app/shell/identity')

const renderApp = () => render(
  <ThemeProvider><AppearanceProvider><PersonalityProvider><IdentityProvider>
    <App />
  </IdentityProvider></PersonalityProvider></AppearanceProvider></ThemeProvider>,
)

const rail = () => screen.getByRole('navigation')
const railLinks = () => within(rail()).getAllByRole('button').map((b) => b.getAttribute('aria-label'))
const tour = () => screen.queryByRole('dialog')
const next = () => screen.getByRole('button', { name: /Next/ })

function setViewport(isMobile: boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: /max-width:\s*768px/.test(q) ? isMobile : false,
    media: q,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {},
    onchange: null, dispatchEvent: () => false,
  }))
}

function firstRun() { ENVELOPES.dashboardConfig = { user_name: '' } }

async function reachDoneScreen(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText('Your name'), 'Ada')
  await user.click(screen.getByRole('button', { name: 'Continue' }))
  await user.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
  await user.click(await screen.findByRole('button', { name: 'stub-skip-essentials' }))
  await user.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
  return screen.findByRole('button', { name: /Take the quick tour/ })
}

async function atStop(id: string) {
  await waitFor(() => expect(tour()).toHaveAttribute('data-tour-step', id), {
    timeout: STOP_BUDGET_MS,
  })
  return screen.getByRole('dialog')
}

beforeEach(() => {
  calls.length = 0
  resetEnvelopes()
  localStorage.clear()
  sessionStorage.clear()
  consumeProductTourRequest()
  location.hash = '#/dashboard'
  setViewport(false)
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('the done screen launches it, and the app is what it runs on', () => {
  it('finishing with "Take the quick tour" lands on a working shell with the tour up', async () => {
    firstRun()
    const user = userEvent.setup()
    renderApp()
    await user.click(await reachDoneScreen(user))

    await waitFor(() => expect(railLinks()).toContain('Chat'))
    const d = await atStop('rail')
    expect(d).toHaveAttribute('aria-modal', 'true')
    expect(d).toHaveAttribute('data-tour-anchored', 'true')
    expect(d.getAttribute('aria-label')).toBe('Gideon tour — step 1 of 5: The sidebar is the whole app')
  })

  it('"Start using" finishes WITHOUT the tour — it is offered, never imposed', async () => {
    firstRun()
    const user = userEvent.setup()
    renderApp()
    await reachDoneScreen(user)
    await user.click(screen.getByRole('button', { name: /Start using/ }))

    await waitFor(() => expect(railLinks()).toContain('Chat'))
    expect(tour()).toBeNull()
    expect(consumeProductTourRequest()).toBe(false)
  })
})

describe('it walks all five stops, over the real surfaces', () => {
  it('rail → chat → inbox → approvals → settings, every anchor resolved', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    act(() => { requestProductTour() })

    const walked: string[] = []
    for (const stop of PRODUCT_TOUR_STOPS) {
      const d = await atStop(stop.id)
      walked.push(stop.id)
      await waitFor(() => expect(d).toHaveAttribute('data-tour-anchored', 'true'), {
        timeout: STOP_BUDGET_MS,
      })
      expect(d.getAttribute('aria-label')).toContain(stop.title)
      if (stop.id !== 'settings') await user.click(next())
    }
    expect(walked).toEqual(['rail', 'chat', 'inbox', 'approvals', 'settings'])

    expect(screen.queryByRole('button', { name: /Next/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Done/ }))
    await waitFor(() => expect(tour()).toBeNull())
  })

  it('the settings stop keeps focus even though that surface autofocuses its own search', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    act(() => { requestProductTour() })

    await atStop('rail')
    for (let n = 0; n < 4; n += 1) await user.click(next())
    const d = await atStop('settings')
    expect(await screen.findByLabelText('Search settings')).toBeInTheDocument()
    await waitFor(() => expect(d.contains(document.activeElement)).toBe(true), {
      timeout: STOP_BUDGET_MS,
    })
  })

  it('each stop names an anchor that exists in the file hosting that surface', () => {
    const SRC = join(process.cwd(), "src")
    const HOSTS: Record<string, string> = {
      rail: 'shared/ui/NavRail.tsx',
      chat: 'features/ChatPage.tsx',
      inbox: 'features/inbox/InboxPage.tsx',
      approvals: 'features/dashboard/DashboardPage.tsx',
      settings: 'features/settings/SettingsHome.tsx',
    }
    for (const stop of PRODUCT_TOUR_STOPS) {
      const host = HOSTS[stop.anchor]
      expect(host, `no host recorded for the "${stop.anchor}" anchor`).toBeTruthy()
      const src = readFileSync(join(SRC, host), 'utf8')
      const ok = src.includes(`data-tour="${stop.anchor}"`) || src.includes(`tour="${stop.anchor}"`)
      expect(ok, `${host} must carry the "${stop.anchor}" tour anchor`).toBe(true)
    }
  })
})

describe('Escape exits anywhere, and what is left behind is a working app', () => {
  it('quitting mid-tour leaves every surface reachable', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    act(() => { requestProductTour() })

    await atStop('rail')
    await user.click(next())
    await atStop('chat')
    await user.click(next())
    await atStop('inbox')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(tour()).toBeNull())

    await user.click(within(rail()).getByRole('button', { name: 'Chat' }))
    await waitFor(() => expect(document.querySelector('[data-tour="chat"]')).not.toBeNull(), {
      timeout: STOP_BUDGET_MS,
    })
  })

  it('the X and a click on the overlay are the pointer twins of Escape', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))

    act(() => { requestProductTour() })
    await atStop('rail')
    await user.click(screen.getByRole('button', { name: 'End the tour' }))
    await waitFor(() => expect(tour()).toBeNull())

    act(() => { requestProductTour() })
    await atStop('rail')
    await user.click(document.querySelector<HTMLElement>('[data-tour-shield]')!)
    await waitFor(() => expect(tour()).toBeNull())
  })
})

describe('nothing about the tour is stored, and nothing is reported', () => {
  it('walking every stop asks the gateway for nothing and writes no key', async () => {
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    expect(calls.length).toBeGreaterThan(0)

    const before = calls.length
    const keysBefore = { ...localStorage }
    act(() => { requestProductTour() })
    await atStop('rail')
    for (let n = 0; n < 4; n += 1) await user.click(next())
    await atStop('settings')
    await user.click(screen.getByRole('button', { name: /Done/ }))

    const during = calls.slice(before)
    expect(during.filter((c) => /tour/i.test(c))).toEqual([])
    expect(during.filter((c) => /onboarding/i.test(c))).toEqual([])

    const newKeys = Object.keys({ ...localStorage }).filter((k) => !(k in keysBefore))
    expect(newKeys.filter((k) => /tour/i.test(k))).toEqual([])
    expect(Object.keys({ ...sessionStorage }).filter((k) => /tour/i.test(k))).toEqual([])
  })

  it('the tour modules import no gateway client and touch no storage', () => {
    const SRC = join(process.cwd(), "src")
    for (const rel of ['features/onboarding/ProductTour.tsx', 'features/onboarding/tourLaunch.ts', 'shared/ui/SpotlightTour.tsx']) {
      const src = readFileSync(join(SRC, rel), 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
      expect(/from '[^']*lib\/api'/.test(src), `${rel} must not import the gateway client`).toBe(false)
      expect(/\bfetch\s*\(/.test(src), `${rel} must not call fetch`).toBe(false)
      expect(/localStorage|sessionStorage/.test(src), `${rel} must not persist anything`).toBe(false)
    }
  })
})

describe("OU-5's auto-pin model behaves identically with the tour present", () => {
  it('a full walk leaves the disclosure record untouched', async () => {
    setNavMode('starter')
    const user = userEvent.setup()
    renderApp()
    await waitFor(() => expect(railLinks()).toContain('Chat'))
    const before = readNavDisclosure()

    act(() => { requestProductTour() })
    await atStop('rail')
    for (let n = 0; n < 4; n += 1) await user.click(next())
    await atStop('settings')
    await user.click(screen.getByRole('button', { name: /Done/ }))
    await waitFor(() => expect(tour()).toBeNull())

    expect(readNavDisclosure()).toEqual(before)
    expect(readNavDisclosure().pinned).toEqual([])
    expect(railLinks()).not.toContain('Tools')
  })

  it('and auto-pin still WORKS — the assertion above is not a dead mechanism', async () => {
    setNavMode('starter')
    location.hash = '#/tools'
    renderApp()
    expect(await screen.findByRole('heading', { name: 'Tools', level: 1 })).toBeInTheDocument()
    await waitFor(() => expect(readNavDisclosure().pinned).toContain('tools'))
  })
})

describe('Discover is the replay entry, and it cannot be lost', () => {
  it('replays the tour from the hub', async () => {
    ENVELOPES.discover = { enabled: true, visible_count: 0, areas: [] }
    location.hash = '#/discover'
    const user = userEvent.setup()
    renderApp()

    const start = await screen.findByRole('button', { name: 'Start the tour' })
    await user.click(start)
    await atStop('rail')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(tour()).toBeNull())
    await waitFor(() => expect(screen.getByRole('button', { name: 'Start the tour' })).toHaveFocus())
  })

  it('is there for a user who dismissed everything', async () => {
    ENVELOPES.discover = { enabled: true, visible_count: 0, areas: [] }
    location.hash = '#/discover'
    renderApp()
    expect(await screen.findByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
    expect(await screen.findByText(/No Discover tips to show/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Dismiss/ })).toBeNull()
  })

  it('is there even when Discover itself is switched off', async () => {
    ENVELOPES.discover = { enabled: false, visible_count: 0, areas: [] }
    location.hash = '#/discover'
    renderApp()
    expect(await screen.findByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
    expect(await screen.findByText('Discover is off')).toBeInTheDocument()
  })

  it('survives a failed tips fetch, which is when a lost user needs it most', async () => {
    ENVELOPES.discover = REJECT
    location.hash = '#/discover'
    renderApp()
    expect(await screen.findByRole('button', { name: 'Start the tour' })).toBeInTheDocument()
  })
})
