import { LearningHeading, LearningTable, learningPanelClass, measuredRate, signedMeasurement } from './learningDisplay'
import { useRetrievalLabelDraft } from './learningActionState'
import { Search, ShieldAlert } from 'lucide-react'
import { LoadError } from '../../shared/ui/ListScaffold'
import { Button } from '../../shared/ui/Button'
import { Checkbox } from '../../shared/ui/forms'
import { InlineError } from '../../shared/ui/InlineError'
import { fvs } from '../../shared/theme/fontWeight'
import { hasApiCode, type RetrievalArmContribution, type RetrievalBenchView, type RetrievalStoreReport } from '../../shared/data/api'
import { EvalsOff } from './EvalsOff'
import { BUSY_REASON } from '../../shared/ui/unavailable'

export function RetrievalBenchPanel({ bench, error, onRetry }: {
  bench: RetrievalBenchView | undefined
  error: unknown
  onRetry: () => void
}) {

  if (bench === undefined) {
    if (!error) return null
    if (hasApiCode(error, 'evals_disabled')) {
      return (
        <section className={learningPanelClass} aria-labelledby="retrieval-bench-heading">
          <Heading />
          <EvalsOff what="retrieval benchmark" />
        </section>
      )
    }
    if (hasApiCode(error, 'retrieval_absent')) {
      return (
        <section className={learningPanelClass} aria-labelledby="retrieval-bench-heading">
          <Heading />
          <p className="text-on-surface-low text-[0.8125rem]">
            No retrieval benchmark has run yet. Run{' '}
            <code className="text-on-surface-var">gideon retrieval-eval</code> to score
            both stores. It reads knowledge.db and memory.db and writes to neither — a run
            that touched either one refuses to report.
          </p>
          <LabelCards stores={['knowledge', 'memory']} />
        </section>
      )
    }
    return <LoadError what="retrieval benchmark" error={error} onRetry={onRetry} />
  }
  if (!bench) return null

  const stores = Object.keys(bench.stores)

  return (
    <section className={learningPanelClass} aria-labelledby="retrieval-bench-heading">
      <Heading />

      <p className="text-on-surface-low text-[0.75rem]">
        An arm is worth enabling when dropping it costs at least{' '}
        {fmt(bench.floors.min_arm_contribution)} P@{bench.k}, measured over at least{' '}
        {bench.floors.min_scored_queries} scored queries. Below that population the verdict is{' '}
        <em>unmeasured</em> rather than a number — a delta computed from four queries is a
        number, not evidence. The <code className="text-on-surface-var">{bench.control_mask}</code>{' '}
        row is the control: every arm off, so it must retrieve nothing. If it ever reports a
        score, the mask stopped reaching the retriever and every delta above it is noise.
      </p>

      {stores.map((store) => (
        <StoreReport key={store} store={store} report={bench.stores[store]} k={bench.k} />
      ))}

      <LabelCards stores={stores} />
    </section>
  )
}

function Heading() {
  return <LearningHeading id="retrieval-bench-heading" icon={Search} suffix={null}>Retrieval arms</LearningHeading>
}

function StoreReport({ store, report, k }: { store: string; report: RetrievalStoreReport; k: number }) {

  if (!report.run || !report.table) {
    return (
      <div className="flex flex-col gap-xs">
        <span data-type="title-s" className="text-on-surface">{store}</span>
        <p className="text-on-surface-low text-[0.8125rem]">
          Not measured yet. <code className="text-on-surface-var">gideon retrieval-eval
          --store {store}</code>
        </p>
      </div>
    )
  }
  const dead = Object.entries(report.table.arm_executors ?? {}).flatMap(([arm, live]) => live ? [] : [arm])
  return (
    <div className="flex flex-col gap-xs">
      <div className="flex flex-wrap items-baseline gap-s">
        <span data-type="title-s" className="text-on-surface">{store}</span>
        <span className="text-on-surface-low text-[0.75rem]">{report.run}</span>
      </div>

      {report.table.corpus_drifted && (
        <Warn>
          The corpus changed since these labels were made, so R@{k}'s denominator is no longer
          the one that was judged. Re-label, or read the recall column as indicative only.
        </Warn>
      )}
      {dead.length > 0 && (
        <Warn>
          No executor for {dead.join(', ')} — that arm never ran, so its zero delta says nothing
          about the arm. Bind an embedding model on{' '}
          <a className="underline" href="#/settings/models">Settings → Models</a> to measure the
          vector arm.
        </Warn>
      )}

      <Provenance sources={report.table.qrels_sources} />

      <LearningTable caption={`Precision and recall at ${k} for the ${store} store, per arm mask`} rows={report.table.rows} rowKey={row => row.mask} columns={[
        { name: 'Arms', render: row => row.mask },
        { name: `P@${k}`, align: 'right', render: row => fmtRate(row.p_at_k) },
        { name: `R@${k}`, align: 'right', render: row => fmtRate(row.r_at_k) },
        { name: 'Scored', align: 'right', render: row => `${row.scored_queries} of ${row.queries}` },
        { name: 'No candidates', align: 'right', render: row => row.no_candidate_queries },
      ]} />

      {report.contributions && report.contributions.length > 0 && (
        <LearningTable caption={`Per-arm marginal contribution for the ${store} store`} rows={report.contributions} rowKey={row => row.arm} columns={[
          { name: 'Arm', render: row => row.arm },
          { name: `ΔP@${k}`, align: 'right', render: row => fmtDelta(row.contribution_p) },
          { name: `ΔR@${k}`, align: 'right', render: row => fmtDelta(row.contribution_r) },
          { name: 'Alone', align: 'right', render: row => fmtRate(row.solo_p_at_k) },
          { name: 'Verdict', render: row => <ContributionVerdict contribution={row} /> },
        ]} />
      )}
    </div>
  )
}

function Provenance({ sources }: { sources?: Record<string, number> }) {
  if (sources === undefined) return <p className="text-on-surface-low text-[0.75rem]">Ground truth: not stated by this run — re-run to record which labels it scored.</p>
  const lines = Object.entries(sources).flatMap(([source, count]) => count > 0 ? [{ source, count }] : [])
  if (!lines.length) return null
  return <p className="text-on-surface-low text-[0.75rem]">Ground truth: {lines.map(({ source, count }, index) => <span key={source}>{index ? ' · ' : ''}{count} <code className="text-on-surface-var">{source || 'unlabelled'}</code></span>)}</p>
}

function ContributionVerdict({ contribution }: { contribution: RetrievalArmContribution }) {
  return <div className="flex flex-col gap-xs">
    <span className={contribution.verdict === 'enable' ? 'text-on-surface' : 'inline-flex items-center gap-xs text-warn'} style={contribution.verdict === 'enable' ? fvs(600) : undefined}>
      {contribution.verdict !== 'enable' && <ShieldAlert size={12} />}{contribution.verdict}
    </span>
    <ul className="flex flex-col gap-0.5 text-on-surface-low">{contribution.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
  </div>
}

function LabelCards({ stores }: { stores: string[] }) {
  const { store, card, picked, busy, err, saved, open, save, toggle, clearError, cancel } = useRetrievalLabelDraft()

  return (
    <div className="flex flex-col gap-m rounded-xl border border-outline-variant/30 bg-surface-container p-l">
      <span data-type="title-s" className="text-on-surface">Label a few queries</span>
      <p className="text-on-surface-low text-[0.8125rem]">
        The benchmark's ground truth is mined from what you actually used. Ten minutes of
        hand-labelling on the head queries is what makes the numbers above worth reading.
      </p>
      <div className="flex flex-wrap items-center gap-s">
        {stores.map((s) => (
          <Button key={s} variant="ghost" disabled={busy} disabledReason={BUSY_REASON} onClick={() => open(s)}>
            Label {s}
          </Button>
        ))}
      </div>
      {err && <InlineError icon onDismiss={clearError}>{err}</InlineError>}
      {saved && <p className="text-on-surface-low text-[0.8125rem]">{saved}</p>}
      {card && (
        <div className="flex flex-col gap-m">
          <p className="text-on-surface-low text-[0.75rem]">
            {card.store}: {card.labelled} hand-labelled, {card.mined} mined. Tick every result
            that answers the query. Ticking none is a real answer — save it.
          </p>
          {card.queries.map((entry) => (
            <fieldset key={entry.query} className="flex flex-col gap-xs">
              <legend className="text-on-surface text-[0.8125rem]" style={fvs(600)}>
                {entry.query}
              </legend>
              {entry.candidates.length === 0 ? (
                <p className="text-on-surface-low text-[0.75rem]">
                  The retriever returned nothing for this query — there is nothing to mark, and
                  that itself is the measurement.
                </p>
              ) : entry.candidates.map((id) => (
                <label key={id} className="flex items-center gap-s text-on-surface-var text-[0.75rem]">
                  <Checkbox
                    checked={(picked[entry.query] ?? []).includes(id)}
                    onChange={() => toggle(entry.query, id)}
                    ariaLabel={id}
                  />
                  <span className="break-all">{id}</span>
                </label>
              ))}
            </fieldset>
          ))}
          <div className="flex flex-wrap items-center gap-s">
            <Button disabled={busy} disabledReason={BUSY_REASON} onClick={save}>Save labels</Button>
            <Button variant="ghost" disabled={busy} disabledReason={BUSY_REASON} onClick={cancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      {busy && !card && <p className="text-on-surface-low text-[0.75rem]">Reading {store}…</p>}
    </div>
  )
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <p
      className="flex items-start gap-1.5 rounded-lg px-m py-s text-[0.75rem]"
      style={{
        background: 'color-mix(in srgb, var(--color-warn) 10%, transparent)',
        color: 'var(--color-warn)',
      }}
    >
      <ShieldAlert size={12} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  )
}

function fmt(value: number | undefined): string {
  return value?.toString() ?? '—'
}

function fmtRate(value: number | null): string {
  return measuredRate(value, 1)
}

function fmtDelta(value: number | null): string {
  return signedMeasurement(value, 1, 100, 'pp', 'no delta')
}
