
const KEY = 'knowledge-reading-positions'

export interface ReadingPosition { pct: number; ts: number }

const MIN_PCT = 0.02
const DONE_PCT = 0.98
const MAX_ENTRIES = 200

type PositionMap = Record<string, ReadingPosition>

function isPosition(v: unknown): v is ReadingPosition {
  if (!v || typeof v !== 'object') return false
  const p = v as Partial<ReadingPosition>
  return typeof p.pct === 'number' && Number.isFinite(p.pct) && typeof p.ts === 'number'
}

export function readingPositions(): PositionMap {
  let raw: string | null = null
  try { raw = localStorage.getItem(KEY) } catch { return {} }
  if (!raw) return {}
  let parsed: unknown
  try { parsed = JSON.parse(raw) } catch { return {} }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
  const out: PositionMap = {}
  for (const [id, v] of Object.entries(parsed as Record<string, unknown>)) {
    if (isPosition(v)) out[id] = { pct: Math.min(1, Math.max(0, v.pct)), ts: v.ts }
  }
  return out
}

export function getReadingPosition(id: string): ReadingPosition | null {
  if (!id) return null
  return readingPositions()[id] ?? null
}

function write(map: PositionMap): void {
  const entries = Object.entries(map).sort((a, b) => b[1].ts - a[1].ts).slice(0, MAX_ENTRIES)
  try { localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries))) }
  catch {   }
}

export function setReadingPosition(id: string, pct: number): void {
  if (!id || !Number.isFinite(pct)) return
  const map = readingPositions()
  if (pct < MIN_PCT || pct >= DONE_PCT) {
    if (!(id in map)) return
    delete map[id]
  } else {
    map[id] = { pct: Math.min(1, Math.max(0, pct)), ts: Date.now() }
  }
  write(map)
}

export function clearReadingPosition(id: string): void {
  if (!id) return
  const map = readingPositions()
  if (!(id in map)) return
  delete map[id]
  write(map)
}
