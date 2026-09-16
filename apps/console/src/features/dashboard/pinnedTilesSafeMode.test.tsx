import { render, act, screen } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { PinnedTiles } from './PinnedTiles'
import { invalidateKeys } from '../../shared/data/data'

const BODY = '<div>sales: 42</div>'
const artifact = vi.fn(async () => ({ slug: 'sales', name: 'Sales', content: BODY }))
const refreshTile = vi.fn(async () => ({ refreshed: false, reason: 'within_ttl', ok: true, nodes: [], row: {} }))

vi.mock('../../app/shell/appSdk', () => ({ launchChat: () => {}, notify: () => {} }))

const surfaces = vi.hoisted(() => ({ safe: false }))
vi.mock('../../shared/ui/surfaces/layers', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../shared/ui/surfaces/layers')>()
  return {
    ...real,
    safeMode: () => surfaces.safe,
    maxSurfaceLayer: () => (surfaces.safe ? real.LAYER_CORE : real.LAYER_USER),
  }
})

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
            refresh: { mode: 'ttl', ttl_secs: 900, skeleton: 'sales-skeleton', data: [{ id: 'h', provider: 'knowledge-health', config: {} }] },
          },
        ],
      },
    ]),
    artifact: (...a: unknown[]) => artifact(...(a as [])),
    artifactExists: vi.fn(async () => true),
    createArtifact: vi.fn(async () => ({})),
    deleteArtifact: vi.fn(async () => ({})),
    pinTile: vi.fn(async () => ({})),
    resolveTile: vi.fn(async () => ({})),
    refreshTile: (...a: unknown[]) => refreshTile(...(a as [])),
    tileLedgerHref: (viewId: string, ref: string) => `/api/dashboard/views/${viewId}/tiles/refresh?ref=${ref}`,
  },
}))

if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = () => 'blob:pinned-tiles-safe-mode-test'
  URL.revokeObjectURL = () => {}
}

beforeEach(() => {
  invalidateKeys('dashboard:views')
  invalidateKeys('dashboard:tile:sales')
  artifact.mockClear()
  refreshTile.mockClear()
  surfaces.safe = false
})

async function paint() {
  render(<PinnedTiles />)
  await act(async () => { await Promise.resolve() })
  await act(async () => { await Promise.resolve() })
}

describe('the pinned-tile band in safe mode', () => {
  it('renders each tile as an inert link and fetches NOTHING', async () => {
    surfaces.safe = true
    await paint()

    const link = screen.getByTestId('pinned-tile-inert')
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('#/artifacts/sales')

    expect(artifact).not.toHaveBeenCalled()
    expect(refreshTile).not.toHaveBeenCalled()

    expect(screen.queryByLabelText('Refresh tile')).toBeNull()
    expect(screen.queryByLabelText('Unpin from dashboard')).toBeNull()
    expect(screen.getByTestId('pinned-tiles-safe-note').textContent).toContain('links only')
  })

  it('renders the LIVE tile with safe mode off, and does call both (the control leg)', async () => {
    await paint()

    expect(screen.queryByTestId('pinned-tile-inert')).toBeNull()
    expect(screen.getByLabelText('Refresh tile')).toBeInTheDocument()
    expect(artifact).toHaveBeenCalled()
    expect(refreshTile).toHaveBeenCalled()
  })
})
