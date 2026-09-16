import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'
import { useState } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Compass, Inbox } from 'lucide-react'
import { SpotlightTour, type SpotlightStep } from './SpotlightTour'


const STEPS: SpotlightStep[] = [
  { id: 'one', anchor: 'one', icon: Compass, title: 'The first thing', body: 'What the first thing is for.' },
  { id: 'two', anchor: 'two', icon: Inbox, title: 'The second thing', body: 'What the second thing is for.' },
]

const REAL_RECT = Element.prototype.getBoundingClientRect
function stubLayout() {
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    return { x: 40, y: 80, top: 80, left: 40, width: 200, height: 120, right: 240, bottom: 200, toJSON: () => ({}) } as DOMRect
  }
}

let behindClicks = 0

function Host({ anchors = ['one'] }: { anchors?: string[] }) {
  const [i, setI] = useState<number | null>(null)
  return (
    <div>
      {anchors.map((a) => <div key={a} data-tour={a}>anchor {a}</div>)}
      <button type="button" onClick={() => setI(0)}>Launch the tour</button>
      <button type="button" onClick={() => { behindClicks += 1 }}>Behind the overlay</button>
      {i !== null && (
        <SpotlightTour steps={STEPS} index={i} label="Test tour"
          onIndex={setI} onExit={() => setI(null)} />
      )}
    </div>
  )
}

const dialog = () => screen.queryByRole('dialog')
const launch = () => screen.getByRole('button', { name: 'Launch the tour' })

beforeEach(() => { behindClicks = 0; stubLayout() })
afterEach(() => { Element.prototype.getBoundingClientRect = REAL_RECT })

describe('a stop points at a real element and names itself', () => {
  it('resolves the anchor and reports the stop', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())

    const d = await screen.findByRole('dialog')
    expect(d).toHaveAttribute('data-tour-step', 'one')
    expect(d).toHaveAttribute('data-tour-anchored', 'true')
    expect(screen.getByText('What the first thing is for.')).toBeInTheDocument()
  })

  it('the step and its position ride the accessible NAME', () => {
    render(<Host />)
    return userEvent.setup().click(launch()).then(async () => {
      const d = await screen.findByRole('dialog')
      expect(d.getAttribute('aria-label')).toBe('Test tour — step 1 of 2: The first thing')
    })
  })

  it('a stop whose surface never mounted keeps its card and admits it', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await user.click(screen.getByRole('button', { name: /Next/ }))

    const d = await screen.findByRole('dialog')
    expect(d).toHaveAttribute('data-tour-step', 'two')
    expect(screen.getByText('What the second thing is for.')).toBeInTheDocument()
    await waitFor(() => expect(d).toHaveAttribute('data-tour-anchored', 'false'))
  })
})

describe('aria-modal is kept, not just declared', () => {
  it('declares modality and owns focus', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())

    const d = await screen.findByRole('dialog')
    expect(d).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(d.contains(document.activeElement)).toBe(true))
  })

  it('Tab cannot reach the page behind the dim', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    const d = await screen.findByRole('dialog')

    for (let n = 0; n < 6; n += 1) {
      await user.tab()
      expect(d.contains(document.activeElement)).toBe(true)
    }
  })

  it('focus returns to whatever opened it', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await screen.findByRole('dialog')

    await user.keyboard('{Escape}')
    await waitFor(() => expect(dialog()).toBeNull())
    expect(launch()).toHaveFocus()
  })

  it('re-takes focus on every advance', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    const d = await screen.findByRole('dialog')

    launch().focus()
    expect(launch()).toHaveFocus()
    await user.click(screen.getByRole('button', { name: /Next/ }))
    await waitFor(() => expect(d).toHaveAttribute('data-tour-step', 'two'))
    expect(d).toHaveFocus()
  })
})

describe('every way out', () => {
  it('Escape exits from a LATER stop too, not just the first', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await user.click(screen.getByRole('button', { name: /Next/ }))
    await waitFor(() => expect(dialog()).toHaveAttribute('data-tour-step', 'two'))

    await user.keyboard('{Escape}')
    await waitFor(() => expect(dialog()).toBeNull())
  })

  it('a click on the overlay ends the tour instead of being swallowed', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await screen.findByRole('dialog')

    const shield = document.querySelector<HTMLElement>('[data-tour-shield]')
    expect(shield, 'the overlay must have exactly one pointer-catching layer').not.toBeNull()
    await user.click(shield!)
    await waitFor(() => expect(dialog()).toBeNull())

    await user.click(screen.getByRole('button', { name: 'Behind the overlay' }))
    expect(behindClicks).toBe(1)
  })

  it('the dim panels never catch the pointer — only the shield does', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await screen.findByRole('dialog')

    const dims = [...document.querySelectorAll<HTMLElement>('.bg-canvas\\/70')]
    expect(dims.length, 'the anchored stop draws four dim bands').toBe(4)
    for (const el of dims) expect(el.className).toContain('pointer-events-none')
  })

  it('Done on the last stop exits', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await user.click(screen.getByRole('button', { name: /Next/ }))
    await waitFor(() => expect(dialog()).toHaveAttribute('data-tour-step', 'two'))

    expect(screen.queryByRole('button', { name: /Next/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Done/ }))
    await waitFor(() => expect(dialog()).toBeNull())
  })

  it('Back is offered from the second stop and not the first', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    expect(screen.queryByRole('button', { name: /Back/ })).toBeNull()
    await user.click(screen.getByRole('button', { name: /Next/ }))
    expect(await screen.findByRole('button', { name: /Back/ })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Back/ }))
    await waitFor(() => expect(dialog()).toHaveAttribute('data-tour-step', 'one'))
  })
})

describe('the ambient halo (the motion-allowed half of the reduced-motion pair)', () => {
  it('pulses around the ring while motion is allowed', async () => {
    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await screen.findByRole('dialog')
    await waitFor(() => expect(document.querySelector('[data-tour-halo]')).not.toBeNull())
  })
})

describe('the overlay owns no state of its own', () => {
  it('writes nothing to storage and asks for nothing', async () => {
    const fetchSpy = vi.fn(() => Promise.resolve(new Response('{}')))
    vi.stubGlobal('fetch', fetchSpy)
    localStorage.clear(); sessionStorage.clear()

    const user = userEvent.setup()
    render(<Host />)
    await user.click(launch())
    await user.click(screen.getByRole('button', { name: /Next/ }))
    await user.click(screen.getByRole('button', { name: /Done/ }))

    expect(fetchSpy).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    vi.unstubAllGlobals()
  })
})
