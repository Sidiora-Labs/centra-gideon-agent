import { expect, it, vi } from 'vitest'
import { act, render, screen } from '@testing-library/react'

vi.stubGlobal('matchMedia', (q: string) => ({
  matches: q.includes('prefers-reduced-motion'),
  media: q,
  onchange: null,
  addListener: () => {},
  removeListener: () => {},
  addEventListener: () => {},
  removeEventListener: () => {},
  dispatchEvent: () => false,
}))

const { ReadingView } = await import('./ReadingView')

function stubScroll(el: HTMLElement, scrollTop: number) {
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true })
  Object.defineProperty(el, 'scrollHeight', { value: 2000, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: 500, configurable: true })
}

it('the reading-progress arc is set directly, never sprung, under reduced motion', async () => {
  render(
    <ReadingView
      item={{ id: 'k1', title: 'A', content: 'Body text long enough to read.', item_type: 'note' } as never}
      annotations={[]}
      onAnnotationsChanged={() => {}}
    />,
  )
  const region = screen.getByRole('group', { name: 'Article body' })
  stubScroll(region, 0)
  await act(async () => {
    region.dispatchEvent(new Event('scroll'))
    await new Promise((r) => setTimeout(r, 30))
  })
  expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '0')

  const arc = () => document.querySelectorAll('circle')[1] as SVGCircleElement
  const seen = new Set<string>()
  stubScroll(region, 1500)
  await act(async () => { region.dispatchEvent(new Event('scroll')) })
  for (let i = 0; i < 25; i += 1) {
    await act(async () => { await new Promise((r) => setTimeout(r, 16)) })
    const v = arc().getAttribute('stroke-dashoffset')
    if (v != null) seen.add(Number(v).toFixed(2))
  }

  const empty = 2 * Math.PI * (22 / 2 - 2.5)
  const intermediates = [...seen]
    .map(Number)
    .filter((v) => Math.abs(v - empty) > 0.5 && Math.abs(v) > 0.5)
  expect(
    intermediates,
    `reduced motion must jump, not sweep. Samples: ${[...seen].join(', ')}`,
  ).toEqual([])
  expect(Number([...seen].pop())).toBeCloseTo(0, 1)
  expect(screen.getByRole('progressbar', { name: /Reading progress/ })).toHaveAttribute('aria-valuenow', '100')
})
