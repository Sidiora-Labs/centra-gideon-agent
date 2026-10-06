import { render, screen, cleanup } from '@testing-library/react'
import { afterEach, describe, it, expect, vi } from 'vitest'
import { ByDayAndPurposeSection } from './UsagePanel'
import { api, type UsageFold } from '../../shared/data/api'
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
describe('configured spend calendar', () => {
  it('sends calendar windows to the actual API instead of browser UTC midnight', async () => {
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, headers: new Headers(), json: async () => ({ totals: {} }) })
    vi.stubGlobal('fetch', request)
    await api.usageTotals({ window: 'day' })
    expect(String(request.mock.calls[0][0])).toContain('window=day')
    expect(String(request.mock.calls[0][0])).not.toContain('since=')
  })
  it('keeps undated history visible without putting it into dated totals', () => {
    const cell = { key: '', calls: 0, tokens_in: 0, tokens_out: 0, tokens: 0, dollars_est: 0, estimated_dollars: 0, estimated_share: 0, unpriced_calls: 0, local_calls: 0, priced: true }
    const fold: UsageFold = { window: 'day', group: 'purpose', dates: ['2026-10-06'], rows: [], total: cell, series: [], estimated_share: 0, unmapped: {}, app_sources: {}, uncounted: { calls: 0, total_calls: 0, total_dollars_est: 0, by_use_case: {} }, reachable_purposes: [], calendar_timezone: 'America/Toronto', legacy_total: { ...cell, calls: 2, dollars_est: 2 } }
    render(<ByDayAndPurposeSection fold={fold} days={1} />)
    expect(screen.getByText(/Undated history: 2 calls/)).toBeTruthy()
    expect(screen.getByText(/dated totals exclude them/)).toBeTruthy()
    expect(screen.getByText('No turns recorded today.')).toBeTruthy()
  })
})
