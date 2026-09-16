import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { degradedReading, degradedPresentation, initialDegradedReading } from './statusSurfaceState'


const boom = () => Promise.reject(new Error('resilience read failed'))
const degraded = { surfaces: [{ surface: 'chat', available: false, backlog: 2, floor: 'keyword search', use_case: 'chat' }] }
const healthy = { surfaces: [{ surface: 'chat', available: true, backlog: 0, floor: '', use_case: 'chat' }] }

function mockApi(over: Record<string, unknown>) {
  vi.doMock('../data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      degraded: () => Promise.resolve(healthy),
      onboarding: () => Promise.resolve({ has_model_provider: true }),
      ...over,
    },
  }))
}

async function mount() {
  const { DegradedChip } = await import('./DegradedChip')
  render(<DegradedChip />)
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the degraded chip cannot stay silent about a check it could not read', () => {
  it('says the status is unknown when the read has never answered', async () => {
    mockApi({ degraded: boom })
    await mount()
    const chip = await waitFor(() => screen.getByRole('button', { name: /Status unknown/i }))
    expect(chip.getAttribute('title'), 'and says what that means').toMatch(/could not be read/i)
    expect(chip.textContent, 'no invented fault').not.toMatch(/degraded/i)
  })

  it('stays silent when every surface really does have a model', async () => {
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.queryByRole('button')).toBeNull())
  })

  it('still reports a real degradation, with its count', async () => {
    mockApi({ degraded: () => Promise.resolve(degraded) })
    await mount()
    await waitFor(() => expect(screen.getByRole('button', { name: /Chat degraded/i })).toBeInTheDocument())
    expect(screen.queryByText(/Status unknown/), 'a measured fault is not "unknown"').toBeNull()
  })

  it('the popover explains the unknown instead of opening on an empty list', async () => {
    mockApi({ degraded: boom })
    await mount()
    fireEvent.click(await waitFor(() => screen.getByRole('button', { name: /Status unknown/i })))
    const dialog = await waitFor(() => screen.getByRole('dialog'))
    expect(dialog.textContent).toMatch(/Could not read the check/i)
    expect(dialog.textContent, 'says what it cannot tell').toMatch(/cannot say whether any surface/i)
    expect(dialog.textContent, 'and that it self-heals').toMatch(/clear itself when the check responds/i)
  })
})

describe('the reading model keeps the two states apart', () => {
  it('the read records its rejection without dropping the last successful answer', () => {
    const known = degradedReading(initialDegradedReading, { type: 'surfaces', surfaces: degraded.surfaces as never })
    const failed = degradedReading(known, { type: 'failure' })
    expect(failed.failed).toBe(true)
    expect(failed.surfaces).toBe(known.surfaces)
  })

  it('unknown requires BOTH never-answered and a failing read', () => {
    expect(degradedPresentation(initialDegradedReading).unknown).toBe(false)
    const failed = degradedReading(initialDegradedReading, { type: 'failure' })
    expect(degradedPresentation(failed).unknown).toBe(true)
    const recovered = degradedReading(failed, { type: 'surfaces', surfaces: healthy.surfaces as never })
    expect(recovered.failed).toBe(false)
    expect(degradedPresentation(recovered).unknown).toBe(false)
  })

  it('an empty-but-known result still renders nothing', () => {
    expect(degradedPresentation({ surfaces: [], failed: true, provider: null }).visible).toBe(false)
  })
})
