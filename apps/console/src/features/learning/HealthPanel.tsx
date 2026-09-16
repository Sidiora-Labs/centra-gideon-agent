import { LearningHeading, learningPanelClass } from './learningDisplay'
import { Activity, AlertTriangle } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { fvs } from '../../shared/theme/fontWeight'
import type {
  AttributionVerdict, HealthComponent, LearningHealth, MaeBucket,
} from '../../shared/data/api'

export function HealthPanel({ health, error, onRetry }: {
  health: LearningHealth | undefined
  error: unknown
  onRetry: () => void
}) {

  if (health === undefined) {
    if (!error) return null
    return <LoadError what="flywheel health" error={error} onRetry={onRetry} />
  }
  if (!health) return null

  const { composite, utilization, judge, attribution, cost_by_op: costByOp, ablation } = health
  const [low, high] = composite.ideal_band

  return (
    <section className={learningPanelClass} aria-labelledby="flywheel-health-heading">
      <LearningHeading id="flywheel-health-heading" icon={Activity} suffix={composite.measured > 0 && composite.measured < composite.of && <span className="inline-flex items-center gap-xs text-warn text-[0.75rem]" title="Components with no data are excluded from the composite rather than scored as zero — an un-instrumented subsystem is not a broken one."><AlertTriangle size={12} /> {composite.of - composite.measured} unmeasured</span>}>Flywheel health, last {health.days} days</LearningHeading>

      <div className="flex flex-wrap items-baseline gap-m rounded-lg bg-surface-container px-l py-l">
        <span data-type="display-s" className="text-on-surface" style={fvs(600)}>
          {composite.score === null ? '—' : composite.score}
        </span>
        <span className="text-on-surface-low text-[0.8125rem]">
          {composite.score === null
            ? 'not measured yet — nothing has run'
            : `of 100, from ${composite.measured} of ${composite.of} components`}
        </span>
      </div>

      <ul className="flex flex-col gap-s">
        {composite.components.map((component) => (
          <ComponentRow key={component.name} component={component} />
        ))}
      </ul>

      <p className="text-on-surface-low text-[0.75rem]">
        Context budget: {utilization.mean === null
          ? 'no ambient render recorded yet'
          : `${pct(utilization.mean)} used across ${utilization.samples} render${utilization.samples === 1 ? '' : 's'}`}
        {' · '}ideal band {pct(low)}–{pct(high)}
      </p>

      <MaePanel mae={judge.mae} runsScanned={judge.runs_scanned} verdicts={judge.verdicts} />

      <AttributionPanel attribution={attribution} />

      <CostPanel rows={costByOp} total={health.capture.cost_usd} />

      <AblationPanel ablation={ablation} />
    </section>
  )
}

const COMPONENT_LABEL: Record<HealthComponent['name'], string> = {
  precision: 'Surfacing precision',
  capture: 'Capture reliability',
  utilization: 'Budget utilization',
  judge: 'Judge trustworthiness',
}

function ComponentRow({ component }: { component: HealthComponent }) {
  const facts = `${COMPONENT_LABEL[component.name]} · weight ${pct(component.weight)}`
  return <li className="grid grid-cols-[1fr_auto] items-center gap-m rounded-lg border border-outline-variant/20 bg-surface-container p-m">
    <div><p className="text-on-surface text-[0.8125rem]">{facts}</p><p className="text-on-surface-var text-[0.75rem]">{component.detail}</p></div>
    <span data-type="title-s" className="text-on-surface" style={fvs(600)}>{component.score ?? '—'}</span>
  </li>
}

function MaePanel({ mae, runsScanned, verdicts }: {
  mae: LearningHealth['judge']['mae']; runsScanned: number; verdicts: number
}) {
  const measured = mae.buckets.length > 0 && verdicts > 0
  const detail = [
    `${mae.labelled} human-labelled of ${verdicts} verdict${verdicts === 1 ? '' : 's'} across ${runsScanned} run${runsScanned === 1 ? '' : 's'}`,
    mae.unlabelled > 0 ? `${mae.unlabelled} awaiting a label` : '',
    mae.no_confidence > 0 ? `${mae.no_confidence} recorded no samples` : '',
  ].filter(Boolean).join(' · ')
  return <div className="flex flex-col gap-s">
    <h3 data-type="title-s" className="text-on-surface">Judge calibration</h3>
    {!measured ? <p className="text-on-surface-low text-[0.75rem]">No judge verdicts in the last {runsScanned} run{runsScanned === 1 ? '' : 's'} — nothing to calibrate yet.</p> : <>
      <div className="grid grid-cols-[repeat(auto-fit,minmax(6rem,1fr))] gap-s">{mae.buckets.map(bucket => <MaeBucketCell key={bucket.bucket} bucket={bucket} />)}</div>
      <p className="text-on-surface-low text-[0.75rem]">{detail}. Error is only reported where a human overrode the judge — silence is not agreement.</p>
    </>}
  </div>
}

function MaeBucketCell({ bucket }: { bucket: MaeBucket }) {
  const title = `Predicted confidence ${bucket.bucket}: ${bucket.n} verdict${bucket.n === 1 ? '' : 's'}, ${bucket.labelled} human-labelled`
  const measurement = bucket.mae === null ? { score: '—', detail: `${bucket.n} unlabelled` } : { score: bucket.mae.toFixed(2), detail: `MAE · n=${bucket.labelled}` }
  return <div title={title} className="flex flex-col items-center gap-xs rounded-lg border border-outline-variant/20 bg-surface-container p-m">
    <span className="text-on-surface-low text-[0.75rem]">{bucket.bucket}</span>
    <span data-type="title-s" className="text-on-surface" style={fvs(600)}>{measurement.score}</span>
    <span className="text-on-surface-low text-[0.75rem]">{measurement.detail}</span>
  </div>
}

const VERDICT_LABEL: Record<AttributionVerdict, string> = {
  EFFECTIVE: 'Effective',
  PARTIALLY_EFFECTIVE: 'Partly effective',
  INEFFECTIVE: 'Ineffective',
  MIXED: 'Mixed',
  HARMFUL: 'Harmful',
  PENDING: 'Awaiting measurement',
}

const VERDICT_ORDER: AttributionVerdict[] = [
  'EFFECTIVE', 'PARTIALLY_EFFECTIVE', 'MIXED', 'INEFFECTIVE', 'HARMFUL', 'PENDING',
]

function AttributionPanel({ attribution }: { attribution: LearningHealth['attribution'] }) {
  const tally = attribution.history.reduce<Partial<Record<AttributionVerdict, number>>>((counts, entry) => {
    counts[entry.verdict] = (counts[entry.verdict] ?? 0) + 1
    return counts
  }, {})
  const badges = VERDICT_ORDER.flatMap(verdict => tally[verdict] === undefined ? [] : [{ verdict, count: tally[verdict] }])
  return <div className="flex flex-col gap-s">
    <h3 data-type="title-s" className="text-on-surface">Accepted-change outcomes</h3>
    {attribution.history.length === 0 ? <p className="text-on-surface-low text-[0.75rem]">No accepted change has been graded yet. A verdict lands once enough runs have gone past the change to measure it.</p> : <>
      <div className="flex flex-wrap gap-s">{badges.map(({ verdict, count }) => <span key={verdict} className="inline-flex items-center rounded-md bg-surface-high px-m py-xs text-on-surface-var text-[0.75rem]">{VERDICT_LABEL[verdict]} · {count}</span>)}</div>
      {attribution.proposers.length > 0 && <ul className="flex flex-col gap-xs">{attribution.proposers.map(proposer => <li key={proposer.source} className="text-on-surface-var text-[0.75rem]">{`${proposer.source}: ${proposer.decided} decided of ${proposer.total} · ${pct(proposer.effective_rate)} effective · ${pct(proposer.harm_rate)} harmful`}</li>)}</ul>}
    </>}
  </div>
}

function CostPanel({ rows, total }: { rows: LearningHealth['cost_by_op']; total: number }) {
  const lines = rows.map(row => ({ key: row.op, text: `${row.op}: $${row.cost_usd.toFixed(4)} over ${row.passes} pass${row.passes === 1 ? '' : 'es'}${row.cost_usd === 0 ? ' — unpriced or free' : ''}` }))
  return <div className="flex flex-col gap-s">
    <h3 data-type="title-s" className="text-on-surface">LLM cost by operation</h3>
    {lines.length === 0 ? <p className="text-on-surface-low text-[0.75rem]">No metered operation in this window.</p> : <ul className="flex flex-col gap-xs">
      {lines.map(line => <li key={line.key} className="text-on-surface-var text-[0.75rem]">{line.text}</li>)}
      <li className="text-on-surface-low text-[0.75rem]">Total ${total.toFixed(4)}</li>
    </ul>}
  </div>
}

const ABLATION_VERDICT_LABEL: Record<'no_effect' | 'earns_its_place', string> = {
  no_effect: 'no measurable effect — a candidate for removal',
  earns_its_place: 'changes what gets injected',
}

function AblationPanel({ ablation }: { ablation: LearningHealth['ablation'] }) {
  const lines = (ablation.rows ?? []).map(row => ({ name: row.heuristic, value: `${row.heuristic}: delta ${row.delta.toFixed(3)} — ${ABLATION_VERDICT_LABEL[row.verdict as keyof typeof ABLATION_VERDICT_LABEL]}` }))
  return <div className="flex flex-col gap-s">
    <h3 data-type="title-s" className="text-on-surface">Surfacing heuristics</h3>
    {lines.length ? <ul className="flex flex-col gap-xs">{lines.map(line => <li key={line.name} className="text-on-surface-var text-[0.75rem]">{line.value}</li>)}</ul> : <p className="text-on-surface-low text-[0.75rem]">No ablation sweep has run yet. One runs daily alongside an ambient render.</p>}
  </div>
}

function pct(value: number): string {
  return `${Math.round(value * 100)}%`
}
