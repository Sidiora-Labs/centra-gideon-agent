import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, findByRole, fireEvent, waitFor } from '@testing-library/react'
import { ApiError } from '../../shared/data/api'


const createKnowledgeItem = vi.fn()
const knowledgeSearchForContext = vi.fn()
const createSchedule = vi.fn()
const runSchedule = vi.fn()
const notifications = vi.fn()
const createULoop = vi.fn()
const uLoopAction = vi.fn()

vi.mock('../../shared/data/api', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  api: {
    createKnowledgeItem: (...a: unknown[]) => createKnowledgeItem(...a),
    knowledgeSearchForContext: (...a: unknown[]) => knowledgeSearchForContext(...a),
    createSchedule: (...a: unknown[]) => createSchedule(...a),
    runSchedule: (...a: unknown[]) => runSchedule(...a),
    notifications: () => notifications(),
    createULoop: (...a: unknown[]) => createULoop(...a),
    uLoopAction: (...a: unknown[]) => uLoopAction(...a),
  },
}))

import { TryOneStep } from './TryOneStep'
import { isProviderFailure, settingsTargetFor, failureText, LOOP_SEED } from './tryOneFlows'

const onProgress = vi.fn()
const onExitTo = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  createSchedule.mockResolvedValue({ ok: true, trigger: { raw_id: 'r', schedule: 'At 09:00 AM', next_run_ts: 1 } })
  runSchedule.mockResolvedValue({ ok: true, result: 'ran' })
  notifications.mockResolvedValue({ notifications: [], unread: 0 })
  createULoop.mockResolvedValue({ id: 'lp-1', status: 'ready', task: LOOP_SEED.task, max_cycles: 1 })
  uLoopAction.mockResolvedValue({ id: 'lp-1', status: 'running', task: LOOP_SEED.task, max_cycles: 1 })
})

function mount() {
  render(<TryOneStep onProgress={onProgress} onDone={vi.fn()} onSkip={vi.fn()} onExitTo={onExitTo} />)
}

describe('classification: which failures are the provider refusing a real call', () => {
  it('the HTTP statuses a refused credential comes back as', () => {
    for (const s of [401, 402, 403]) expect(isProviderFailure('upstream said no', s)).toBe(true)
  })

  it('the provider vocabulary, from the words the gateway actually forwards', () => {
    const real = [
      'Incorrect API key provided: sk-***',
      'Unauthorized',
      'authentication_error: invalid x-api-key',
      'You exceeded your current quota, please check your plan and billing details',
      'Rate limit reached for gpt-5 in organization org-x',
      'The model `gpt-5-preview` does not exist or you do not have access to it',
      'No provider entries registered',
      'no chat model is bound',
    ]
    for (const m of real) expect(isProviderFailure(m), m).toBe(true)
  })

  it('matches the snake_case error CODES too, not just the prose', () => {
    const codes = [
      'insufficient_quota', 'invalid_api_key', 'authentication_error',
      'rate_limit_exceeded', 'model_not_found', 'billing_hard_limit_reached',
    ]
    for (const c of codes) expect(isProviderFailure(c), c).toBe(true)
  })

  it('does NOT claim provider trouble for an unrelated server fault', () => {
    for (const m of ['Task is too short', 'invalid channel ID format', 'HTTP 500', 'disk is full']) {
      expect(isProviderFailure(m), m).toBe(false)
    }
  })

  it('routes each class to a REAL Settings subpage id', () => {
    expect(settingsTargetFor('Incorrect API key provided', 500).path).toBe('settings/providers')
    expect(settingsTargetFor('upstream refused', 401).path).toBe('settings/providers')
    expect(settingsTargetFor('disk is full', 500).path).toBe('settings/doctor')
  })

  it('never invents a message when the error carried none', () => {
    expect(failureText(new Error(''))).toBe('The call failed without a message.')
    expect(failureText(new ApiError('Incorrect API key provided', 401))).toBe('Incorrect API key provided')
  })
})

describe('a real call refused after a passing Test', () => {
  const REFUSED = 'Incorrect API key provided: sk-***. You can find your API key at the provider dashboard.'

  it('shows the provider’s sentence verbatim', async () => {
    createULoop.mockRejectedValue(new ApiError(REFUSED, 401))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain(REFUSED)
  })

  it('offers a Settings deep-link into the surface that owns the credential', async () => {
    createULoop.mockRejectedValue(new ApiError(REFUSED, 401))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    const link = await screen.findByRole('button', { name: 'Open model provider settings' })
    fireEvent.click(link)
    expect(onExitTo).toHaveBeenCalledWith('settings/providers')
  })

  it('names the passing-Test situation, so the user is not left doubting the Test', async () => {
    createULoop.mockRejectedValue(new ApiError(REFUSED, 401))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    expect(await screen.findByText(/passed its test and then refused this call/)).toBeTruthy()
  })

  it('says that following the link finishes setup', async () => {
    createULoop.mockRejectedValue(new ApiError(REFUSED, 401))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    expect(await screen.findByText(/finishes setup and takes you there/)).toBeTruthy()
  })

  it('the card stays retryable in place — a fixed key should not need a reload', async () => {
    createULoop.mockRejectedValue(new ApiError(REFUSED, 401))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    const retry = await screen.findByRole('button', { name: 'Try again' })
    createULoop.mockResolvedValue({ id: 'lp-2', status: 'ready', task: LOOP_SEED.task, max_cycles: 1 })
    uLoopAction.mockResolvedValue({ id: 'lp-2', status: 'running', task: LOOP_SEED.task, max_cycles: 1 })
    fireEvent.click(retry)
    await waitFor(() => expect(onProgress).toHaveBeenCalledWith({ first_success: { loop: true } }))
    expect(screen.queryByRole('alert')).toBeNull()
  })
})

describe('every card carries the failure path, not just one', () => {
  const REFUSED = 'authentication_error: invalid x-api-key'
  const cases: { name: RegExp; arm: () => void }[] = [
    { name: /Save and ask/, arm: () => createKnowledgeItem.mockRejectedValue(new ApiError(REFUSED, 401)) },
    { name: /Create and fire once/, arm: () => createSchedule.mockRejectedValue(new ApiError(REFUSED, 401)) },
    { name: /Start it/, arm: () => createULoop.mockRejectedValue(new ApiError(REFUSED, 401)) },
  ]

  for (const c of cases) {
    it(`${String(c.name)} shows the error AND the deep-link`, async () => {
      c.arm()
      const { container } = render(
        <TryOneStep onProgress={onProgress} onDone={vi.fn()} onSkip={vi.fn()} onExitTo={onExitTo} />)
      fireEvent.click(screen.getByRole('button', { name: c.name }))
      const alert = await findByRole(container, 'alert')
      expect(alert.textContent).toContain(REFUSED)
      expect(await findByRole(container, 'button', { name: 'Open model provider settings' })).toBeTruthy()
    })
  }
})

describe('a non-provider failure is not blamed on the provider', () => {
  it('routes to Doctor and does not claim the Test was fine', async () => {
    createULoop.mockRejectedValue(new ApiError('Task is too short', 400))
    mount()
    fireEvent.click(screen.getByRole('button', { name: /Start it/ }))
    expect((await screen.findByRole('alert')).textContent).toContain('Task is too short')
    fireEvent.click(await screen.findByRole('button', { name: 'Open Settings → Doctor' }))
    expect(onExitTo).toHaveBeenCalledWith('settings/doctor')
    expect(screen.queryByText(/passed its test and then refused/)).toBeNull()
  })
})
