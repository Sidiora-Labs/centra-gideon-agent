
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

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

const { EntranceGroup, EntranceRegion } = await import('./Entrance')

function Surface({ onPoke = () => {} }: { onPoke?: () => void }) {
  return (
    <EntranceGroup className="flex flex-col">
      <EntranceRegion><p>first region</p></EntranceRegion>
      <EntranceRegion>
        <button type="button" onClick={onPoke}>poke</button>
      </EntranceRegion>
      <EntranceRegion><p>third region</p></EntranceRegion>
    </EntranceGroup>
  )
}

const group = () => document.querySelector<HTMLElement>('[data-entrance]')!
const regions = () => [...document.querySelectorAll<HTMLElement>('[data-entrance-region]')]

describe('under prefers-reduced-motion the surface just IS', () => {
  it('the group takes the no-entrance branch', () => {
    render(<Surface />)
    expect(group()).toHaveAttribute('data-entrance', 'none')
    expect(regions()).toHaveLength(3)
    for (const r of regions()) expect(r).toHaveAttribute('data-entrance-region', 'none')
  })

  it('nothing carries an initial hidden state or a transform', () => {
    render(<Surface />)
    for (const el of [group(), ...regions()]) {
      expect(el.style.opacity, `${el.dataset.entrance ?? el.dataset.entranceRegion} still starts hidden`).toBe('')
      expect(el.style.transform).toBe('')
    }
  })

  it('no node under the group animates at all', () => {
    render(<Surface />)
    const animated = [...group().querySelectorAll<HTMLElement>('*')]
      .filter((el) => /\banimate-|\banimation:|\btransition:/.test(el.className + (el.getAttribute('style') ?? '')))
    expect(animated.map((el) => el.outerHTML), 'these nodes still animate').toEqual([])
  })

  it('still renders every region and its controls still work', () => {
    const onPoke = vi.fn()
    render(<Surface onPoke={onPoke} />)
    expect(screen.getByText('first region')).toBeInTheDocument()
    expect(screen.getByText('third region')).toBeInTheDocument()
    const poke = screen.getByRole('button', { name: 'poke' })
    poke.click()
    expect(onPoke).toHaveBeenCalledTimes(1)
  })

  it('the group keeps the layout classes the surface handed it', () => {
    render(<Surface />)
    expect(group().className).toBe('flex flex-col')
  })
})
