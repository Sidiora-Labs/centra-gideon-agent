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
    gideonConfig: () => new Promise(() => {}),
    theme: () => new Promise(() => {}),
  },
}))
vi.mock('./identity', async (importOriginal) => ({
  ...await importOriginal<typeof import('./identity')>(),
  useIdentity: () => ({ setName }),
  firstNameOf: (n: string) => n.split(' ')[0],
  DEFAULT_USER_NAME: 'Operator',
}))
vi.mock('../../shared/ui/DotGlow', () => ({ DotGlow: () => null }))
vi.mock('../../features/onboarding/ImportStep', () => ({
  ImportStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('2 imported')}>stub-imported</button>
      <button type="button" onClick={onSkip}>stub-skip-import</button>
    </div>
  ),
}))
vi.mock('../../features/onboarding/EssentialsStep', () => ({
  EssentialsStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('gpt-5')}>stub-continue</button>
      <button type="button" onClick={onSkip}>stub-skip</button>
    </div>
  ),
}))
vi.mock('../../features/onboarding/TryOneStep', () => ({
  TryOneStep: ({ onDone, onSkip }: { onDone: (s: string) => void; onSkip: () => void }) => (
    <div>
      <button type="button" onClick={() => onDone('1 of 3 tried')}>stub-tried</button>
      <button type="button" onClick={onSkip}>stub-skip-try</button>
    </div>
  ),
}))

import { Onboarding } from './Onboarding'
import { AppearanceProvider } from './appearance'
import { readNavDisclosure } from './navDisclosure'

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
  saveOnboardingState.mockResolvedValue({ ok: true, state: {} })
  onboarding.mockResolvedValue({ needs_model: true, has_model_provider: false, has_chat_binding: false })
})

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: ORIGINAL_MATCH_MEDIA })
})

function renderFlow() {
  return render(<AppearanceProvider><Onboarding /></AppearanceProvider>)
}

async function enterName() {
  renderFlow()
  await waitFor(() => expect(onboarding).toHaveBeenCalled())
  fireEvent.change(screen.getByPlaceholderText('Your name'), { target: { value: 'Ada Lovelace' } })
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
}

async function enterNameAndImport() {
  await enterName()
  fireEvent.click(await screen.findByRole('button', { name: 'stub-imported' }))
}


describe('every step transition persists its resume point', () => {
  it('records nothing for the import step, then `essentials` when it is left', async () => {
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
    expect(saveOnboardingState).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'stub-imported' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `essentials` when the import step is SKIPPED too', async () => {
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-import' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'essentials' }))
  })

  it('records `first_success` when the essentials step is completed', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `first_success` when the essentials step is SKIPPED too', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip' }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'first_success' }))
  })

  it('records `done` and commits the name LAST', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' }))
    expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace')
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success', 'done'])
  })

  it('leaving the first-success step does NOT invent a fourth resume point', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    const steps = saveOnboardingState.mock.calls.map(([p]) => p.step)
    expect(steps).toEqual(['essentials', 'first_success'])
  })

  it('skipping the first-success step reaches the recap too', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
  })

  it('writes only the `step` key — no lane progress the shell did not observe', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    for (const [patch] of saveOnboardingState.mock.calls) expect(Object.keys(patch)).toEqual(['step'])
  })
})

describe('finishing marks the install as onboarded under THIS version (OU-5 / C4)', () => {
  async function finishFlow() {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    fireEvent.click(await screen.findByRole('button', { name: /Start using/ }))
  }

  it('writes the starter-rail marker', async () => {
    localStorage.clear()
    expect(readNavDisclosure().mode).toBe('expert')
    await finishFlow()
    await waitFor(() => expect(readNavDisclosure().mode).toBe('starter'))
  })

  it('leaves already-earned pins alone', async () => {
    localStorage.setItem('nav-disclosure', JSON.stringify({ mode: 'expert', pinned: ['tools'] }))
    await finishFlow()
    await waitFor(() => expect(readNavDisclosure()).toEqual({ mode: 'starter', pinned: ['tools'] }))
  })
})


describe('re-entering the flow resumes at the persisted step', () => {
  it('lands on the try-one step when the home stopped at first_success', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: false, trigger: false, loop: false },
    })
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-tried' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'stub-continue' })).toBeNull()
  })

  it('does not walk the stored resume point backwards', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true, step: 'first_success',
    })
    await enterName()
    await waitFor(() => expect(saveOnboardingState).toHaveBeenCalled())
    expect(saveOnboardingState.mock.calls.map(([p]) => p.step)).toEqual(['first_success'])
  })

  it('restates what the earlier visit set up, checked against live readiness', async () => {
    onboarding.mockResolvedValue({
      needs_model: false, has_model_provider: true, has_chat_binding: true,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
      first_success: { knowledge: true, trigger: false, loop: false },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText('anthropic-models')).toBeTruthy()
    expect(screen.getByText('Chat model: anthropic-models')).toBeTruthy()
    expect(screen.getByText(/1 of 3 tried/, { selector: 'p' })).toBeTruthy()
  })

  it('does not promise a model the home no longer resolves', async () => {
    onboarding.mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false,
      step: 'first_success', essentials: { model: 'anthropic-models', search: false, speech: false, channel: null },
    })
    await enterName()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByText(/Chat model — set up later in Settings/)).toBeTruthy()
    expect(screen.queryByText(/Chat model: anthropic-models/)).toBeNull()
  })

  it('starts a completed home over instead of dropping it on the recap', async () => {
    onboarding.mockResolvedValue({
      needs_model: true, has_model_provider: false, has_chat_binding: false, step: 'done',
    })
    await enterName()
    expect(await screen.findByRole('button', { name: 'stub-imported' })).toBeTruthy()
  })
})

describe('skip at any step lands in a working dashboard', () => {
  it('skips from the FIRST step, committing the shared default name', async () => {
    localStorage.clear()
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    fireEvent.click(screen.getByRole('button', { name: /^Skip setup/ }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Operator'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
    expect(readNavDisclosure().mode).toBe('starter')
  })

  it('names the default it will use, rather than renaming you silently', async () => {
    renderFlow()
    await waitFor(() => expect(onboarding).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: /Skip setup — start as Operator/ })).toBeTruthy()
  })

  it('skips from a MIDDLE step, keeping the name that was typed', async () => {
    await enterNameAndImport()
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip setup and go to the dashboard' }))
    await waitFor(() => expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace'))
    expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  })

  it('offers no skip on the last step — "Start using" is the door', async () => {
    await enterNameAndImport()
    fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Skip setup/ })).toBeNull()
  })
})

describe('a failed progress write costs the user nothing', () => {
  it('still advances when the resume-point POST rejects', async () => {
    saveOnboardingState.mockRejectedValue(new Error('gateway down'))
    await enterNameAndImport()
    expect(await screen.findByRole('button', { name: 'stub-continue' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'stub-continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'stub-tried' }))
    expect(await screen.findByRole('button', { name: /Start using/ })).toBeTruthy()
  })
})

it('records the tour request before the completed flow releases the identity gate', async () => {
  const { consumeProductTourRequest } = await import('../../features/onboarding/tourLaunch')
  consumeProductTourRequest()
  await enterNameAndImport()
  fireEvent.click(await screen.findByRole('button', { name: 'stub-continue' }))
  fireEvent.click(await screen.findByRole('button', { name: 'stub-skip-try' }))
  fireEvent.click(await screen.findByRole('button', { name: /Take the quick tour/ }))
  expect(consumeProductTourRequest()).toBe(true)
  expect(saveOnboardingState).toHaveBeenCalledWith({ step: 'done' })
  expect(setName).toHaveBeenCalledWith('Ada Lovelace', 'ada-lovelace')
})
