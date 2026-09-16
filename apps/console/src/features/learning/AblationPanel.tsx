import { LearningHeading, LearningTable, learningPanelClass, measurement, signedMeasurement } from './learningDisplay'
import { Scissors, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { fvs } from '../../shared/theme/fontWeight'
import { hasApiCode } from '../../shared/data/api'
import type {
  AblationHistoryEntry, AblationRegistryRow, AblationView,
} from '../../shared/data/api'
import { EvalsOff } from './EvalsOff'

export function AblationPanel({ view, error, onRetry }: {
  view: AblationView | undefined
  error: unknown
  onRetry: () => void
}) {

  if (view === undefined) {
    if (!error) return null
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className={learningPanelClass} aria-labelledby="ablation-heading">
          <Heading />
          <EvalsOff what="ablation" />
        </section>
      )
    }
    if (hasApiCode(error, 'ablation_absent')) {
      return (
        <section className={learningPanelClass} aria-labelledby="ablation-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No ablation has run yet. Register a component in{' '}
            <code className="text-on-surface-var">evals/ablation_registry.json</code> and run{' '}
            <code className="text-on-surface-var">gideon ablation --force</code>. It is a
            deliberate command on a monthly cadence, not a background job — one report is a
            multi-cell matrix, so nothing here spends money on its own.
          </p>
        </section>
      )
    }
    return <LoadError what="ablation report" error={error} onRetry={onRetry} />
  }
  if (!view) return null

  const report = view.report
  const armKeys = orderArms(Object.keys(report.arms))

  return (
    <section className={learningPanelClass} aria-labelledby="ablation-heading">
      <Heading matrixId={report.matrix_id} />

      <VerdictCard view={view} />

      <p className="text-on-surface-low text-[0.75rem]">
        A gain below ε = {report.epsilon} is no gain. The comparison is SIGNED: a component whose
        absence <em>improved</em> the benchmark is not a keep with a negative delta, it is a
        component that does not pay for itself. Measured over {report.trials}{' '}
        trial{report.trials === 1 ? '' : 's'} per arm
        {report.created_at ? <> · {report.created_at}</> : null}.
      </p>

      <div className="flex flex-col gap-xs">
        <span data-type="title-s" className="text-on-surface">
          {report.component_id || 'component'} · {report.kind} · {report.target}
        </span>
        <LearningTable caption={`Per-arm results for the ${report.component_id || 'ablated'} component, replayed over the ${report.subject || 'registered'} scenario`} rows={armKeys.map(arm => ({ arm, aggregate: report.arms[arm] }))} rowKey={row => row.arm || 'unattributed'} columns={[
          { name: 'Arm', render: row => row.arm || 'unattributed' },
          { name: 'Cells', align: 'right', render: row => row.aggregate.total },
          { name: 'Scored', align: 'right', render: row => row.aggregate.scored_count },
          { name: 'Mean score', align: 'right', render: row => fmtMean(row.aggregate.mean_score) },
          { name: 'Verifier absent', align: 'right', render: row => row.aggregate.counts.verifier_absent ?? 0 },
        ]} />
        <p className="text-on-surface-low text-[0.75rem]">
          on − off: <span className="text-on-surface-var">{fmtDelta(report.delta)}</span>
          {report.cheap_delta !== null && (
            <> · on − cheap: <span className="text-on-surface-var">{fmtDelta(report.cheap_delta)}</span></>
          )}
          {Object.keys(report.live_state).length > 0 && (
            <> · {Object.keys(report.live_state).length} live file
              {Object.keys(report.live_state).length === 1 ? '' : 's'} watched and unchanged</>
          )}
        </p>
      </div>

      <Cadence view={view} />

      {view.history.length > 0 && <History history={view.history} />}

      {view.registry.length > 0 && <Registry rows={view.registry} />}
    </section>
  )
}

function Heading({ matrixId }: { matrixId?: string }) {
  return <LearningHeading id="ablation-heading" icon={Scissors} suffix={matrixId && <span className="text-on-surface-low text-[0.75rem]">{matrixId}</span>}>Component ablation</LearningHeading>
}

function VerdictCard({ view }: { view: AblationView }) {
  const { verdict, target, component_id: componentId } = view.report
  const subject = target || componentId || 'this component'
  const decided = new Set(view.verdict_vocabulary).has(verdict)
  const verb = new Map([['keep', 'Keep'], ['remove', 'Retire'], ['lighten', 'Lighten']]).get(verdict)
  const title = [verb ? `${verb} ${subject}` : '', !decided ? `No verdict for ${subject}` : ''].join('')
  return (
    <div className="flex flex-col gap-s rounded-xl border border-outline-variant/30 bg-surface-container p-l">
      <span data-type="title-s" className="text-on-surface">
        {title}
      </span>
      {!decided && (
        <span
          className="inline-flex w-fit items-center gap-1.5 rounded-pill px-m h-6 text-[0.75rem]"
          style={{
            background: 'color-mix(in srgb, var(--color-warn) 14%, transparent)',
            color: 'var(--color-warn)',
          }}
        >
          <ShieldAlert size={12} /> inconclusive
        </span>
      )}
      <p className="text-on-surface-low text-[0.8125rem]">
        {verdict === 'keep' && (
          <>Switching it off measurably degraded the benchmark, by more than ε. It earns its keep;
            there is nothing to do.</>
        )}
        {verdict === 'remove' && (
          <>Switching it off changed nothing measurable. A retirement proposal is waiting in your{' '}
            <a className="underline" href="#/inbox">Inbox</a> — the harness proposes, you decide.</>
        )}
        {verdict === 'lighten' && (
          <>It pays for itself, but a deliberately cheaper variant matched the full one. Swapping
            in the cheap form keeps the gain and drops the cost.</>
        )}
        {!decided && (
          <>An arm produced no scored cell, so there is no delta to read. This is deliberately not
            a retirement recommendation: an absent verifier is never a zero, and treating it as
            one would retire a component on a measurement that never happened.</>
        )}
      </p>
      <span className="text-on-surface-low text-[0.75rem]" style={fvs(600)}>
        {view.report.subject ? <>replayed over {view.report.subject}</> : null}
      </span>
    </div>
  )
}

function Cadence({ view }: { view: AblationView }) {
  const schedule = `Every ${view.cadence_days} day${view.cadence_days === 1 ? '' : 's'}, one component in turn.`
  const last = view.last_run_ts ? `Last run ${view.last_run_ts}.` : 'Never run.'
  return <p className="text-on-surface-low text-[0.75rem]">{schedule} {last} {view.due ? <span className="text-on-surface-var" style={fvs(600)}>Due now — run <code>gideon ablation</code>.</span> : 'Not due yet.'}</p>
}

function History({ history }: { history: AblationHistoryEntry[] }) {
  const rows = history.map((_, index) => history[history.length - index - 1])
  return (
    <div className="flex flex-col gap-xs">
      <span data-type="title-s" className="text-on-surface">Past cadences</span>
      <LearningTable caption={`The last ${rows.length} ablation cadence run${rows.length === 1 ? '' : 's'}, newest first`} rows={rows} rowKey={entry => `${entry.ts}-${entry.component_id}`} columns={[
        { name: 'When', render: entry => entry.ts },
        { name: 'Component', render: entry => entry.component_id },
        { name: 'Verdict', render: entry => entry.verdict },
        { name: 'Delta', align: 'right', render: entry => fmtDelta(entry.delta) },
        { name: 'Proposal', render: entry => proposalLabel(entry) },
      ]} />
    </div>
  )
}

function Registry({ rows }: { rows: AblationRegistryRow[] }) {
  return <section className="flex flex-col gap-s">
    <h3 data-type="title-s" className="text-on-surface">Registered components ({rows.length})</h3>
    <ul className="grid gap-s">{rows.map(row => <li key={row.component_id} className="rounded-lg border border-outline-variant/30 bg-surface-container p-m">
      <header><span className="text-on-surface text-[0.8125rem]" style={fvs(600)}>{row.component_id}</span><span className="text-on-surface-low text-[0.75rem]"> · {row.kind} · {row.target}</span></header>
      {row.description && <p className="text-on-surface-low text-[0.75rem]">{row.description}</p>}
      {row.cheap_value === null && <p className="text-on-surface-low text-[0.75rem]">No cheap form declared, so this component can never earn a lighten verdict.</p>}
    </li>)}</ul>
  </section>
}

function orderArms(keys: string[]): string[] {
  const ranks = new Map([['on', 0], ['off', 1], ['cheap', 2]])
  return keys.slice().sort((left, right) => (ranks.get(left) ?? 3) - (ranks.get(right) ?? 3))
}

function fmtMean(value: number | null): string {
  return measurement(value, 3)
}

function fmtDelta(value: number | null): string {
  return signedMeasurement(value, 3)
}

function proposalLabel(entry: AblationHistoryEntry): string {
  const prefix = 'not_filed:'
  const value = entry.proposal
  if (!value) return entry.verdict === 'remove' ? 'not filed' : '—'
  return value.indexOf(prefix) === 0 ? `not filed (${value.substring(prefix.length)})` : value
}
