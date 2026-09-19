import type { WeekOccurrence } from '../../shared/data/api'


export type CellState =
  | 'empty'
  | 'fires'
  | 'quiet'
  | 'skipped'
  | 'mixed'

export type WeekCell = {
  day: number
  hour: number
  state: CellState
  count: number
  liveCount: number
  triggers: string[]
  triggerIds: string[]
  reasons: string[]
}

export type WeekGrid = {
  cells: WeekCell[]
  days: Date[]
  totalFires: number
  suppressedFires: number
  emptyHours: number[]
  outsideWindow: number
}

export const DAYS_IN_WEEK = 7
export const HOURS_IN_DAY = 24

export function startOfDay(d: Date): Date {
  const out = new Date(d)
  out.setHours(0, 0, 0, 0)
  return out
}

export function weekDays(start: Date): Date[] {
  const first = startOfDay(start)
  return Array.from({ length: DAYS_IN_WEEK }, (_, i) => {
    const d = new Date(first)
    d.setDate(d.getDate() + i)
    return d
  })
}

export function weekEnd(start: Date): Date {
  const first = startOfDay(start)
  const end = new Date(first)
  end.setDate(end.getDate() + DAYS_IN_WEEK)
  return end
}

export function dayIndex(days: Date[], at: number): number {
  const m = new Date(at * 1000)
  const key = `${m.getFullYear()}-${m.getMonth()}-${m.getDate()}`
  return days.findIndex((d) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}` === key)
}

function cellState(count: number, live: number, suppressedKinds: Set<string>): CellState {
  if (count === 0) return 'empty'
  if (live === count) return 'fires'
  if (live > 0) return 'mixed'
  if (suppressedKinds.has('skipped')) return 'skipped'
  if (suppressedKinds.has('quiet')) return 'quiet'
  return 'fires'
}

export function buildWeekGrid(occurrences: readonly WeekOccurrence[], start: Date): WeekGrid {
  const days = weekDays(start)
  const cells: WeekCell[] = []
  const index = new Map<string, WeekCell>()
  const kinds = new Map<string, Set<string>>()
  for (let day = 0; day < DAYS_IN_WEEK; day++) {
    for (let hour = 0; hour < HOURS_IN_DAY; hour++) {
      const cell: WeekCell = { day, hour, state: 'empty', count: 0, liveCount: 0, triggers: [], triggerIds: [], reasons: [] }
      cells.push(cell)
      index.set(`${day}:${hour}`, cell)
      kinds.set(`${day}:${hour}`, new Set())
    }
  }

  let total = 0
  let suppressed = 0
  let outside = 0
  for (const o of occurrences ?? []) {
    const day = dayIndex(days, o.at)
    if (day < 0) {
      outside++
      continue
    }
    const hour = new Date(o.at * 1000).getHours()
    const cell = index.get(`${day}:${hour}`)
    if (!cell) continue
    cell.count++
    total++
    if (o.suppressed_by) {
      suppressed++
      kinds.get(`${day}:${hour}`)?.add(o.suppressed_by)
      if (o.reason && !cell.reasons.includes(o.reason)) cell.reasons.push(o.reason)
    } else {
      cell.liveCount++
    }
    if (o.trigger_id && !cell.triggerIds.includes(o.trigger_id)) {
      cell.triggerIds.push(o.trigger_id)
      cell.triggers.push(o.trigger_name || o.trigger_id)
    }
  }

  for (const cell of cells) {
    cell.state = cellState(cell.count, cell.liveCount, kinds.get(`${cell.day}:${cell.hour}`) ?? new Set())
  }

  const emptyHours: number[] = []
  for (let hour = 0; hour < HOURS_IN_DAY; hour++) {
    if (cells.every((c) => c.hour !== hour || c.count === 0)) emptyHours.push(hour)
  }

  return { cells, days, totalFires: total, suppressedFires: suppressed, emptyHours, outsideWindow: outside }
}

export function visibleHours(grid: WeekGrid): number[] {
  const all = Array.from({ length: HOURS_IN_DAY }, (_, h) => h)
  if (grid.totalFires === 0) return all
  return all.filter((h) => !grid.emptyHours.includes(h))
}

export function cellLabel(cell: WeekCell, day: Date): string {
  const when = `${day.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })} ${String(cell.hour).padStart(2, '0')}:00`
  if (cell.count === 0) return `${when} — no fires`
  const names = cell.triggers.join(', ')
  const fires = `${cell.count} fire${cell.count === 1 ? '' : 's'}`
  if (cell.state === 'fires') return `${when} — ${fires}: ${names}`
  const why = cell.reasons.join('; ')
  if (cell.state === 'mixed') return `${when} — ${fires}, ${cell.count - cell.liveCount} suppressed (${why}): ${names}`
  return `${when} — ${fires}, all suppressed (${why}): ${names}`
}

export function weekSummary(grid: WeekGrid): string {
  if (grid.totalFires === 0) return 'No scheduled fires this week'
  const live = grid.totalFires - grid.suppressedFires
  const base = `${live} fire${live === 1 ? '' : 's'} this week`
  const parts = [base]
  if (grid.suppressedFires > 0) parts.push(`${grid.suppressedFires} suppressed`)
  if (grid.outsideWindow > 0) parts.push(`${grid.outsideWindow} outside the drawn week`)
  return parts.join(' · ')
}
