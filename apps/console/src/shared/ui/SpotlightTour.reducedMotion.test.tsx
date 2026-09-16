
import { describe, expect, it, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { Compass } from 'lucide-react'
import { SpotlightTour, type SpotlightStep } from './SpotlightTour'

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    matches: query.includes('prefers-reduced-motion'),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

const STEPS: SpotlightStep[] = [
  { id: 'one', anchor: 'one', icon: Compass, title: 'The first thing', body: 'What the first thing is for.' },
]

const REAL_RECT = Element.prototype.getBoundingClientRect
beforeEach(() => {
  Element.prototype.getBoundingClientRect = function (): DOMRect {
    return { x: 40, y: 80, top: 80, left: 40, width: 200, height: 120, right: 240, bottom: 200, toJSON: () => ({}) } as DOMRect
  }
})
afterEach(() => { Element.prototype.getBoundingClientRect = REAL_RECT })

function mount() {
  return render(
    <>
      <div data-tour="one">anchor one</div>
      <SpotlightTour steps={STEPS} index={0} label="Test tour" onIndex={() => {}} onExit={() => {}} />
    </>,
  )
}

describe('under prefers-reduced-motion the spotlight is a still frame', () => {
  it('draws NO pulsing halo', async () => {
    mount()
    await screen.findByRole('dialog')
    await waitFor(() => expect(document.querySelector('[data-tour-halo]')).toBeNull())
  })

  it('still spotlights the anchor — this is a frozen frame, not a blank overlay', async () => {
    mount()
    const d = await screen.findByRole('dialog')
    expect(d).toHaveAttribute('data-tour-anchored', 'true')
    await waitFor(() => {
      const rings = [...document.querySelectorAll<HTMLElement>('.fixed.rounded-lg')]
        .filter((el) => (el.getAttribute('style') ?? '').includes('--color-primary'))
      expect(rings.length, 'the static outline ring must still be drawn').toBe(1)
    })
    expect(document.querySelectorAll('.bg-canvas\\/70').length).toBe(4)
  })

  it('still teaches — the card, its copy and its controls are untouched', async () => {
    mount()
    await screen.findByRole('dialog')
    expect(screen.getByText('The first thing')).toBeInTheDocument()
    expect(screen.getByText('What the first thing is for.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'End the tour' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Done/ })).toBeInTheDocument()
  })

  it('no node in the overlay carries an animation class', async () => {
    mount()
    const d = await screen.findByRole('dialog')
    const overlay = d.parentElement!
    const animated = [...overlay.querySelectorAll<HTMLElement>('*')]
      .filter((el) => /\banimate-|\banimation:/.test(el.className + (el.getAttribute('style') ?? '')))
    expect(animated.map((el) => el.className), 'these nodes still animate').toEqual([])
  })
})
