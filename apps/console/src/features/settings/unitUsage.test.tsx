import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { UnitUsageSummary, UsageTable } from './UsagePanel'
import type { UsageAgg } from '../../shared/data/api'
afterEach(cleanup)
const units: UsageAgg['units'] = {
  image: { quantity: 2, calls: 3, unknown_quantity_calls: 1, unpriced_calls: 1, cost_usd: .2 },
  minute: { quantity: .5, calls: 1, unknown_quantity_calls: 0, unpriced_calls: 0, cost_usd: 0 },
  second: { quantity: 4.25, calls: 1, unknown_quantity_calls: 0, unpriced_calls: 0, cost_usd: .4 },
  character: { quantity: 9, calls: 1, unknown_quantity_calls: 0, unpriced_calls: 0, cost_usd: .01 },
}
describe('native media usage', () => {
  it('keeps billing units separate and explains unknown amounts and cost', () => {
    render(<UnitUsageSummary units={units} />)
    for (const label of ['images', 'audio minutes', 'video seconds', 'speech characters']) expect(screen.getByText(label)).toBeTruthy()
    expect(screen.getByText('2 · 3 calls · ≥$0.2000')).toBeTruthy()
    expect(screen.getByText('0.5 · 1 call · $0.0000')).toBeTruthy()
    expect(screen.getByText(/unknown quantities/)).toBeTruthy()
    expect(screen.getByText(/Cost is incomplete/)).toBeTruthy()
  })
  it('adds actual unit quantities to model rows and preserves the known cost floor', () => {
    render(<UsageTable rows={[{ model: 'image-model', input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_creation_tokens: 0, turns: 3, cost_usd: .2, priced: false,
      units: { image: units!.image } }]} keyField="model" empty="empty" />)
    expect(screen.getByText('2 images + unknown')).toBeTruthy()
    expect(screen.getByText('≥$0.2000')).toBeTruthy()
  })
  it('accepts older token-only aggregates without inventing media activity', () => {
    const { container } = render(<UnitUsageSummary units={undefined} />)
    expect(container.textContent).toBe('')
  })
})
