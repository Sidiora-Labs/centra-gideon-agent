import { useCallback, useEffect, useMemo, useState } from 'react'
import { reportActionFailure } from '../../app/shell/reportingWrite'
import { Check, X, RefreshCw, Sparkles } from 'lucide-react'
import { api, type DashboardView, type DashboardTile, type Artifact, type TileRefreshRow } from '../../shared/data/api'
import { useQuery, invalidateKeys } from '../../shared/data/data'
import { useVisiblePoll } from '../../shared/data/useVisiblePoll'
import { relPast } from '../schedule/scheduleMeta'
import { WidgetFrame } from '../../shared/ui/widget/WidgetFrame'
import { GenUiWidget } from '../../shared/ui/genui/GenUiWidget'
import { GenUiHostCtx } from '../../shared/ui/genui/actions'
import { findGenUiBlock } from '../../shared/ui/widget/blocks'
import { LiquidShape } from '../../shared/ui/motion'
import { safeMode } from '../../shared/ui/surfaces/layers'
import { SquareIconButton } from '../../shared/ui/SquareIconButton'
import { costLabel, isLive, lastRefreshFailed, sourceChips } from './tileFreshness'


const VIEWS_CACHE_KEY = 'dashboard:views'

export function artifactTiles(views: DashboardView[] | undefined): DashboardTile[] {
  const overview = views?.find((v) => v.id === 'overview')
  if (!overview) return []
  return overview.tiles
    .filter((t) => t.ref.startsWith('artifact:'))
    .sort((a, b) => a.order - b.order)
}

export function PinnedTiles() {
  const { data: views, refresh } = useQuery<DashboardView[]>(
    VIEWS_CACHE_KEY, () => api.dashboardViews().catch(() => [] as DashboardView[]), { persist: true },
  )
  const tiles = useMemo(() => artifactTiles(views), [views])

  const resolve = useCallback((ref: string, keep: boolean) => {
    api.resolveTile('overview', { ref, keep }).then(() => {
      invalidateKeys(VIEWS_CACHE_KEY)
      refresh()
    }).catch(() => {})
  }, [refresh])

  if (tiles.length === 0) return null

  const safe = safeMode()

  return (
    <section className="flex min-w-0 flex-col gap-s" data-testid="pinned-tiles">
      <div className="flex items-center gap-s">
        <Sparkles size={14} className="shrink-0 text-on-surface-low" />
        {
}
        <h2 data-type="label-l" className="text-on-surface-var">Pinned</h2>
        <span className="h-px flex-1 bg-outline-variant/40" />
      </div>
      {safe ? (
        <>
          {
}
          <p data-type="body-s" className="text-on-surface-low" data-testid="pinned-tiles-safe-note">
            Safe mode — tiles are links only. Nothing is fetched, refreshed or re-run.
          </p>
          <ul className="flex min-w-0 flex-col gap-xs">
            {tiles.map((t) => (
              <InertTile key={t.ref} tile={t} />
            ))}
          </ul>
        </>
      ) : (
        <div className="grid grid-cols-1 gap-l lg:grid-cols-2">
          {tiles.map((t) => (
            <PinnedTile key={t.ref} tile={t} onResolve={resolve} />
          ))}
        </div>
      )}
    </section>
  )
}

function InertTile({ tile }: { tile: DashboardTile }) {
  const slug = tile.ref.slice('artifact:'.length)
  return (
    <li className="min-w-0">
      <a
        href={`#/artifacts/${encodeURIComponent(slug)}`}
        data-testid="pinned-tile-inert"
        data-type="body-m"
        className="block truncate rounded-lg border border-outline-variant/40 bg-surface-low/60 px-s py-xs text-on-surface underline decoration-dotted underline-offset-2"
      >
        {slug}
      </a>
    </li>
  )
}

const TONE_CLASS = {
  ok: 'bg-success',
  error: 'bg-error',
  pending: 'bg-outline',
} as const

function FreshnessBar({ tile, row }: { tile: DashboardTile; row: TileRefreshRow | undefined }) {
  const chips = sourceChips(tile, row)
  const failed = lastRefreshFailed(row)
  const cost = costLabel(row)
  return (
    <div className="flex min-w-0 items-center gap-xs" data-testid="tile-freshness">
      <a
        href={api.tileLedgerHref('overview', tile.ref)}
        target="_blank"
        rel="noreferrer"
        data-type="label-s"
        data-testid="tile-ledger-link"
        title={
          row?.ts
            ? `Refreshed ${row.ts}${cost ? ` · ${cost}` : ''}${row.error ? ` · ${row.error}` : ''} — open the ledger row`
            : 'Never refreshed — open the ledger row'
        }
        className={`truncate underline decoration-dotted underline-offset-2 ${failed ? 'text-error' : 'text-on-surface-low'}`}
      >
        {row?.ts ? relPast(row.ts) : 'never refreshed'}
      </a>
      {chips.map((c) => (
        <span
          key={c.id}
          role="img"
          aria-label={c.title}
          title={c.title}
          data-testid={`tile-source-${c.tone}`}
          className={`size-2 shrink-0 rounded-full ${TONE_CLASS[c.tone]}`}
        />
      ))}
    </div>
  )
}

export const TILE_COMPOSURE_INTENSITY = 0.5

function PinnedTile({ tile, onResolve }: { tile: DashboardTile; onResolve: (ref: string, keep: boolean) => void }) {
  const slug = tile.ref.slice('artifact:'.length)
  const isProposal = tile.added_by === 'agent'
  const live = isLive(tile)

  const { data: artifact, refresh, revalidating: reReading } = useQuery<Artifact | null>(
    `dashboard:tile:${slug}`, () => api.artifact(slug).catch(() => null), { persist: true },
  )
  const [reloadKey, setReloadKey] = useState(0)
  useEffect(() => { if (reloadKey) refresh() }, [reloadKey, refresh])

  const [row, setRow] = useState<TileRefreshRow | undefined>(undefined)

  const tick = useCallback(() => {
    api.refreshTile('overview', { ref: tile.ref })
      .then((r) => { if (r.row?.ts) setRow(r.row); if (r.refreshed) refresh() })
      .catch(() => {})
  }, [tile.ref, refresh])
  useVisiblePoll(tick, live ? 60_000 : null)

  const onRefreshClick = useCallback(() => {
    if (!live) { setReloadKey((k) => k + 1); return }
    api.refreshTile('overview', { ref: tile.ref, force: true })
      .then((r) => { if (r.row?.ts) setRow(r.row); refresh() })
      .catch(reportActionFailure('refresh this tile'))
  }, [live, tile.ref, refresh])

  const body = artifact?.content
  const genuiBody = findGenUiBlock(body || '')

  return (
    <div className="flex min-w-0 flex-col gap-xs rounded-lg border border-outline-variant/40 bg-surface-low/60 p-s">
      <div className="flex items-center gap-s">
        {
}
        <LiquidShape
          from="blob"
          to="squircle"
          active={Boolean(body) && !reReading}
          intensity={TILE_COMPOSURE_INTENSITY}
          className="size-4 shrink-0"
        />
        <span data-type="label-m" className="min-w-0 flex-1 truncate text-on-surface-var">
          {artifact?.name || slug}
        </span>
        {live && <FreshnessBar tile={tile} row={row} />}
        {isProposal && (
          <span data-type="label-s" className="inline-flex items-center gap-1 rounded-pill bg-primary-container px-2 py-0.5 text-on-primary-container">
            <Sparkles size={11} /> Proposed
          </span>
        )}
        <SquareIconButton label="Refresh tile" onClick={onRefreshClick}>
          <RefreshCw size={12} />
        </SquareIconButton>
        {isProposal ? (
          <>
            <SquareIconButton label="Accept — keep on dashboard" onClick={() => onResolve(tile.ref, true)}>
              <Check size={13} />
            </SquareIconButton>
            <SquareIconButton label="Dismiss proposal" onClick={() => onResolve(tile.ref, false)}>
              <X size={13} />
            </SquareIconButton>
          </>
        ) : (
          <SquareIconButton label="Unpin from dashboard" onClick={() => onResolve(tile.ref, false)}>
            <X size={13} />
          </SquareIconButton>
        )}
      </div>
      {body
        ? (genuiBody
          ? (
            <GenUiHostCtx.Provider
              value={{ producer: { kind: 'tile', viewId: 'overview', ref: tile.ref }, onResolved: refresh }}
            >
              <GenUiWidget content={genuiBody.html} title={artifact?.name || slug} slug={slug} />
            </GenUiHostCtx.Provider>
          )
          : <WidgetFrame html={body} title={artifact?.name || slug} slug={slug} />)
        : <div data-type="body-s" className="px-s py-l text-center text-on-surface-low">Loading tile…</div>}
    </div>
  )
}
