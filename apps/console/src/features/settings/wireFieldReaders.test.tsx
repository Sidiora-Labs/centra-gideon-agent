import { describe, expect, it } from 'vitest'
import { render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { CallerRow, HealthRow } from './GuardrailsPanel'
import type { CallerHealth, ProviderHealth } from '../../shared/data/api'


const SETTINGS = join(process.cwd(), "src/features/settings")
const read = (f: string) => readFileSync(join(SETTINGS, f), 'utf8')

const base: ProviderHealth = {
  name: 'bedrock',
  breaker_state: 'closed',
  consecutive_failures: 0,
  calls: 20,
  passed: 8,
  failed: 12,
  pass_rate: 0.4,
  p50_ms: 100,
  p90_ms: 200,
  p99_ms: 900,
  failure_modes: { rate_limited: 7, provider_error: 4, timeout: 1 },
  degraded: false,
}

describe('ProviderHealth.failure_modes reaches the row', () => {
  it('renders every mode with its count', () => {
    const { container } = render(<HealthRow p={base} />)
    const text = container.textContent ?? ''
    expect(text).toContain('rate limited ×7')
    expect(text).toContain('provider error ×4')
    expect(text).toContain('timeout ×1')
  })

  it('orders modes by frequency so the dominant one reads first', () => {
    const { container } = render(<HealthRow p={base} />)
    const text = container.textContent ?? ''
    expect(text.indexOf('rate limited')).toBeLessThan(text.indexOf('provider error'))
    expect(text.indexOf('provider error')).toBeLessThan(text.indexOf('timeout'))
  })

  it('renders nothing extra for a provider with no failures', () => {
    const { container } = render(
      <HealthRow p={{ ...base, failed: 0, pass_rate: 1, failure_modes: {} }} />)
    expect(container.querySelectorAll('.rounded-pill').length).toBe(0)
  })

  it('survives a missing failure_modes without throwing', () => {
    const { container } = render(
      <HealthRow p={{ ...base, failure_modes: undefined as unknown as Record<string, number> }} />)
    expect(container.textContent).toContain('bedrock')
  })
})

describe('ProviderHealth.p99_ms is shown when the tail diverges', () => {
  it('shows p99 when it is materially worse than p90', () => {
    expect(render(<HealthRow p={base} />).container.textContent).toContain('p99 900ms')
  })

  it('omits p99 when it tracks p90', () => {
    const { container } = render(<HealthRow p={{ ...base, p90_ms: 200, p99_ms: 210 }} />)
    expect(container.textContent).toContain('p90 200ms')
    expect(container.textContent).not.toContain('p99')
  })
})

describe('MemoryLink.from_kind reaches the backlinks row', () => {
  it('EntityBacklinks renders from_kind beside from_ref', () => {
    const src = read('MemoryPanel.tsx')
    expect(src, 'the backlinks row should render l.from_kind').toMatch(/\{l\.from_kind\}/)
  })

  it('does NOT render to_entity or to_ref in a backlinks row', () => {
    const src = read('MemoryPanel.tsx')
    expect(src).not.toMatch(/\{l\.to_entity\}/)
    expect(src).not.toMatch(/\{l\.to_ref\}/)
  })
})

const deadCaller: CallerHealth = {
  name: 'skill_ladder',
  calls: 6,
  passed: 0,
  failed: 6,
  pass_rate: 0,
  p50_ms: 0,
  p90_ms: 0,
  p99_ms: 0,
  failure_modes: { provider_error: 6 },
  dollars_est: 0.12,
}

describe('CallerHealth reaches a rendered row', () => {
  it('names the dead subsystem, its rate, and why', () => {
    const { container } = render(<CallerRow c={deadCaller} />)
    const text = container.textContent ?? ''
    expect(text).toContain('skill ladder')
    expect(text).toContain('6 calls')
    expect(text).toContain('0% ok')
    expect(text).toContain('provider error')
  })

  it('a healthy caller stays a one-line summary, no failure mode', () => {
    const healthy: CallerHealth = { ...deadCaller, passed: 6, failed: 0, pass_rate: 1, p90_ms: 800 }
    const text = render(<CallerRow c={healthy} />).container.textContent ?? ''
    expect(text).toContain('100% ok')
    expect(text).toContain('p90 800ms')
    expect(text).not.toContain('provider error')
  })
})
