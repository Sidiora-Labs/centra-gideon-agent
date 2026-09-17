import { LearningHeading, LearningTable, learningPanelClass, measurement, signedMeasurement, measuredRate } from './learningDisplay'
import { documentationUrl } from '../../app/shell/config'
import { ExternalLink, FlaskConical, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { StatusPill } from '../../shared/ui/StatusPill'
import { fvs } from '../../shared/theme/fontWeight'
import { hasApiCode } from '../../shared/data/api'
import { UNRECORDED_LABEL, provenanceRecorded, tokensUnrecorded } from '../../shared/data/unrecorded'
import { EvalsOff } from './EvalsOff'
import type {
  BenchmarkArmAggregate, BenchmarkProviderBinding, BenchmarkReport, BenchmarkTaskRow,
  BenchmarkView,
} from '../../shared/data/api'

export function BenchmarkPanel({ view, error, onRetry }: {
  view: BenchmarkView | undefined
  error: unknown
  onRetry: () => void
}) {
  if (view === undefined) {
    if (!error) return null
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className={learningPanelClass} aria-labelledby="skillbench-heading">
          <Heading />

          <EvalsOff what="benchmark" />
        </section>
      )
    }
    if (hasApiCode(error, 'learning_benchmark_absent')) {
      return (
        <section className={learningPanelClass} aria-labelledby="skillbench-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No skill-impact benchmark has run yet. Start with{' '}
            <code className="text-on-surface-var">
              python scripts/learning_benchmark.py --preflight
            </code>{' '}
            — it calls no model — then <code className="text-on-surface-var">--run</code>. The full
            paired design is 100 real model calls, so nothing here starts one on a click.
          </p>
          <MethodologyLink doc="docs/reference/LEARNING_BENCHMARK_PROTOCOL.md" />
        </section>
      )
    }
    return <LoadError what="skill-impact benchmark" error={error} onRetry={onRetry} />
  }
  if (!view) return null

  const report = view.report
  const rows = orderedRows(view)

  return (
    <section className={learningPanelClass} aria-labelledby="skillbench-heading">
      <Heading runId={report.run_id} />

      <p className="text-on-surface-low text-[0.8125rem]">
        Does a skill you approved make the next run better? Two arms over identical work —{' '}
        <span className="text-on-surface-var">skills_on</span> and{' '}
        <span className="text-on-surface-var">skills_off</span> — at{' '}
        {report.trials_per_arm} trial{report.trials_per_arm === 1 ? '' : 's'} per arm, each in a
        fresh seeded fixture home. Task set v{report.task_set_version}
        {report.created_at ? <> · {report.created_at}</> : null}.
      </p>

      <Provenance report={report} />

      <Coverage report={report} registerSize={view.register.length} />

      <LearningTable caption={`Per-task skills-on versus skills-off results for task set v${report.task_set_version}, with unmeasured tasks shown as not measured rather than as zero`} rows={rows} rowKey={row => row.task_id} columns={[
        { name: 'Task', render: row => row.task_id },
        { name: 'Skill', render: row => row.skill },
        { name: 'Verdict', render: row => <TaskVerdict row={row} /> },
        { name: 'Delta (pts)', align: 'right', render: row => fmtDelta(row.delta_points) },
        { name: 'Token ratio', align: 'right', render: row => fmtRatio(row.token_ratio, row) },
        { name: 'skills_on', align: 'right', render: row => fmtArm(row.arms.skills_on) },
        { name: 'skills_off', align: 'right', render: row => fmtArm(row.arms.skills_off) },
        { name: 'Absent', align: 'right', render: row => row.absent_cells },
      ]} />

      <p className="text-on-surface-low text-[0.75rem]">
        A delta under {report.thresholds.inconclusive_band_points} points is reported as{' '}
        <span className="text-on-surface-var">inconclusive</span>, including in our favour, and a
        delta smaller than its own arm&apos;s spread is unresolved too. Arms whose spend differs by
        more than {pct(report.thresholds.token_match_tolerance)} yield{' '}
        <span className="text-on-surface-var">not_token_matched</span> — the measurement declining a
        question it did not ask. Below{' '}
        {report.thresholds.min_trials_per_arm} trials per arm, no verdict is offered at all.
        Thresholds come from <code className="text-on-surface-var">{report.thresholds.source}</code>,
        not from this page.
      </p>

      {report.reproduction && <Reproduction repro={report.reproduction} />}

      {report.skipped.length > 0 && <Skipped report={report} />}

      <MethodologyLink doc={report.protocol_doc || view.protocol_doc} />
    </section>
  )
}

function Heading({ runId }: { runId?: string }) {
  return <LearningHeading id="skillbench-heading" icon={FlaskConical} suffix={runId && <span className="text-on-surface-low text-[0.75rem]">{runId}</span>}>Skill impact benchmark</LearningHeading>
}

function Coverage({ report, registerSize }: { report: BenchmarkReport; registerSize: number }) {
  const empty = report.measured_tasks === 0
  const title = empty ? 'Nothing was measured' : `${report.measured_tasks} of ${registerSize} task${registerSize === 1 ? '' : 's'} measured`
  const detail = empty
    ? 'No task assembled both arms, so there is no delta to read and none is drawn. This is published as an unmeasured run rather than as a zero: a fabricated 0.000 here would read as "the skills you approved do nothing".'
    : `${report.absent_cells} cell${report.absent_cells === 1 ? '' : 's'} came back with an absent verifier — a timeout, spawn fault or unparseable child. Those are counted, never folded into an arm, so an infrastructure failure can never register as a skills-off win.`
  return <div className="grid gap-s rounded-lg border border-outline-variant/30 bg-surface-container p-m">
    <h3 data-type="title-s" className="text-on-surface">{title}</h3>
    {empty && <StatusPill tone="warn" className="w-fit gap-xs"><ShieldAlert size={12} /> not measured</StatusPill>}
    <p className="text-on-surface-low text-[0.8125rem]">{detail}</p>
  </div>
}

function bindingRef(binding: BenchmarkProviderBinding): string {
  return [binding.provider_name, binding.model].join(':')
}

function unrecordedCellPhrase(count: number | undefined): string {
  return typeof count === 'number' ? `${count} cell${count === 1 ? '' : 's'}` : 'One or more cells'
}

function Provenance({ report }: { report: BenchmarkReport }) {
  const binding = report.provider_binding
  const kind = binding ? 'bound' : provenanceRecorded(report) ? 'unbound' : 'unrecorded'
  const recorded = kind !== 'unrecorded'
  const title = kind === 'bound' && binding ? `Cells called ${bindingRef(binding)}` : kind === 'unbound' ? 'No model was bound — these cells called no model' : 'Provenance was not recorded'
  const homeRefs = Object.entries(report.pin?.model_fingerprint ?? {}).map(([useCase, ref]) => `${useCase}=${ref}`).join(', ')
  const cellRefs = report.pin?.cell_model_fingerprint
  const cellLabel = cellRefs === null ? UNRECORDED_LABEL : Object.entries(cellRefs ?? {}).map(([useCase, ref]) => `${useCase}=${ref}`).join(', ') || 'no model at all'
  return (
    <div className="flex flex-col gap-s rounded-xl border border-outline-variant/30 bg-surface-container p-l">
      <span data-type="title-s" className="text-on-surface">
        {title}
      </span>

      {!binding && (
        <StatusPill tone="warn" className="w-fit gap-1.5 px-m h-6">
          <ShieldAlert size={12} />
          {recorded ? 'not a model measurement' : 'provenance unrecorded'}
        </StatusPill>
      )}
      <p data-type="body-s" className="text-on-surface-low">
        {binding ? (
          <>
            Bound for use case <span className="text-on-surface-var">{binding.use_case}</span> over
            the <span className="text-on-surface-var">{binding.protocol}</span>-compatible protocol
            {binding.base_url ? <> at <code className="text-on-surface-var">{binding.base_url}</code></> : null}
            . One use case and one model ref, declared by the run — not inherited from this home.
          </>
        ) : recorded ? (
          <>
            This run declared no provider binding, so every cell resolved the offline{' '}
            <code className="text-on-surface-var">scripted</code> replay rather than a model. Both
            arms off one canned script is a fabricated comparison, so no number below is a model
            measurement. A real run names its model:{' '}
            <code className="text-on-surface-var">--bind-provider Provider:model</code>.
          </>
        ) : (
          <>
            This report was written before runs recorded which provider their cells could reach, so
            whether a model was called is UNKNOWN here — which is not the same as knowing none was.
            Re-run to record it.
          </>
        )}
      </p>
      {homeRefs.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          The pin records this <span className="text-on-surface-var">home&apos;s</span> bindings —{' '}
          {homeRefs}
          {' '}— which is what the operator configured, not what the cells reached.
        </p>
      )}

      {cellRefs !== undefined && (
        <p data-type="caption" className="text-on-surface-low">
          The pin records what the <span className="text-on-surface-var">cells</span> could reach —{' '}
          {cellLabel}
          .
        </p>
      )}

      {report.tokens_recorded === false && (
        <p data-type="caption" className="text-on-surface-low">
          {unrecordedCellPhrase(report.unrecorded_spend_cells)} reported no token usage — their
          provider omitted it — so every token ratio below is{' '}
          <span className="text-on-surface-var">{UNRECORDED_LABEL}</span> rather than zero, and no
          row is offered a direction on a spend match this run could not measure.
        </p>
      )}
    </div>
  )
}

function TaskVerdict({ row }: { row: BenchmarkTaskRow }) {
  const details = [
    row.reason,
    row.verdict !== null && !row.spend_observed ? 'spend not observed — the token ratio is not a match' : '',
    row.spend_estimated ? 'tokens estimated, not provider-reported' : '',
    ...notesFor(row),
  ].filter(Boolean)
  return <div className="flex flex-col gap-xs">
    <span className={row.verdict === null ? 'text-on-surface-low' : 'text-on-surface-var'}>{verdictLabel(row.verdict)}</span>
    {details.map((detail, index) => <p key={index} className="text-on-surface-low">{detail}</p>)}
  </div>
}

const NOTE_ALREADY_SHOWN = [
  'spend was NOT observed for every contributing cell',
  'tokens and dollars are ESTIMATED',
]

function notesFor(row: BenchmarkTaskRow): string[] {
  const details: string[] = []
  for (const note of row.notes ?? []) {
    if (NOTE_ALREADY_SHOWN.find(fragment => note.includes(fragment))) continue
    details.push(note)
  }
  return details
}

function Reproduction({ repro }: { repro: NonNullable<BenchmarkReport['reproduction']> }) {
  const verdict = repro.reproduces ? 'Reproduced within the stated variance' : 'Did NOT reproduce within the stated variance'
  const conditions = Object.entries(repro.conditions).map(([name, met]) => ({ name, met, label: met ? 'met' : 'not met' }))
  const changes = repro.verdict_changes.map(change => change.task_id)
  return <section className="flex flex-col gap-s rounded-lg border border-outline-variant/30 p-m">
    <h3 data-type="title-s" className="text-on-surface">{verdict}</h3>
    <p className="text-on-surface-low text-[0.75rem]">Baseline <span className="text-on-surface-var">{repro.baseline_run_id || '—'}</span> vs re-run <span className="text-on-surface-var">{repro.rerun_run_id || '—'}</span>. The variance is stated in <code className="text-on-surface-var">{repro.stated_variance_source}</code> — it is a set of equalities plus verdict-class agreement, not a number this page chose. A changed verdict class is a finding to publish, not a run to discard.</p>
    <ul className="flex flex-col gap-xs">{conditions.map(condition => <li key={condition.name} className="text-[0.75rem]"><span className={condition.met ? 'text-on-surface-var' : 'text-on-surface-low'}>{condition.label}</span><span className="text-on-surface-low"> · {condition.name}</span></li>)}</ul>
    {changes.length > 0 && <p className="text-on-surface-low text-[0.75rem]">{changes.length} task{changes.length === 1 ? '' : 's'} changed verdict class: {changes.join(', ')}.</p>}
  </section>
}

function Skipped({ report }: { report: BenchmarkReport }) {
  return <section className="flex flex-col gap-s">
    <h3 data-type="title-s" className="text-on-surface">Not run ({report.skipped.length})</h3>
    <ul className="grid gap-s">{report.skipped.map(({ task_id, skill, blockers }) => <li key={task_id} className="rounded-lg border border-outline-variant/30 bg-surface-container p-m">
      <header><span className="text-on-surface text-[0.8125rem]" style={fvs(600)}>{task_id}</span><span className="text-on-surface-low text-[0.75rem]"> · {skill}</span></header>
      {blockers.map((blocker, index) => <p key={index} className="text-on-surface-low text-[0.75rem]">{blocker}</p>)}
    </li>)}</ul>
  </section>
}

function MethodologyLink({ doc }: { doc: string }) {
  const destination = documentationUrl(doc)
  return destination
    ? <a href={destination} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-xs text-on-surface-var text-[0.75rem] underline">Methodology: the benchmark protocol <ExternalLink size={12} aria-hidden="true" /></a>
    : <span className="text-on-surface-var">Methodology: {doc}</span>
}

function orderedRows(view: BenchmarkView): BenchmarkTaskRow[] {
  const tasks = new Map<string, BenchmarkTaskRow>()
  for (const task of view.report.tasks) tasks.set(task.task_id, task)
  const registered = new Set<string>()
  const ordered: BenchmarkTaskRow[] = []
  for (const entry of view.register) {
    registered.add(entry.task_id)
    const row = tasks.get(entry.task_id)
    if (row) ordered.push(row)
  }
  for (const task of view.report.tasks) if (!registered.has(task.task_id)) ordered.push(task)
  return ordered
}

function verdictLabel(verdict: string | null): string {
  return verdict ?? 'not measured'
}

function fmtDelta(value: number | null): string {
  return signedMeasurement(value, 2)
}

function fmtArm(agg: BenchmarkArmAggregate | undefined): string {
  return agg ? [measurement(agg.mean_score, 2), `±${measurement(agg.spread, 2)}`].join(' ') : 'not measured'
}

function fmtRatio(value: number | null, row: BenchmarkTaskRow): string {
  return tokensUnrecorded(row) ? UNRECORDED_LABEL : measurement(value, 4)
}

function pct(fraction: number): string {
  return measuredRate(fraction)
}
