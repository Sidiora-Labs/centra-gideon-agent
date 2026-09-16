import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { headlineCost } from './UsagePanel'


describe('the headline cost states a floor rather than nothing', () => {
  it('shows the exact figure when every model is priced', () => {
    expect(headlineCost({ cost_usd: 11.3496, priced: true })).toBe('$11.35')
    expect(headlineCost({ cost_usd: 0.7398, priced: true })).toBe('$0.7398')
  })

  it('states a FLOOR when some of the spend is priced and some is not', () => {
    expect(headlineCost({ cost_usd: 11.3496, priced: false })).toBe('≥$11.35')
    expect(headlineCost({ cost_usd: 1.89, priced: false })).toBe('≥$1.89')
    expect(headlineCost({ cost_usd: 0.0123, priced: false })).toBe('≥$0.0123')
  })

  it('falls back to the word only when there is no floor to state', () => {
    expect(headlineCost({ cost_usd: 0, priced: false })).toBe('unpriced')
  })

  it('never prints a bare $0.00 for an unpriced period', () => {
    for (const cost of [0, 0.0]) {
      expect(headlineCost({ cost_usd: cost, priced: false })).not.toBe('$0.00')
    }
  })

  it('the stat renders through this function, not an inline branch', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/UsagePanel.tsx"), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
    expect(src).toMatch(/<BigStat caption="cost" value=\{headlineCost\(t\)\} \/>/)
    expect(src, 'no inline unpriced ternary should come back').not.toMatch(/t\.priced \? fmtUsd/)
    expect(src, 'the incompleteness marker carries what the floor does not say')
      .toMatch(/<span className="text-warning">Partial<\/span>/)
  })
})
