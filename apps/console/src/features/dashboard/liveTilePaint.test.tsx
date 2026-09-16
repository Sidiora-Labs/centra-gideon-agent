import { render, act, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach } from 'vitest'
import { PinnedTiles } from './PinnedTiles'
import { invalidateKeys } from '../../shared/data/data'

const BODY = '<div>sales: 42</div>'

const refreshTile = vi.fn()

vi.mock('../../app/shell/appSdk', () => ({ launchChat: () => {}, notify: () => {} }))

vi.mock('../../shared/data/api', () => ({
  api: {
    dashboardViews: vi.fn(async () => [
      {
        id: 'overview',
        tiles: [
          {
            ref: 'artifact:sales',
            size: 'm',
            order: 0,
            added_by: 'user',
            refresh: {
              mode: 'ttl',
              ttl_secs: 900,
              skeleton: 'sales-skeleton',
              data: [{ id: 'health', provider: 'knowledge-health', config: {} }],
            },
          },
        ],
      },
    ]),
    artifact: vi.fn(async () => ({ slug: 'sales', name: 'Sales', content: BODY })),
    artifactExists: vi.fn(async () => true),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    resolveTile: vi.fn(async () => ({})),
    refreshTile: (...args: unknown[]) => refreshTile(...args),
    tileLedgerHref: (viewId: string, ref: string) =>
      `/api/dashboard/views/${viewId}/tiles/refresh?ref=${encodeURIComponent(ref)}`,
  },
}))

const FAILED_ROW = {
  kind: 'tile_refreshed',
  event_id: 'overview__sales-evt-2',
  ts: '2026-08-17T09:00:00Z',
  ok: false,
  tokens: 0,
  cost_usd: 0,
  duration_ms: 8,
  error: 'the store is unreachable',
  nodes: [{ id: 'health', provider: 'knowledge-health', ok: false, error: 'the store is unreachable', duration_ms: 8 }],
}

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:live-tile-test'
    URL.revokeObjectURL = () => {}
  }
})

beforeEach(() => {
  refreshTile.mockReset()
  localStorage.clear()
  invalidateKeys('dashboard:views')
  invalidateKeys('dashboard:tile:sales')
})

async function paint() {
  render(<PinnedTiles />)
  await act(async () => { await Promise.resolve() })
  await act(async () => { await Promise.resolve() })
  await act(async () => { await Promise.resolve() })
}

describe('a failed refresh keeps the last-good paint (never an empty panel)', () => {
  it('renders the artifact body and a RED source chip, not a loading state', async () => {
    refreshTile.mockResolvedValue({ refreshed: false, reason: 'data_failed', ok: false, nodes: FAILED_ROW.nodes, row: FAILED_ROW })

    await paint()

    expect(screen.queryByText('Loading tile…')).toBeNull()
    expect(document.querySelector('iframe')).not.toBeNull()
    const dot = document.querySelector('[data-testid="tile-source-error"]')
    expect(dot).not.toBeNull()
    expect(dot?.getAttribute('aria-label')).toContain('the store is unreachable')
  })

  it('deep-links to the ledger row and shows the failure in the freshness label', async () => {
    refreshTile.mockResolvedValue({ refreshed: false, reason: 'data_failed', ok: false, nodes: FAILED_ROW.nodes, row: FAILED_ROW })

    await paint()

    const link = document.querySelector('[data-testid="tile-ledger-link"]') as HTMLAnchorElement | null
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toContain('/tiles/refresh?ref=artifact%3Asales')
    expect(link?.getAttribute('title')).toContain('the store is unreachable')
    expect(link?.getAttribute('title')).toContain('0 tokens')
  })
})

describe('a healthy refresh reports zero cost', () => {
  it('shows a green chip and states the zero-token cost on the freshness link', async () => {
    refreshTile.mockResolvedValue({
      refreshed: true,
      reason: '',
      ok: true,
      nodes: [{ id: 'health', provider: 'knowledge-health', ok: true, error: '', duration_ms: 3 }],
      row: { ...FAILED_ROW, ok: true, error: '', nodes: [{ id: 'health', provider: 'knowledge-health', ok: true, error: '', duration_ms: 3 }] },
    })

    await paint()

    expect(document.querySelector('[data-testid="tile-source-ok"]')).not.toBeNull()
    const link = document.querySelector('[data-testid="tile-ledger-link"]')
    expect(link?.getAttribute('title')).toContain('0 tokens · 8 ms')
  })
})

describe('a manual tile polls nothing', () => {
  it('never calls the refresh endpoint and renders no freshness bar', async () => {
    const api = await import('../../shared/data/api')
    ;(api.api.dashboardViews as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      {
        id: 'overview',
        tiles: [
          {
            ref: 'artifact:sales',
            size: 'm',
            order: 0,
            added_by: 'user',
            refresh: { mode: 'manual', ttl_secs: 0, skeleton: '', data: [] },
          },
        ],
      },
    ])

    await paint()

    expect(refreshTile).not.toHaveBeenCalled()
    expect(document.querySelector('[data-testid="tile-freshness"]')).toBeNull()
  })
})
