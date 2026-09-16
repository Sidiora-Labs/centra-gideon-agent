export function epochSeconds(ts?: number | string | null): number | undefined {
  if (ts == null || ts === '') return undefined
  if (typeof ts === 'number') return Number.isFinite(ts) ? ts : undefined
  const ms = Date.parse(ts)
  return Number.isFinite(ms) ? ms / 1000 : undefined
}


export function clockTime(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function fullStamp(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })
}

export function isoStamp(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs === undefined) return ''
  return new Date(secs * 1000).toISOString()
}

export function sessionRecencyMs(s: SessionStamps): number {
  const secs = sessionActivitySeconds(s)
  return secs == null ? 0 : secs * 1000
}

type SessionStamps = { last_activity_ts?: string; last_ts?: string; created?: string }

export function sessionActivitySeconds(s: SessionStamps): number | undefined {
  return epochSeconds(s.last_activity_ts) ?? epochSeconds(s.last_ts) ?? epochSeconds(s.created)
}
