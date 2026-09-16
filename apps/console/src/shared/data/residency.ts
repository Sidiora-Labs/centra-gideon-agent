import type { LoadedModel, MemoryPressure } from './api'

export function pressureTone(p: MemoryPressure): string {
  if (p.source === 'unavailable') return 'var(--color-outline-variant)'
  if (p.warn) return 'var(--color-danger)'
  if (p.used_pct >= p.warn_pct * 0.85) return 'var(--color-warning)'
  return 'var(--color-primary)'
}

export function pressureDetail(p: MemoryPressure): string {
  if (p.source === 'unavailable' || p.total_mb <= 0) return 'System memory unavailable on this host'
  const gb = (mb: number) => `${(mb / 1024).toFixed(1)} GB`
  return `${gb(p.used_mb)} of ${gb(p.total_mb)} in use · ${p.used_pct}%`
}

export function sortOccupants(rows: LoadedModel[]): LoadedModel[] {
  return [...rows].sort((a, b) => {
    if (a.is_active !== b.is_active) return a.is_active ? 1 : -1
    return (b.rss_mb ?? -1) - (a.rss_mb ?? -1)
  })
}

export function reclaimableCount(rows: LoadedModel[]): number {
  return rows.filter((r) => !r.is_active).length
}

export function occupantDetail(row: LoadedModel): string {
  const parts: string[] = [row.kind]
  if (row.rss_mb != null && row.rss_mb > 0) parts.push(`${Math.round(row.rss_mb)} MB`)
  if (row.generation != null && row.generation > 0) parts.push(`gen ${row.generation}`)
  if (!row.is_active) parts.push('not bound')
  return parts.join(' · ')
}
