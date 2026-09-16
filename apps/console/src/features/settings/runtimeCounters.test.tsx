import { describe, expect, it, vi } from 'vitest'
import { act, render } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'


const PANEL = join(process.cwd(), "src/features/settings/UsagePanel.tsx")
const API = join(process.cwd(), "src/shared/data/api.ts")

const MEASURED = [
  'sessions_created', 'sessions_cleaned',
  'subagents_spawned', 'subagents_completed', 'subagents_failed',
  'input_tokens', 'output_tokens',
  'cache_creation_tokens', 'cache_read_tokens',
  'total_turns', 'total_duration_ms',
]

const WRITERLESS = [
  'messages_received', 'messages_success', 'messages_failed',
  'tool_approvals', 'tool_denials', 'tool_auto_approved', 'timeouts',
]

describe('the wire type matches what the backend actually sends', () => {
  const api = readFileSync(API, 'utf8')
  const iface = api.slice(
    api.indexOf('export interface SystemAgentStats'),
    api.indexOf('export interface SystemInfo'),
  )

  it('declares every measured counter', () => {
    const missing = MEASURED.filter((f) => !new RegExp(`\\b${f}\\b`).test(iface))
    expect(missing, `SystemAgentStats is missing measured counter(s): ${missing.join(', ')}`).toEqual([])
  })

  it('declares no writerless counter', () => {
    const zombies = WRITERLESS.filter((f) => new RegExp(`\\b${f}\\b`).test(iface))
    expect(zombies, `SystemAgentStats declares writerless counter(s): ${zombies.join(', ')}`).toEqual([])
  })
})

describe('UsagePanel reads every measured counter', () => {
  const src = readFileSync(PANEL, 'utf8')

  it('references all 11 on the stats object', () => {
    const unread = MEASURED.filter((f) => !src.includes(`sys.${f}`))
    expect(unread, `measured but never rendered: ${unread.join(', ')}`).toEqual([])
  })

  it('gates the section on ANY counter moving, not just tokens', () => {
    expect(src).toMatch(/sessions_created > 0/)
    expect(src).toMatch(/subagents_spawned > 0/)
  })
})

describe('the rendered rows', () => {
  const stats = {
    sessions_created: 4, sessions_cleaned: 1,
    subagents_spawned: 7, subagents_completed: 6, subagents_failed: 1,
    input_tokens: 12_400, output_tokens: 3_100,
    cache_creation_tokens: 50, cache_read_tokens: 9_000,
    total_turns: 18, total_duration_ms: 3_930_000,
  }

  const mount = async (overrides: Partial<typeof stats> = {}) => {
    vi.resetModules()
    vi.doMock('../../shared/data/api', () => ({
      api: {
        usageTotals: () => Promise.resolve({ totals: null }),
        usageRollup: () => Promise.resolve({ rows: [] }),
        usageFold: () => Promise.reject(new Error('no fold')),
        gideonConfig: () => Promise.resolve(null),
        system: () => Promise.resolve({ stats: { ...stats, ...overrides } }),
      },
    }))
    const { UsagePanel } = await import('./UsagePanel')
    let r!: ReturnType<typeof render>
    await act(async () => {
      r = render(<UsagePanel query={{}} setQuery={() => {}} />)
      await new Promise((res) => setTimeout(res, 0))
    })
    return r
  }

  it('shows both session lifecycle counters', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('4 created / 1 cleaned')
  })

  it('shows subagent failures in warn ink only when non-zero', async () => {
    const failed = await mount()
    expect(failed.container.querySelector('.text-warn')?.textContent).toContain('1 failed')
    const clean = await mount({ subagents_failed: 0 })
    expect(clean.container.textContent).not.toContain('failed')
  })

  it('formats cumulative duration as a span, not raw ms', async () => {
    const { container } = await mount()
    expect(container.textContent).toContain('1h 5m')
    expect(container.textContent).not.toContain('3930000')
  })

  it('omits the prompt-cache row when no provider reported cached tokens', async () => {
    const { container } = await mount({ cache_read_tokens: 0, cache_creation_tokens: 0 })
    expect(container.textContent).not.toContain('Prompt cache')
  })
})
