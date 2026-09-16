import { LearningHeading, LearningTable, learningPanelClass, measurement, measuredRate, groupMeasurements } from './learningDisplay'
import { Gavel, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { fvs } from '../../shared/theme/fontWeight'
import { hasApiCode } from '../../shared/data/api'
import type { JudgeBenchRecommendation, JudgeBenchRow, JudgeBenchView } from '../../shared/data/api'
import { EvalsOff } from './EvalsOff'

export function JudgeBenchPanel({ bench, error, onRetry }: {
  bench: JudgeBenchView | undefined
  error: unknown
  onRetry: () => void
}) {

  if (bench === undefined) {
    if (!error) return null
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className={learningPanelClass} aria-labelledby="judge-bench-heading">
          <Heading />
          <EvalsOff what="judge benchmark" />
        </section>
      )
    }
    if (hasApiCode(error, 'judge_bench_absent')) {
      return (
        <section className={learningPanelClass} aria-labelledby="judge-bench-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No benchmark has run yet. Run <code className="text-on-surface-var">gideon judge-bench</code>{' '}
            to measure which model tier each rubric actually needs. It is a deliberate command,
            not a background job — the full matrix is hundreds of judge calls, so nothing here
            spends money on its own.
          </p>
        </section>
      )
    }
    return <LoadError what="judge benchmark" error={error} onRetry={onRetry} />
  }
  if (!bench) return null

  const classes = groupMeasurements(bench.rows, row => row.rubric_class)

  return (
    <section className={learningPanelClass} aria-labelledby="judge-bench-heading">
      <Heading benchId={bench.bench_id} />

      <p className="text-on-surface-low text-[0.75rem]">
        Adequate means: agreement ≥ {fmt(bench.floors.agreement)} · strong-vs-null separation ≥{' '}
        {fmt(bench.floors.separation)} · position-swap flip rate ≤ {fmt(bench.floors.flip_rate)} ·
        and no forbidden-success-mode case passed. A floor is not a preference: one missed
        disqualifier rules a tier out on its own.
      </p>

      {bench.recommendations.map((rec) => (
        <RecommendationCard key={rec.rubric_class} rec={rec} />
      ))}

      {Array.from(classes, ([rubricClass, rubricRows]) => (
        <div key={rubricClass} className="flex flex-col gap-xs">
          <span data-type="title-s" className="text-on-surface">{rubricClass}</span>
          <LearningTable caption={`Judge benchmark results for the ${rubricClass} rubric class, by tier and sample count`} rows={rubricRows} rowKey={row => `${row.tier}-${row.samples}`} columns={[
            { name: 'Tier', render: row => row.tier },
            { name: 'Samples', align: 'right', render: row => row.samples },
            { name: 'Agreement', align: 'right', render: row => fmtRate(row.agreement) },
            { name: 'Separation', align: 'right', render: row => fmtNum(row.separation) },
            { name: 'Flip rate', align: 'right', render: row => fmtRate(row.flip_rate) },
            { name: 'Cost', align: 'right', render: row => fmtCost(row.cost_usd) },
            { name: 'Wall', align: 'right', render: row => `${row.wall_secs.toFixed(1)}s` },
            { name: 'Verdict', render: row => <JudgeVerdict row={row} /> },
          ]} />
        </div>
      ))}
    </section>
  )
}

function Heading({ benchId }: { benchId?: string }) {
  return <LearningHeading id="judge-bench-heading" icon={Gavel} suffix={benchId && <span className="text-on-surface-low text-[0.75rem]">{benchId}</span>}>Judge tiers</LearningHeading>
}

function JudgeVerdict({ row }: { row: JudgeBenchRow }) {
  const details = [
    ...(!row.adequate ? row.inadequate_reasons : []),
    ...row.notes,
  ]
  return <div className="flex flex-col gap-xs">
    {row.adequate ? <span className="text-on-surface" style={fvs(600)}>adequate</span> : <span className="inline-flex items-center gap-xs text-warn"><ShieldAlert size={12} /> not adequate</span>}
    {details.length > 0 && <ul className="flex flex-col gap-0.5 text-on-surface-low">{details.map((detail, index) => <li key={index}>{detail}</li>)}</ul>}
  </div>
}

function RecommendationCard({ rec }: { rec: JudgeBenchRecommendation }) {
  const recommended = rec.verdict === 'recommended'
  const refusals = new Map([['cost_unknown', 'cheapest tier unknown']])
  const title = recommended ? `${rec.tier} at ${rec.samples} sample${rec.samples === 1 ? '' : 's'}` : refusals.get(rec.verdict) ?? 'no adequate tier'
  return (
    <div className="flex flex-col gap-s rounded-xl border border-outline-variant/30 bg-surface-container p-l">
      <span data-type="title-s" className="text-on-surface">
        {rec.rubric_class}:{' '}
        {title}
      </span>
      {recommended && (
        <p className="text-on-surface-low text-[0.8125rem]">
          Bind <code className="text-on-surface-var">{rec.use_case}</code>
          {rec.model_ref ? <> to <code className="text-on-surface-var">{rec.model_ref}</code></> : null}{' '}
          on <a className="underline" href="#/settings/models">Settings → Models</a>. The harness
          recommends; you rebind.
        </p>
      )}
      <ul className="flex flex-col gap-0.5 text-on-surface-low text-[0.75rem]">
        {rec.notes.map((note) => <li key={note}>{note}</li>)}
      </ul>
    </div>
  )
}

function fmt(value: number | undefined): string {
  return value?.toString() ?? '—'
}

function fmtRate(value: number | null): string {
  return measuredRate(value)
}

function fmtNum(value: number | null): string {
  return measurement(value, 2)
}

function fmtCost(value: number | null): string {
  return value === null ? 'unknown' : `$${measurement(value, 4)}`
}
