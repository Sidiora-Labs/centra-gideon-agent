import { describe, expect, it } from 'vitest'
import { act, render } from '@testing-library/react'
import { ProgressRing } from './ProgressRing'


const C = 2 * Math.PI * 11.5

function offsetOf(container: HTMLElement): number {
  const arc = container.querySelectorAll('circle')[1] as SVGCircleElement
  const attr = arc.getAttribute('stroke-dashoffset')
  return attr == null ? NaN : Number(attr)
}

describe('the arc tweens between values rather than jumping', () => {
  it('passes through intermediate offsets after pct changes', async () => {
    const { container, rerender } = render(<ProgressRing label="Cycle progress" pct={0} tone={"var(--color-primary)"} />)

    expect(offsetOf(container as HTMLElement)).toBeCloseTo(C, 1)

    const seen = new Set<number>()
    await act(async () => {
      rerender(<ProgressRing label="Cycle progress" pct={1} tone={"var(--color-primary)"} />)
    })
    for (let i = 0; i < 40; i++) {
      await act(async () => { await new Promise((r) => setTimeout(r, 16)) })
      const v = offsetOf(container as HTMLElement)
      if (!Number.isNaN(v)) seen.add(Number(v.toFixed(2)))
    }

    expect(
      seen.size,
      `expected the arc to pass through intermediate offsets, saw: ${[...seen].join(', ')}`,
    ).toBeGreaterThan(2)

    const final = offsetOf(container as HTMLElement)
    expect(final).toBeLessThan(C * 0.5)
  })

  it('a full ring is offset 0 and an empty ring is the whole circumference', () => {
    const full = render(<ProgressRing label="Cycle progress" pct={1} tone={"red"} />)
    expect(offsetOf(full.container as HTMLElement)).toBeCloseTo(0, 1)
    const empty = render(<ProgressRing label="Cycle progress" pct={0} tone={"red"} />)
    expect(offsetOf(empty.container as HTMLElement)).toBeCloseTo(C, 1)
  })
})
