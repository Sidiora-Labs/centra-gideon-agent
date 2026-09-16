import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { DegradedChip } from './DegradedChip'
import { api } from '../data/api'


const SURFACES = [
  { surface: 'search_ranking', available: false, floor: 'Keyword ranking', backlog: 3 },
  { surface: 'inbox_classify', available: false, floor: 'Rules only', backlog: 0 },
]

const SURFACES_WITH_USE_CASES = [
  { surface: 'inbox_classify', available: false, floor: 'Rules only', backlog: 0, use_cases: ['chat'] },
  { surface: 'knowledge_enrich', available: false, floor: 'Documents still captured', backlog: 7, use_cases: ['embedding'] },
  { surface: 'voice_capture', available: false, floor: 'Text input keeps working', backlog: 0, use_cases: ['stt'] },
  { surface: 'future_thing', available: false, floor: 'Something still works', backlog: 0, use_cases: ['some_new_case'] },
]

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
  vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: SURFACES } as never)
  vi.spyOn(api, 'onboarding').mockResolvedValue({ has_model_provider: true } as never)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('DegradedChip width in the shell corner', () => {
  it('drops its text label at mobile width, keeping an accessible name', async () => {
    setViewport(true)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    expect(btn.textContent?.trim(), 'mobile chip must render no visible text').toBe('')
    expect(btn.getAttribute('aria-label')).toMatch(/degraded/i)
  })

  it('the tooltip agrees with its own count — singular at one surface', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: [SURFACES[0]] } as never)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    expect(btn.getAttribute('title')).toMatch(/1 surface running without a model/)
    expect(btn.getAttribute('title'), 'and not the hedge it replaced').not.toMatch(/surface\(s\)/)
  })

  it('and plural above one', async () => {
    vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces: SURFACES } as never)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    expect(btn.getAttribute('title')).toMatch(/2 surfaces running without a model/)
  })

  it('keeps its text label on desktop, where the corner has room', async () => {
    setViewport(false)
    render(<DegradedChip />)
    const btn = await waitFor(() => screen.getByRole('button', { name: /degraded/i }))
    expect(btn.textContent).toContain('2 degraded')
    expect(btn.getAttribute('aria-label')).toBeNull()
  })

  it('still renders nothing when every surface has a model', async () => {
    setViewport(true)
    vi.spyOn(api, 'degraded').mockResolvedValue({
      surfaces: [{ surface: 'search_ranking', available: true, floor: '', backlog: 0 }],
    } as never)
    const { container } = render(<DegradedChip />)
    await waitFor(() => expect(api.degraded).toHaveBeenCalled())
    expect(container.textContent).toBe('')
  })
})

async function openPopover(surfaces: unknown[]) {
  setViewport(false)
  vi.spyOn(api, 'degraded').mockResolvedValue({ surfaces } as never)
  const r = render(<DegradedChip />)
  await waitFor(() => expect(api.degraded).toHaveBeenCalled())
  await waitFor(() => expect(screen.getByRole('button')).toBeTruthy())
  screen.getByRole('button').click()
  await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
  return r
}

describe('the degraded popover names the missing use-case', () => {
  it('states what is missing beside what still works', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    const text = container.textContent ?? ''
    expect(text).toContain('No model for Chat')
    expect(text).toContain('Rules only')
  })

  it('uses the canonical use-case label, not the raw slug', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    expect(container.textContent).toContain('No model for Speech-to-text')
    expect(container.textContent).not.toContain('Stt')
  })

  it('names Embedding for the knowledge surface', async () => {
    expect((await openPopover(SURFACES_WITH_USE_CASES)).container.textContent)
      .toContain('No model for Embedding')
  })

  it('falls back to a prettified slug for a use-case the map does not know', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    expect(container.textContent).toContain('No model for Some new case')
  })

  it('renders no use-case line when the payload omits the field', async () => {
    const { container } = await openPopover(SURFACES)
    expect(container.textContent).toContain('Keyword ranking')
    expect(container.textContent).not.toContain('No model for')
  })

  it('renders no use-case line for an empty use_cases array', async () => {
    const { container } = await openPopover([
      { surface: 'x', available: false, floor: 'Still fine', backlog: 0, use_cases: [] },
    ])
    expect(container.textContent).toContain('Still fine')
    expect(container.textContent).not.toContain('No model for')
  })
})


const MODELS_LINK = 'a[href="#/settings/models"]'

describe('the degraded popover links to where you fix it', () => {
  it('offers exactly one actionable — the Models link', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    const dialog = screen.getByRole('dialog')
    expect(dialog.querySelectorAll('button, a').length,
      'the panel had zero actionables; it needs exactly one').toBe(1)
    const link = dialog.querySelector<HTMLAnchorElement>(MODELS_LINK)!
    expect(link, 'and that actionable is the Models link').not.toBeNull()
    expect(link.textContent).toContain('Settings')
    expect(link.textContent).toContain('Models')
    expect(link.getAttribute('aria-label')).toBeNull()
    expect(container.textContent).toContain('No model for Speech-to-text')
  })

  it('places the link ABOVE the first surface row, where a tall panel still shows it', async () => {
    await openPopover(SURFACES_WITH_USE_CASES)
    const link = screen.getByRole('dialog').querySelector(MODELS_LINK)!
    const firstRowName = screen.getByText('Inbox classify')
    expect(
      link.compareDocumentPosition(firstRowName) & Node.DOCUMENT_POSITION_FOLLOWING,
      'the link must precede the surface rows, or a long list pushes it off-screen',
    ).toBeTruthy()
  })

  it('closes the popover and its scrim on activate, and returns focus to the chip', async () => {
    const { container } = await openPopover(SURFACES_WITH_USE_CASES)
    const trigger = screen.getByRole('button')
    screen.getByRole('dialog').querySelector<HTMLAnchorElement>(MODELS_LINK)!.click()
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(container.querySelector('.fixed.inset-0'), 'the click-away scrim must go too').toBeNull()
    expect(document.activeElement, 'focus must not be dropped on <body>').toBe(trigger)
  })

  it('offers no link in the unknown state, where no fault has been measured', async () => {
    setViewport(false)
    vi.spyOn(api, 'degraded').mockRejectedValue(new Error('unreachable'))
    const { container } = render(<DegradedChip />)
    await waitFor(() => expect(screen.getByRole('button')).toBeTruthy())
    screen.getByRole('button').click()
    await waitFor(() => expect(screen.getByRole('dialog')).toBeTruthy())
    expect(container.textContent).toContain('Could not read the check')
    expect(container.querySelector(MODELS_LINK), 'no destination without a diagnosis').toBeNull()
  })
})

describe('setup-land: no provider has ever been configured', () => {
  it('trades the degraded count for a setup invitation, info-toned', async () => {
    setViewport(false)
    vi.spyOn(api, 'onboarding').mockResolvedValue({ has_model_provider: false } as never)
    render(<DegradedChip />)
    const chip = await screen.findByRole('button', { name: /set up a model/i })
    expect(chip.textContent).toContain('Set up a model')
    expect(chip.getAttribute('style')).toContain('--color-info')
    expect(chip.getAttribute('style')).not.toContain('--color-warn')
  })

  it('keeps the degraded vocabulary once a provider exists — a regression is not setup-land', async () => {
    setViewport(false)
    render(<DegradedChip />)
    await waitFor(() => expect(api.degraded).toHaveBeenCalled())
    const chip = await screen.findByTitle(/running without a model/i)
    expect(chip.textContent).toContain('degraded')
    expect(chip.getAttribute('style')).toContain('--color-warn')
  })

  it('a failing degraded check outranks setup-land — never a calm invitation over an unread state', async () => {
    setViewport(false)
    vi.spyOn(api, 'degraded').mockRejectedValue(new Error('boom'))
    vi.spyOn(api, 'onboarding').mockResolvedValue({ has_model_provider: false } as never)
    render(<DegradedChip />)
    const chip = await screen.findByTitle(/status unknown/i)
    expect(chip.textContent).toContain('Status unknown')
    expect(chip.textContent).not.toContain('Set up a model')
  })
})
