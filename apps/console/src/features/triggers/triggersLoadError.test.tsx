import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'


const good = { autonomyLadder: () => Promise.reject(new Error('no ladder in this test')),
  triggerVariables: () => Promise.resolve({ lifecycle: [], schedule: [], event: [] }) }

function mockApi(over: Record<string, () => Promise<unknown>>) {
  vi.doMock('../../shared/data/api', async (orig) => ({
    ...(await orig<Record<string, unknown>>()),
    api: {
      schedules: () => Promise.resolve({ jobs: [] }),
      hooks: () => Promise.resolve([]),
      storeTriggers: () => Promise.resolve([]),
      eventTriggers: () => Promise.resolve([]),
      actionProviders: () => Promise.resolve([]),
      ...good,
      ...over,
    },
  }))
}

async function mount() {
  const { TriggersSection } = await import('./TriggersSection')
  const navigate = vi.fn()
  render(<TriggersSection sub="" navigate={navigate} navEpoch={0} query={{}} setQuery={() => {}} />)
  return { navigate }
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear() })

describe('the triggers list distinguishes failure from empty', () => {
  it('shows a retryable LoadError when every source rejects', async () => {
    const boom = () => Promise.reject(new Error('gateway down'))
    mockApi({ schedules: boom, hooks: boom, storeTriggers: boom, eventTriggers: boom })
    await mount()
    const alert = await waitFor(() => screen.getByRole('alert'))
    expect(alert.textContent, 'names what failed to load').toMatch(/triggers/i)
    expect(screen.getByRole('button', { name: /Retry/ }), 'and offers a way back').toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'No triggers' }), 'not the newcomer empty state').toBeNull()
  })

  it('shows the preset empty state — not an error — when the fetch really is empty', async () => {
    mockApi({})
    await mount()
    await waitFor(() => expect(screen.getByRole('heading', { name: 'No triggers' })).toBeInTheDocument())
    expect(screen.queryByRole('alert'), 'a genuine empty is not an error').toBeNull()
  })

  it('renders the working list on a PARTIAL failure — one bad source does not hide the rest', async () => {
    const boom = () => Promise.reject(new Error('events down'))
    mockApi({ eventTriggers: boom })
    await mount()
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })
})

describe('the source no longer swallows its own error', () => {
  it('the catch-to-empty is gone from the four list fetchers', () => {
    const src = require('node:fs').readFileSync(require('node:path').join(process.cwd(), "src/features/triggers/TriggersListPage.tsx"), 'utf8')
    for (const key of ['triggers:schedules', 'triggers:hooks', 'triggers:store', 'triggers:events']) {
      const m = new RegExp(`useQuery\\('${key}'[\\s\\S]*?\\{ persist:`).exec(src)
      expect(m, `${key} fetcher must be found`).not.toBeNull()
      expect(m![0], `${key} must not catch its rejection to []`).not.toMatch(/\.catch\(\(\)\s*=>\s*\[\]/)
    }
    expect(src, 'and the error flag gates the LoadError').toMatch(/const loadFailed = triggers === null &&/)
  })
})
