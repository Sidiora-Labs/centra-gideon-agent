import { useState } from 'react'
import { MoreRow } from '../../../shared/ui/MoreRow'
import { CalendarClock, ChevronDown, ChevronRight, Plus } from 'lucide-react'
import { useDashboardLive } from '../DashboardLive'
import { statusMeta, relPast, relFuture } from '../../schedule/scheduleMeta'
import { epochSeconds } from '../../../shared/data/epoch'
import { SlotEmptyState, SlotAction, WidgetRow, StatusDot } from './kit'
import { ListSkeleton } from '../../../shared/ui/ListScaffold'
import { partitionRuns } from './scheduleFold'
import { Button } from '../../../shared/ui/Button'
import type { RouteProps } from '../../../app/shell/useQueryState'
import { rowSubject } from '../../../shared/data/rowSubject'

function rel(ts?: number | string | null): string {
  const secs = epochSeconds(ts)
  if (secs == null) return ''
  return secs <= Date.now() / 1000 ? relPast(secs) : relFuture(secs)
}

export function ScheduleWidget({ navigate }: RouteProps) {
  const { schedule, scheduleDidIds, scheduleSuppressed, read } = useDashboardLive()
  const [showSuppressed, setShowSuppressed] = useState(false)

  if (schedule.length === 0) {
    if (!read.schedule) return <ListSkeleton rows={4} what="recent scheduled runs" />
    return (
      <SlotEmptyState
        icon={CalendarClock}
        action={<SlotAction icon={Plus} onClick={() => navigate('triggers/new')}>New trigger</SlotAction>}
      >No recent scheduled runs. Triggers you set up appear here as they fire.</SlotEmptyState>
    )
  }

  const { did, suppressed } = partitionRuns(schedule, scheduleDidIds)
  const visible = showSuppressed ? [...did, ...suppressed] : did

  const row = (r: typeof schedule[number], i: number) => {
    const o = statusMeta(r.outcome ?? r.status)
    const when = r.finished_at ?? r.started_at
    const origin = r.job_id && r.job_id !== 'day-budget'
      ? `triggers?open=${encodeURIComponent(`schedule:${r.job_id}`)}`
      : 'triggers'
    return (
      <WidgetRow key={r.id ?? r.run_id ?? `${r.job_id}-${i}`} onClick={() => navigate(origin)}
        label={rowSubject([r.job_name || r.job_id || 'Schedule', statusMeta(r.outcome ?? r.status).label])}>
        <div className="flex items-center gap-s">
          <StatusDot color={o.tone} />
          <div className="min-w-0 flex-1">
            <p data-type="title-m" className="truncate text-on-surface">{r.job_name || r.job_id || 'Schedule'}</p>
            <p data-type="body-m" className="truncate text-on-surface-low">
              <span style={{ color: o.tone }}>{o.label}</span>{r.trigger ? ` · ${r.trigger}` : ''}
            </p>
          </div>
          <span data-type="body-m" className="shrink-0 text-on-surface-low">{rel(when)}</span>
        </div>
      </WidgetRow>
    )
  }

  return (
    <div className="flex flex-col gap-xs pt-xs">
      {did.length === 0 && !showSuppressed && (
        <p data-type="body-m" className="px-xs text-on-surface-low">
          Nothing ran recently — every recent fire was held by a gate.
        </p>
      )}
      {visible.slice(0, 6).map(row)}
      {
}
      <MoreRow total={visible.length} shown={6} />
      {scheduleSuppressed > 0 && (
        <Button
          variant="ghost"
          size="xs"
          ariaExpanded={showSuppressed}
          onClick={() => setShowSuppressed((v) => !v)}
          className="self-start px-xs text-on-surface-low"
        >
          {showSuppressed ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          {showSuppressed
            ? `Hide ${scheduleSuppressed} suppressed by a gate`
            : `Show ${scheduleSuppressed} suppressed by a gate`}
        </Button>
      )}
    </div>
  )
}
