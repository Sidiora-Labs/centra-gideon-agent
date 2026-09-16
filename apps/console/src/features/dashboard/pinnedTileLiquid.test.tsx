
import { render, act, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest'
import { invalidateKeys } from '../../shared/data/data'
import { runtime } from '../../shared/theme/runtime'

let reducedMotion = false
const mediaListeners = new Set<() => void>()

Object.defineProperty(window, 'matchMedia', {
  configurable: true,
  writable: true,
  value: (query: string) => ({
    get matches() {
      return query.includes('prefers-reduced-motion') ? reducedMotion : false
    },
    media: query,
    addEventListener: (_type: string, fn: () => void) => { mediaListeners.add(fn) },
    removeEventListener: (_type: string, fn: () => void) => { mediaListeners.delete(fn) },
    addListener: (fn: () => void) => { mediaListeners.add(fn) },
    removeListener: (fn: () => void) => { mediaListeners.delete(fn) },
    dispatchEvent: () => false,
    onchange: null,
  }) as unknown as MediaQueryList,
})

function setReducedMotion(on: boolean): void {
  reducedMotion = on
  mediaListeners.forEach((fn) => fn())
}

const BODY = '<div>sales: 42</div>'

let hasBody = false

let holdArtifact: Promise<void> | null = null
let releaseArtifact: (() => void) | null = null
function holdTheFetch(): void {
  holdArtifact = new Promise<void>((resolve) => { releaseArtifact = resolve })
}
async function releaseTheFetch(): Promise<void> {
  releaseArtifact?.()
  holdArtifact = null
  releaseArtifact = null
  for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })
}

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
    artifact: vi.fn(async () => {
      if (holdArtifact) await holdArtifact
      return { slug: 'sales', name: 'Sales', content: hasBody ? BODY : undefined }
    }),
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

const OK_ROW = {
  kind: 'tile_refreshed',
  event_id: 'overview__sales-evt-1',
  ts: '2026-08-17T09:00:00Z',
  ok: true,
  tokens: 0,
  cost_usd: 0,
  duration_ms: 8,
  nodes: [{ id: 'health', provider: 'knowledge-health', ok: true, duration_ms: 8 }],
}

const { PinnedTiles, TILE_COMPOSURE_INTENSITY } = await import('./PinnedTiles')
const { LiquidShape } = await import('../../shared/ui/motion/LiquidShape')

const liquid = () =>
  document.querySelector<SVGSVGElement>('[data-testid="pinned-tiles"] svg[data-liquid-shape]')

const dOf = (el: Element | null) => el?.querySelector('path')?.getAttribute('d') ?? ''

beforeAll(() => {
  if (typeof URL.createObjectURL !== 'function') {
    URL.createObjectURL = () => 'blob:pinned-tile-liquid-test'
    URL.revokeObjectURL = () => {}
  }
})

beforeEach(() => {
  hasBody = false
  holdArtifact = null
  releaseArtifact = null
  refreshTile.mockReset()
  refreshTile.mockResolvedValue({ refreshed: false, reason: 'within_ttl', ok: true, row: OK_ROW })
  localStorage.clear()
  invalidateKeys('dashboard:views')
  invalidateKeys('dashboard:tile:sales')
  setReducedMotion(false)
  runtime.expressiveness = 0.8
})

afterEach(() => {
  setReducedMotion(false)
  runtime.expressiveness = 0.8
})

async function paint() {
  render(<PinnedTiles />)
  for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })
}

async function loadTheBody() {
  hasBody = true
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh tile' })) })
  for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })
}

describe('a pinned tile wears its composure as a liquid silhouette', () => {
  it('reaches the primitive, on the animated branch, with the text carriers intact', async () => {
    await paint()

    expect(screen.getByText('Sales')).toBeInTheDocument()

    const el = liquid()
    expect(el).not.toBeNull()
    expect(el).toHaveAttribute('data-liquid-shape', 'morph')
    expect(el).toHaveAttribute('data-liquid-tier', 'bold')
    expect(dOf(el)).toMatch(/^M[\d.]/)
    expect(el).toHaveAttribute('aria-hidden', 'true')

    expect(screen.getByText('Loading tile…')).toBeInTheDocument()
    const chip = document.querySelector('[data-testid="tile-source-ok"]')
    expect(chip).not.toBeNull()
    expect(chip?.getAttribute('aria-label')).toBeTruthy()
  })

  it('depicts blob while loading and squircle once loaded, pinned by exact geometry', async () => {
    setReducedMotion(true)
    await paint()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const unsettled = dOf(liquid())
    expect(unsettled).toMatch(/^M[\d.]/)

    const reference = (active: boolean) => {
      const { container, unmount } = render(
        <LiquidShape from="blob" to="squircle" active={active} intensity={TILE_COMPOSURE_INTENSITY} />,
      )
      const d = container.querySelector('path')?.getAttribute('d') ?? ''
      unmount()
      expect(d).toMatch(/^M[\d.]/)
      return d
    }
    const blob = reference(false)
    const squircle = reference(true)
    expect(blob).not.toBe(squircle)

    expect(unsettled).toBe(blob)

    await loadTheBody()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const settled = dOf(liquid())
    expect(settled).toBe(squircle)
    expect(settled).not.toBe(unsettled)
  })

  it('unsettles while a re-read is IN FLIGHT and settles again when it lands', async () => {
    setReducedMotion(true)
    await paint()
    await loadTheBody()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const settled = dOf(liquid())
    expect(settled).toMatch(/^M[\d.]/)

    holdTheFetch()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Refresh tile' })) })
    for (let i = 0; i < 4; i++) await act(async () => { await Promise.resolve() })

    const inFlight = dOf(liquid())
    expect(inFlight).toMatch(/^M[\d.]/)
    expect(inFlight).not.toBe(settled)

    await releaseTheFetch()
    expect(screen.getByText('Sales')).toBeInTheDocument()
    expect(dOf(liquid())).toBe(settled)
  })

  it('keeps the SAME liquid node across the loading→loaded flip', async () => {
    await paint()

    const before = liquid()
    expect(before).not.toBeNull()

    const title = screen.getByText('Sales')
    expect(title.parentElement).not.toBeNull()
    expect(title.parentElement?.contains(before as Node)).toBe(true)

    await loadTheBody()

    expect(screen.queryByText('Loading tile…')).toBeNull()
    expect(document.querySelector('iframe')).not.toBeNull()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const after = liquid()
    expect(after).not.toBeNull()
    expect(after === before).toBe(true)
    expect(after).toHaveAttribute('data-liquid-shape', 'morph')
  })

  it('takes the INSTANT path under prefers-reduced-motion and stays put', async () => {
    setReducedMotion(true)
    await paint()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const el = liquid()
    expect(el).not.toBeNull()
    expect(el).toHaveAttribute('data-liquid-shape', 'instant')
    expect(el).toHaveAttribute('data-liquid-tier', 'reduced')

    const first = dOf(el)
    expect(first).toMatch(/^M[\d.]/)

    vi.useRealTimers()
    await new Promise((r) => setTimeout(r, 120))
    expect(dOf(liquid())).toBe(first)
  })

  it('drops to the refined tier at expressiveness 0 and still draws a real shape', async () => {
    runtime.expressiveness = 0
    await paint()
    expect(screen.getByText('Sales')).toBeInTheDocument()

    const el = liquid()
    expect(el).not.toBeNull()
    expect(el).toHaveAttribute('data-liquid-shape', 'morph')
    expect(el).toHaveAttribute('data-liquid-tier', 'refined')
    expect(dOf(el)).toMatch(/^M[\d.]/)
    expect(dOf(el).match(/C/g)).toHaveLength(16)
  })
})
