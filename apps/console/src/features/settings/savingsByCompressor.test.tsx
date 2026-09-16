import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import type { ToolsSavings } from '../../shared/data/api'


const base: ToolsSavings = {
  saved_chars: 40_000,
  saved_tokens_estimated: 10_000,
  estimated: true,
  projection_count: 12,
  top_compressor: 'json_head',
  by_compressor: { json_head: 32_000, log_tail: 8_000 },
  rows: [],
}

async function mount(over: Partial<ToolsSavings> = {}) {
  vi.resetModules()
  vi.doMock('../../shared/data/api', () => ({
    api: {
      toolsSavings: () => Promise.resolve({ ...base, ...over }),
      gideonConfig: () => Promise.resolve({}),
      patchConfig: () => Promise.resolve({}),
    },
  }))
  const { SavingsCard } = await import('./ProjectionRulesPanel')
  let r!: ReturnType<typeof render>
  await act(async () => {
    r = render(<SavingsCard />)
    await new Promise((res) => setTimeout(res, 0))
  })
  return r
}

describe('the per-compressor breakdown', () => {
  it('names every contributing compressor', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('json_head')
    expect(text).toContain('log_tail')
  })

  it('converts chars to tokens so both figures share one unit', async () => {
    const text = (await mount()).container.textContent ?? ''
    expect(text).toContain('8,000')
    expect(text).toContain('2,000')
    expect(text).not.toContain('32,000')
  })

  it('orders by savings, biggest first', async () => {
    const text = (await mount()).container.textContent ?? ''
    const reordered = await mount({ by_compressor: { log_tail: 8_000, json_head: 32_000 } })
    const t2 = reordered.container.textContent ?? ''
    expect(t2.indexOf('json_head')).toBeLessThan(t2.lastIndexOf('log_tail'))
    expect(text.indexOf('json_head')).toBeLessThan(text.lastIndexOf('log_tail'))
  })

  it('drops a compressor that saved nothing', async () => {
    const { container } = await mount({
      by_compressor: { json_head: 32_000, log_tail: 8_000, noop_thing: 0 },
    })
    expect(container.textContent).not.toContain('noop_thing')
  })
})

describe('the breakdown appears only when it adds something', () => {
  it('is omitted for a single compressor', async () => {
    const { container } = await mount({ by_compressor: { json_head: 40_000 } })
    const text = container.textContent ?? ''
    expect(text).toContain('top compressor: json_head')
    expect(text.match(/json_head/g)?.length).toBe(1)
  })

  it('is omitted when every entry saved zero', async () => {
    const { container } = await mount({ by_compressor: { zeroish_one: 0, zeroish_two: 0 } })
    expect([...container.querySelectorAll('.font-mono')].map((e) => e.textContent))
      .not.toContain('zeroish_one')
  })

  it('survives a missing by_compressor entirely', async () => {
    const { container } = await mount({
      by_compressor: undefined as unknown as Record<string, number>,
    })
    expect(container.textContent).toContain('TokenJuice saved')
  })
})

describe('the card still hides itself when there is nothing to report', () => {
  it('renders nothing at zero savings, breakdown or not', async () => {
    const { container } = await mount({
      saved_chars: 0, saved_tokens_estimated: 0, projection_count: 0,
      top_compressor: null, by_compressor: { json_head: 5, log_tail: 5 },
    })
    expect(container.textContent).toBe('')
  })
})
