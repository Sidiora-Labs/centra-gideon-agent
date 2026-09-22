import { useMemo, useState } from 'react'
import { CalendarDays, ChevronLeft, ChevronRight, AlertTriangle } from 'lucide-react'
import { fvs } from '../../shared/theme/fontWeight'
import { EmptyState } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { useQuery } from '../../shared/data/data'
import { api, type WeekProjection } from '../../shared/data/api'
import { buildWeekGrid, cellLabel, visibleHours, weekSummary, startOfDay, weekEnd, type CellState, type WeekCell } from './weekGrid'


const CELL_TONE: Record<CellState, { bg: string; fg: string }> = {
  empty: { bg: 'transparent', fg: 'var(--color-on-surface-low)' },
  fires: { bg: 'color-mix(in srgb, var(--color-primary) 68%, transparent)', fg: 'var(--color-on-primary)' },
  quiet: { bg: 'color-mix(in srgb, var(--color-warn) 26%, transparent)', fg: 'var(--color-on-surface-var)' },
  skipped: { bg: 'color-mix(in srgb, var(--color-on-surface-low) 20%, transparent)', fg: 'var(--color-on-surface-low)' },
  mixed: { bg: 'color-mix(in srgb, var(--color-primary) 34%, transparent)', fg: 'var(--color-on-surface)' },
}

export function WeekGridView({ onOpenTrigger }: { onOpenTrigger?: (triggerId: string) => void }) {
  const [offset, setOffset] = useState(0)

  const start = useMemo(() => {
    const d = startOfDay(new Date())
    d.setDate(d.getDate() + offset * 7)
    return d
  }, [offset])

  const { data: week } = useQuery<WeekProjection>(
    `triggers:week:${start.getTime()}`,
    () => api.triggersWeek(start, 7, weekEnd(start)),
    { persist: false },
  )

  const grid = useMemo(() => buildWeekGrid(week?.occurrences ?? [], start), [week, start])
  const hours = useMemo(() => visibleHours(grid), [grid])
  const cellAt = useMemo(() => {
    const m = new Map<string, WeekCell>()
    for (const c of grid.cells) m.set(`${c.day}:${c.hour}`, c)
    return m
  }, [grid])

  const viewerTz = Intl.DateTimeFormat().resolvedOptions().timeZone
  const tzMismatch = Boolean(week?.server_tz && viewerTz && week.server_tz !== viewerTz)

  return (
    <div className="mx-auto px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
      <div className="mb-m flex flex-wrap items-center justify-between gap-s">
        <div className="min-w-0">
          <div className="flex items-center gap-s">
            <span data-type="title-s" className="text-on-surface">{weekLabel(grid.days)}</span>
            {offset !== 0 && <Button size="sm" variant="ghost" onClick={() => setOffset(0)}>Today</Button>}
          </div>
          <div className="mt-0.5 text-on-surface-low text-[0.8125rem]">
            {week === undefined ? 'Projecting…' : weekSummary(grid)}
            {tzMismatch && <span> · times in {viewerTz} (server: {week?.server_tz})</span>}
          </div>
        </div>
        <div className="flex items-center gap-xs">
          {
}
          <Button size="sm" variant="ghost" ariaLabel="Previous week" onClick={() => setOffset((o) => o - 1)}><ChevronLeft size={15} /></Button>
          <Button size="sm" variant="ghost" ariaLabel="Next week" onClick={() => setOffset((o) => o + 1)}><ChevronRight size={15} /></Button>
        </div>
      </div>

      {
}
      {week && week.truncated.length > 0 && (
        <div className="mb-m flex items-start gap-s rounded-lg bg-surface-high p-s text-[0.8125rem] text-on-surface-var">
          <AlertTriangle size={15} style={{ color: 'var(--color-warn)' }} className="mt-0.5 shrink-0" />
          <span>
            {week.truncated.length} trigger{week.truncated.length === 1 ? '' : 's'} fire too often to plot in full
            — this week is partial for {week.truncated.join(', ')}.
          </span>
        </div>
      )}

      {week !== undefined && grid.totalFires === 0 ? (
        <EmptyState
          icon={CalendarDays}
          title="No fires this week"
          hint="Only enabled schedules with a fire inside this week are plotted — one-shot, interval, and cron alike. A disabled trigger has no fires."
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-separate" style={{ borderSpacing: '2px' }}>
            <caption className="sr-only">
              Scheduled trigger fires by day and hour. Shaded cells are suppressed by a quiet window or a skip date.
            </caption>
            <thead>
              <tr>
                <th scope="col" className="w-12 text-right text-on-surface-low text-[0.75rem]" style={fvs(500)}>
                  <span className="sr-only">Hour</span>
                </th>
                {grid.days.map((d, i) => (
                  <th key={i} scope="col" className="px-1 pb-1 text-center text-[0.75rem] text-on-surface-var" style={fvs(500)}>
                    <div>{d.toLocaleDateString(undefined, { weekday: 'short' })}</div>
                    <div className="text-on-surface-low">{d.getDate()}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {hours.map((hour) => (
                <tr key={hour}>
                  <th scope="row" className="pr-1 text-right align-middle text-on-surface-low text-[0.75rem] tabular-nums" style={fvs(400)}>
                    {String(hour).padStart(2, '0')}
                  </th>
                  {grid.days.map((day, di) => {
                    const cell = cellAt.get(`${di}:${hour}`)
                    if (!cell) return <td key={di} />
                    const tone = CELL_TONE[cell.state]
                    const label = cellLabel(cell, day)
                    const clickable = cell.count > 0 && Boolean(onOpenTrigger)
                    const open = () => {
                      const id = cell.triggerIds[0] ?? ''
                      if (id && onOpenTrigger) onOpenTrigger(id)
                    }
                    return (
                      <td
                        key={di}
                        role={clickable ? 'button' : undefined}
                        tabIndex={clickable ? 0 : undefined}
                        aria-label={label}
                        title={label}
                        onClick={clickable ? open : undefined}
                        onKeyDown={
                          clickable
                            ? (e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault()
                                  open()
                                }
                              }
                            : undefined
                        }
                        data-type="caption"
                        className="h-6 rounded text-center tabular-nums transition-colors"
                        style={{
                          background: tone.bg,
                          color: tone.fg,
                          cursor: clickable ? 'pointer' : 'default',
                          textDecoration: cell.state === 'skipped' ? 'line-through' : undefined,
                          border:
                            cell.count === 0 ? '1px solid var(--color-outline-var)' : '1px solid transparent',
                        }}
                      >
                        {cell.count > 0 ? cell.count : ''}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <Legend />
        </div>
      )}
    </div>
  )
}

function Legend() {
  const items: Array<{ state: CellState; label: string }> = [
    { state: 'fires', label: 'Will run' },
    { state: 'mixed', label: 'Partly suppressed' },
    { state: 'quiet', label: 'Quiet hours' },
    { state: 'skipped', label: 'Skip date' },
  ]
  return (
    <div className="mt-m flex flex-wrap items-center gap-m text-on-surface-low text-[0.75rem]">
      {items.map((it) => (
        <span key={it.state} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block size-3 rounded"
            style={{
              background: CELL_TONE[it.state].bg,
              border: '1px solid transparent',
              textDecoration: it.state === 'skipped' ? 'line-through' : undefined,
            }}
          />
          {it.label}
        </span>
      ))}
    </div>
  )
}

function weekLabel(days: Date[]): string {
  if (days.length === 0) return ''
  const a = days[0], b = days[days.length - 1]
  const sameMonth = a.getMonth() === b.getMonth()
  const fmt = (d: Date, withMonth: boolean) =>
    d.toLocaleDateString(undefined, withMonth ? { month: 'short', day: 'numeric' } : { day: 'numeric' })
  return `${fmt(a, true)} – ${fmt(b, !sameMonth)}`
}
