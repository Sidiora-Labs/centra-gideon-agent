import { LearningHeading, LearningTable, learningPanelClass } from './learningDisplay'
import { Eye, TrendingDown, TrendingUp } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { StatusPill } from '../../shared/ui/StatusPill'
import type { AttentionScope } from '../../shared/data/api'

export function AttentionPanel({ scopes, error, onRetry }: {
  scopes: AttentionScope[] | undefined
  error: unknown
  onRetry: () => void
}) {
  if (scopes === undefined) {
    if (!error) return null
    return <LoadError what="attention accounting" error={error} onRetry={onRetry} />
  }
  if (!scopes) return null

  return (
    <section className={learningPanelClass} aria-labelledby="attention-heading">
      <Heading />

      {scopes.length === 0 ? (
        <p className="text-on-surface-low text-[0.8125rem]">
          No workflow runs recorded yet. Once workflows run, each template's attention
          cost — gate answers, mid-flight edits, judge overrides — is tallied here, per
          run, with a trend the graduation proposal cites.
        </p>
      ) : (
        <>
          <p className="text-on-surface-low text-[0.75rem]">
            Attention events are human gate answers, mid-flight edits, and judge
            divergences — an auto-approved gate counts for nothing. Debt decays on a
            7-day half-life, so last night's intervention weighs on today's graduation
            question and last month's does not.
          </p>
          <LearningTable caption="Human-attention accounting per workflow template" rows={scopes} rowKey={row => row.scope} columns={[
            { name: 'Template', render: row => row.scope },
            { name: 'Runs', align: 'right', render: row => row.runs },
            { name: 'Events/run', align: 'right', render: row => row.events_per_run.toFixed(2) },
            { name: 'p50 dwell', align: 'right', render: row => fmtDwell(row.dwell_p50_secs) },
            { name: 'Debt', align: 'right', render: row => row.debt.toFixed(2) },
            { name: 'Trend', render: row => <TrendChip trend={row.trend} /> },
          ]} />
        </>
      )}
    </section>
  )
}

function Heading() {
  return <LearningHeading id="attention-heading" icon={Eye} suffix={null}>Attention</LearningHeading>
}

function TrendChip({ trend }: { trend: AttentionScope['trend'] }) {
  const label = trend || 'too few runs'
  switch (trend) {
    case 'rising': return <StatusPill tone="warn" className="gap-1.5 h-6 px-m w-fit"><TrendingUp size={12} /> {label}</StatusPill>
    case 'falling': return <span className="inline-flex items-center gap-1.5 text-on-surface-var"><TrendingDown size={12} /> {label}</span>
    default: return <span className={trend ? 'text-on-surface-var' : 'text-on-surface-low'}>{trend ? 'flat' : label}</span>
  }
}

function fmtDwell(secs: number): string {
  if (secs === 0) return '—'
  const [divisor, unit] = secs >= 60 ? [60, 'm'] as const : [1, 's'] as const
  return `${(secs / divisor).toFixed(1)}${unit}`
}
