import type { DashboardTile, TileNodeOutcome, TileRefreshRow } from '../../shared/data/api'


export type ChipTone = 'ok' | 'error' | 'pending'

export interface SourceChip {
  id: string
  tone: ChipTone
  title: string
}

export function isLive(tile: DashboardTile): boolean {
  return tile.refresh?.mode === 'ttl' && Boolean(tile.refresh?.skeleton)
}

export function sourceChips(tile: DashboardTile, row: TileRefreshRow | undefined): SourceChip[] {
  const nodes = tile.refresh?.data ?? []
  const byId = new Map<string, TileNodeOutcome>()
  for (const o of row?.nodes ?? []) byId.set(o.id, o)
  return nodes.map((n) => {
    const outcome = byId.get(n.id)
    if (!outcome) return { id: n.id, tone: 'pending' as ChipTone, title: `${n.id} — not refreshed yet` }
    if (outcome.ok) return { id: n.id, tone: 'ok' as ChipTone, title: `${n.id} — ok (${n.provider})` }
    return {
      id: n.id,
      tone: 'error' as ChipTone,
      title: `${n.id} — ${outcome.error || 'failed with no reason given'}`,
    }
  })
}

export function costLabel(row: TileRefreshRow | undefined): string {
  if (!row || row.ts === undefined) return ''
  const tokens = row.tokens ?? 0
  const ms = row.duration_ms ?? 0
  return tokens === 0 ? `0 tokens · ${ms} ms` : `${tokens} tokens · ${ms} ms`
}

export function lastRefreshFailed(row: TileRefreshRow | undefined): boolean {
  return Boolean(row && row.ts !== undefined && row.ok === false)
}
