import { Scale, Clock, CircleAlert, BellOff, Brain, MessageSquare } from 'lucide-react'
import { EmptyState, ListRow, ListSkeleton, LoadError } from '../../shared/ui/ListScaffold'
import { api, type CalibrationBucket, type DecisionRow } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { fvs } from '../../shared/theme/fontWeight'
import {
  bucketLabel,
  bucketPlottable,
  calibrationCaption,
  calibrationState,
  confidenceLabel,
  gradeLabel,
  horizonLabel,
  pendingState,
} from './decisionMeta'

export function DecisionJournal({ onOpenItem, onOpenChat }: { onOpenItem: (id: string) => void
  onOpenChat: () => void }) {
  const { data, loading, error, refresh } = useQuery('knowledge:decisions', () => api.decisionJournal())

  if (loading && !data) return <ListSkeleton rows={4} what="decision journal" />
  if (error) return <LoadError what="decision journal" error={error} onRetry={refresh} />
  if (!data) return null

  const pending = data.decisions.filter((d) => d.status === 'pending')
  const resolved = data.decisions.filter((d) => d.status === 'resolved')

  if (data.decisions.length === 0) {
    return (
      <EmptyState
        icon={Scale}
        title="No decisions logged yet"
        hint="Log a decision in chat — what you decided, what you expect to happen, and how confident you are. It comes back on its own when the horizon arrives."
        action={{ label: 'Open chat', onClick: onOpenChat, icon: MessageSquare }}
      />
    )
  }

  return (
    <div className="flex flex-col gap-xl">
      <CalibrationStrip view={data} />
      {pending.length > 0 && (
        <section aria-labelledby="decisions-pending" className="flex flex-col gap-s">
          <h2 id="decisions-pending" data-type="title-s" className="text-on-surface-low">Open ({pending.length})</h2>
          {pending.map((d, i) => <PendingRow key={d.id} d={d} index={i} onOpen={() => onOpenItem(d.id)} />)}
        </section>
      )}
      {resolved.length > 0 && (
        <section aria-labelledby="decisions-resolved" className="flex flex-col gap-s">
          <h2 id="decisions-resolved" data-type="title-s" className="text-on-surface-low">Resolved ({resolved.length})</h2>
          {resolved.map((d, i) => <ResolvedRow key={d.id} d={d} index={i} onOpen={() => onOpenItem(d.id)} />)}
        </section>
      )}
    </div>
  )
}

function DomainTag({ domain }: { domain: string }) {
  return (
    <span data-type="caption" className="shrink-0 rounded-full px-2 py-[1px] text-on-surface-low"
      style={{ background: 'color-mix(in srgb, var(--color-primary) 10%, transparent)' }}>{domain}</span>
  )
}

function CalibrationStrip({ view }: { view: Parameters<typeof calibrationCaption>[0] }) {
  const state = calibrationState(view.calibration)
  const domains = Object.entries(view.calibration).sort((a, b) => b[1].n - a[1].n)
  return (
    <section aria-labelledby="decisions-calibration" className="rounded-xl border border-outline-variant p-l">
      <div className="flex items-center gap-s">
        <Scale size={16} className="text-primary" aria-hidden />
        <h2 id="decisions-calibration" data-type="title-s" className="text-on-surface">Calibration</h2>
      </div>
      {
}
      <p data-type="body-m" className="mt-1 text-on-surface-low" data-calibration-state={state}>
        {calibrationCaption(view)}
      </p>
      {domains.length > 0 && (
        <ul className="mt-l flex flex-col gap-s">
          {domains.map(([domain, b]) => (
            <li key={domain} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-s">
                <span data-type="title-m" className="text-on-surface" style={fvs(500)}>{domain}</span>
                <span data-type="body-s" className="text-on-surface-low tabular-nums">{bucketLabel(b, view.calibration_min_n)}</span>
              </div>
              <Bar b={b} />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function Bar({ b }: { b: CalibrationBucket }) {
  if (!bucketPlottable(b)) {
    return (
      <p data-type="body-s" className="text-on-surface-low opacity-80">
        Not enough resolved decisions in this domain to draw a rate.
      </p>
    )
  }
  const pct = Math.round((b.as_expected_rate ?? 0) * 100)
  return (
    <div className="flex h-[6px] overflow-hidden rounded-full" role="img"
      aria-label={`${b.better} better than expected, ${b.as_expected} as expected, ${b.worse} worse than expected`}>
      <span className="h-full" style={{ width: `${(b.better / b.n) * 100}%`, background: 'var(--color-ok)' }} />
      <span className="h-full" style={{ width: `${pct}%`, background: 'var(--color-primary)' }} />
      <span className="h-full" style={{ width: `${(b.worse / b.n) * 100}%`, background: 'var(--color-warn)' }} />
    </div>
  )
}

function PendingRow({ d, index, onOpen }: { d: DecisionRow; index: number; onOpen: () => void }) {
  const state = pendingState(d)
  const Icon = state === 'stale' ? BellOff : state === 'overdue' ? CircleAlert : Clock
  const tone = state === 'stale' ? 'var(--color-warn)' : state === 'overdue' ? 'var(--color-danger)' : 'var(--color-on-surface-low)'
  return (
    <ListRow index={index} onClick={onOpen} label={d.summary} accent={state === 'counting' ? undefined : tone}>
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex min-w-0 items-center gap-s">
          <span className="truncate text-on-surface" style={fvs(500)}>{d.summary}</span>
          <DomainTag domain={d.domain} />
        </div>
        <p data-type="body-m" className="line-clamp-2 text-on-surface-low">
          <span className="text-on-surface-low opacity-80">Expected: </span>{d.expectation}
        </p>
        <div data-type="body-s" className="flex flex-wrap items-center gap-s" style={{ color: tone }}>
          <Icon size={13} aria-hidden />
          <span data-pending-state={state}>{horizonLabel(d)}</span>
          <span className="text-on-surface-low opacity-80">· {confidenceLabel(d)}</span>
        </div>
      </div>
    </ListRow>
  )
}

function ResolvedRow({ d, index, onOpen }: { d: DecisionRow; index: number; onOpen: () => void }) {
  const grade = d.outcome_grade || ''
  const tone = grade === 'better' ? 'var(--color-ok)' : grade === 'worse' ? 'var(--color-warn)' : 'var(--color-primary)'
  return (
    <ListRow index={index} onClick={onOpen} label={d.summary} accent={tone}>
      <div className="flex min-w-0 flex-col gap-s">
        <div className="flex min-w-0 items-center gap-s">
          <span className="truncate text-on-surface" style={fvs(500)}>{d.summary}</span>
          <DomainTag domain={d.domain} />
          {
}
          <span data-type="body-s" className="shrink-0" style={{ color: tone }}>{gradeLabel(grade)}</span>
        </div>
        <dl className="grid gap-s sm:grid-cols-2">
          <div className="min-w-0">
            <dt data-type="caption" className="text-on-surface-low uppercase tracking-wide">Expected</dt>
            <dd data-type="body-m" className="mt-1 text-on-surface">{d.expectation}</dd>
          </div>
          <div className="min-w-0">
            <dt data-type="caption" className="text-on-surface-low uppercase tracking-wide">What happened</dt>
            <dd data-type="body-m" className="mt-1 text-on-surface">{d.outcome || '—'}</dd>
          </div>
        </dl>
        <div data-type="body-s" className="flex flex-wrap items-center gap-s text-on-surface-low">
          <span>{confidenceLabel(d)}</span>
          {
}
          {d.lesson_memory_key
            ? <span className="inline-flex items-center gap-1"><Brain size={12} aria-hidden /> lesson recorded</span>
            : <span className="opacity-80">no lesson recorded</span>}
        </div>
      </div>
    </ListRow>
  )
}
