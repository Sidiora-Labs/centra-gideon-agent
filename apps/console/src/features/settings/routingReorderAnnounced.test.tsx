import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, act, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { RoutingPanel } from './RoutingPanel'


const ROWS = [
  {
    use_case: 'reasoning', mode: 'off', pin: '',
    candidates: [{ ref: 'local:qwen', local: true }, { ref: 'cloud:opus', local: false }],
    classes: { short_chat: { order: ['local:qwen', 'cloud:opus'], basis: { source: 'manual' } } },
  },
]

const setRoutingPolicy = vi.fn((_body: unknown) => Promise.resolve({}))
const routingPolicy = vi.fn(() => Promise.resolve({ enabled: true, use_cases: ROWS }))

vi.mock('../../shared/data/api', () => ({
  api: {
    routingPolicy: () => routingPolicy(),
    setRoutingPolicy: (b: unknown) => setRoutingPolicy(b as never),
    modelsTelemetry: () => Promise.resolve({ rows: [] }),
    routingProposals: () => Promise.resolve({ count: 0, proposals: [] }),
  },
}))
vi.mock('../../shared/data/data', () => ({
  useQuery: (_k: string, fn: () => Promise<unknown>) => {
    const [d, setD] = require('react').useState(undefined)
    require('react').useEffect(() => { void fn().then(setD) }, [])
    return { data: d, refresh: () => {} }
  },
}))

function renderPanel() {
  return render(<RoutingPanel query={{ uc: 'reasoning', qc: 'short_chat' }} setQuery={() => {}} />)
}

describe('a routing reorder is announced', () => {
  beforeEach(() => { setRoutingPolicy.mockClear(); setRoutingPolicy.mockImplementation(() => Promise.resolve({})) })

  it('the section mounts a polite status region, empty at rest', async () => {
    const { container } = renderPanel()
    await waitFor(() => expect(screen.getAllByRole('button', { name: /^Move / }).length).toBeGreaterThan(0))
    const regions = [...container.querySelectorAll('[role="status"][aria-live="polite"]')]
    const sr = regions.find((r) => r.className.includes('sr-only'))
    expect(sr, 'an sr-only polite region must exist before any move').toBeTruthy()
    expect(sr!.textContent).toBe('')
  })

  it('a successful move announces the ref AND its new position', async () => {
    const { container } = renderPanel()
    const later = await waitFor(() => screen.getByRole('button', { name: 'Move local:qwen later' }))
    await act(async () => { later.click() })
    await waitFor(() => {
      const sr = [...container.querySelectorAll('[role="status"][aria-live="polite"]')]
        .find((r) => r.className.includes('sr-only'))
      expect(sr!.textContent).toBe('local:qwen moved to position 2 of 2')
    })
    expect(setRoutingPolicy).toHaveBeenCalledWith(
      expect.objectContaining({ use_case: 'reasoning', query_class: 'short_chat', order: ['cloud:opus', 'local:qwen'] }),
    )
  })

  it('a FAILED move announces nothing', async () => {
    setRoutingPolicy.mockImplementation(() => Promise.reject(new Error('nope')))
    const { container } = renderPanel()
    const later = await waitFor(() => screen.getByRole('button', { name: 'Move local:qwen later' }))
    await act(async () => { later.click() })
    await waitFor(() => expect(screen.getByRole('alert')).toBeTruthy())
    const sr = [...container.querySelectorAll('[role="status"][aria-live="polite"]')]
      .find((r) => r.className.includes('sr-only'))
    expect(sr!.textContent, 'a rejected write must not claim the move happened').toBe('')
  })

  it('save reports success, so the announcement cannot be inferred from settling', () => {
    const src = readFileSync(join(process.cwd(), "src/features/settings/RoutingPanel.tsx"), 'utf8')
    expect(src).toMatch(/Promise<boolean>/)
    expect(src).toMatch(/\.then\(\(ok\) => \{ if \(ok\)/)
  })
})
