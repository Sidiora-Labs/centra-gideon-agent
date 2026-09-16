import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { ModelPill } from './controls'


const pill = (contextPct?: number) => (
  <ModelPill data={undefined} agent="" value="Auto" onSelect={vi.fn()} contextPct={contextPct} />
)

describe('the context ring never states an unmeasured percentage', () => {
  it('renders no percentage at all when the backend measured nothing', () => {
    const { container } = render(pill(undefined))
    expect(container.querySelector('[title^="Context:"]')).toBeNull()
    expect(container.innerHTML).not.toContain('%')
  })

  it('renders a 0% ring when the context was measured and is empty', () => {
    render(pill(0))
    expect(screen.getByTitle('Context: 0% used')).toBeTruthy()
  })

  it('renders the measured value when there is one', () => {
    render(pill(61.5))
    expect(screen.getByTitle('Context: 62% used')).toBeTruthy()
  })

  it('unmeasured and measured-zero produce different markup', () => {
    const unmeasured = render(pill(undefined)).container.innerHTML
    const measuredZero = render(pill(0)).container.innerHTML
    expect(unmeasured).not.toEqual(measuredZero)
  })
})
