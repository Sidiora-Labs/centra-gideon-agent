import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'


const saveOnboardingState = vi.fn()
const onboarding = vi.fn()
const setName = vi.fn()

vi.mock('../../shared/data/api', () => ({
  api: {
    saveOnboardingState: (...a: unknown[]) => saveOnboardingState(...a),
    onboarding: () => onboarding(),
    themes: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
    gideonConfig: () => new Promise(() => {}),
  },
}))
vi.mock('../../app/shell/identity', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../app/shell/identity')>(),
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
import { readNavDisclosure } from '../../app/shell/navDisclosure'
import { peekOnboardingExit, clearOnboardingExit } from './exitTo'
import { runtime } from '../../shared/theme/runtime'

const ORIGINAL_MATCH_MEDIA = window.matchMedia
const DEFAULT_BOUNCINESS = runtime.bounciness

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
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
  runtime.bounciness = DEFAULT_BOUNCINESS
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

describe('the done screen points at the Inbox with a link that can leave the flow', () => {
  it('hands the destination to the route guard and finishes', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('button', { name: 'Open the Inbox instead' }))
    expect(peekOnboardingExit()).toBe('inbox')
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  })
})

describe('the done screen hands over the real Bounciness dial', () => {
  it('moving it reaches runtime.bounciness — the value every spring preset reads', async () => {
    await reachDoneScreen()
    const dial = screen.getByRole('slider', { name: 'Bounciness' })
    expect(dial).toHaveAttribute('max', '1')
    fireEvent.change(dial, { target: { value: '0' } })
    expect(runtime.bounciness).toBe(0)
  })
})

describe('the done screen unlock switch is the ONE nav-disclosure setting', () => {
  it('writes expert mode when it is on at finish', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('switch', { name: 'Show every surface' }))
    fireEvent.click(screen.getByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(readNavDisclosure().mode).toBe('expert'))
  })

  it('leaves the starter rail when it is off at finish', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(readNavDisclosure().mode).toBe('starter'))
  })

  it('writes nothing until the flow finishes', async () => {
    await reachDoneScreen()
    fireEvent.click(screen.getByRole('switch', { name: 'Show every surface' }))
    expect(localStorage.getItem('nav-disclosure')).toBeNull()
  })

  it('states what the switch will do, in both positions', async () => {
    await reachDoneScreen()
    expect(screen.getByText(/joins it the first time you open one/)).toBeTruthy()
    fireEvent.click(screen.getByRole('switch', { name: 'Show every surface' }))
    expect(screen.getByText(/every destination from the start/)).toBeTruthy()
  })
})
