import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'


const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const gideonConfig = vi.fn()
const setAutoUpdate = vi.fn()
const setName = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    gideonConfig: () => gideonConfig(),
    setAutoUpdate: (...a: unknown[]) => setAutoUpdate(...a),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('../../app/shell/identity', () => ({
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
  DEFAULT_USER_NAME: 'Operator',
}))
vi.mock('../../shared/ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('./ImportStep', () => ({
  ImportStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-import</button>
  ),
}))
vi.mock('./EssentialsStep', () => ({
  EssentialsStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip</button>
  ),
}))
vi.mock('./TryOneStep', () => ({
  TryOneStep: ({ onSkip }: { onSkip: () => void }) => (
    <button type="button" onClick={onSkip}>stub-skip-try</button>
  ),
}))

import { Onboarding } from '../../app/shell/Onboarding'
import { AppearanceProvider } from '../../app/shell/appearance'
import { clearOnboardingExit, peekOnboardingExit } from './exitTo'

const ORIGINAL_MATCH_MEDIA = window.matchMedia

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(window, 'matchMedia', {
    configurable: true, writable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }),
  })
  localStorage.clear()
  clearOnboardingExit()
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
  gideonConfig.mockResolvedValue({ updates: { auto: 'staged' }, apps: { registry_source_enabled: true } })
  setAutoUpdate.mockResolvedValue({ ok: true })
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

async function reachDoneScreen() {
  render(<AppearanceProvider><Onboarding /></AppearanceProvider>)
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
  await screen.findByRole('button', { name: /Start using/ })
}

describe('the done screen tells what the product does on its own', () => {
  it('hands over the real staged-update switch, reflecting the config', async () => {
    await reachDoneScreen()
    const sw = await screen.findByRole('switch', { name: 'Update automatically (staged)' })
    expect(sw).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(sw)
    expect(setAutoUpdate).toHaveBeenCalledWith('off')
    expect(sw).toHaveAttribute('aria-checked', 'false')
  })

  it('an install that never opted in shows the switch off — staged is opt-in', async () => {
    gideonConfig.mockResolvedValue({ auto_update: true, apps: { registry_source_enabled: true } })
    await reachDoneScreen()
    const sw = await screen.findByRole('switch', { name: 'Update automatically (staged)' })
    expect(sw).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(sw)
    expect(setAutoUpdate).toHaveBeenCalledWith('staged')
  })

  it('a refused write tells through the app toast and does not fight the switch', async () => {
    setAutoUpdate.mockRejectedValue(new Error('nope'))
    const toasts: string[] = []
    const onToast = (e: Event) => toasts.push((e as CustomEvent<{ message: string }>).detail.message)
    window.addEventListener('ne:toast', onToast)
    try {
      await reachDoneScreen()
      fireEvent.click(await screen.findByRole('switch', { name: 'Update automatically (staged)' }))
      await waitFor(() => expect(toasts.some((m) => m.includes("Couldn't disable automatic updates"))).toBe(true))
      expect(screen.getByRole('switch', { name: 'Update automatically (staged)' })).toHaveAttribute('aria-checked', 'false')
    } finally {
      window.removeEventListener('ne:toast', onToast)
    }
  })

  it('names configured Store sources and routes to where removal persists', async () => {
    await reachDoneScreen()
    await screen.findByText(/sources configured for this gateway/)
    fireEvent.click(screen.getByRole('button', { name: 'Review Store sources' }))
    expect(peekOnboardingExit()).toBe('apps')
  })

  it('stays quiet about a source a pre-provisioned opt-out never got', async () => {
    gideonConfig.mockResolvedValue({ updates: { auto: 'staged' }, apps: { registry_source_enabled: false } })
    await reachDoneScreen()
    await screen.findByRole('switch', { name: 'Update automatically (staged)' })
    expect(screen.queryByText(/sources configured for this gateway/)).toBeNull()
  })

  it('an unreadable config withholds the switch and offers the Settings path', async () => {
    gideonConfig.mockRejectedValue(new Error('boom'))
    await reachDoneScreen()
    const link = await screen.findByRole('button', { name: 'Manage updates in Settings' })
    expect(screen.queryByRole('switch', { name: 'Update automatically (staged)' })).toBeNull()
    fireEvent.click(link)
    expect(peekOnboardingExit()).toBe('settings/updates')
  })
})
