import { useEffect, useState } from 'react'
import { Gavel, ListTree, Scale } from 'lucide-react'
import { Segmented } from '../../shared/ui/Segmented'
import { FormSkeleton } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { api, type WorkflowLedgerRails } from '../../shared/data/api'
import { fmtElapsed } from './workflowMeta'


export const EM_DASH = '—'

function cell(value: number | null | undefined, fmt: (n: number) => string): string {
  return typeof value === 'number' ? fmt(value) : EM_DASH
}

function text(value: string | null | undefined): string {
  return value ? value : EM_DASH
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col rounded-lg bg-surface-high p-s">
      <dt data-type="caption" className="text-on-surface-low">{label}</dt>
      <dd className="text-on-surface tabular-nums">{value}</dd>
    </div>
  )
}

function RoiBars({ series }: { series: number[] }) {
  const peak = Math.max(...series)
  return (
    <div className="flex h-16 items-end gap-xs" role="img" aria-label={`Judge scores across ${series.length} verdicts`}>
      {series.map((score, i) => (
        <div
          key={i}
          className="min-w-[6px] flex-1 rounded-t-sm bg-primary"
          style={{ height: `${peak > 0 ? Math.max(4, (score / peak) * 100) : 4}%` }}
          title={`Verdict ${i + 1}: ${score}`}
        />
      ))}
    </div>
  )
}

export function LedgerRailsPanel({ runId }: { runId: string }) {
  const [data, setData] = useState<WorkflowLedgerRails | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'findings' | 'verdicts'>('findings')

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .workflowRunLedgerRails(runId)
      .then((body) => { if (live) setData(body) })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'could not read this run’s ledger') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  if (loading) return <FormSkeleton sections={1} rows={3} title={false} />
  if (error) return <InlineError>{error}</InlineError>
  if (!data) return <p data-type="body-s" className="text-on-surface-low">This run has no ledger to project.</p>

  const { findings, verdicts, totals, coverage } = data
  const absent = coverage.filter((c) => c.producer === 'none')

  return (
    <div className="flex flex-col gap-l">
      {
}
      <section className="flex flex-col gap-xs">
        <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
          <Scale size={13} aria-hidden /> Rail totals
        </h3>
        <dl data-type="caption" className="grid grid-cols-2 gap-xs sm:grid-cols-3">
          <Stat label="Steps completed" value={String(totals.steps_completed)} />
          <Stat label="Verdicts" value={String(totals.verdicts)} />
          <Stat label="Cost (est.)" value={cell(totals.cost_usd, (n) => `~$${n.toFixed(4)}`)} />
          <Stat label="Tokens" value={cell(totals.tokens, (n) => n.toLocaleString())} />
          <Stat label="Step time" value={cell(totals.duration_secs, fmtElapsed)} />
          <Stat
            label="Judge outcomes"
            value={Object.entries(totals.verdicts_by_word).map(([w, n]) => `${w} ${n}`).join(' · ') || EM_DASH}
          />
        </dl>
      </section>

      {
}
      {absent.length > 0 && (
        <p data-type="caption" className="text-on-surface-low">
          No run-side producer for {absent.map((c) => c.kind).join(', ')} — these rails show{' '}
          {EM_DASH} rather than zero, because nothing here writes that event.
        </p>
      )}

      <Segmented
        ariaLabel="Ledger rail"
        value={tab}
        onChange={(v) => setTab(v as 'findings' | 'verdicts')}
        options={[
          { key: 'findings', label: `Findings (${findings.length})` },
          { key: 'verdicts', label: `Verdict / ROI (${verdicts.length})` },
        ]}
      />

      {tab === 'findings' && (
        <section className="flex flex-col gap-xs">
          <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
            <ListTree size={13} aria-hidden /> Findings rail
          </h3>
          {findings.length === 0 ? (
            <p data-type="caption" className="text-on-surface-low">
              No step has completed yet — the rail fills as the run works.
            </p>
          ) : (
            <ul className="flex flex-col gap-xs">
              {findings.map((f, i) => (
                <li key={`${f.node_id ?? ''}-${f.epoch ?? i}-${i}`} className="flex flex-col gap-xs rounded-lg bg-surface-high p-s">
                  <div className="flex items-baseline justify-between gap-s">
                    <span data-type="label-s" className="text-on-surface">
                      {text(f.node_id)}
                      {f.cycle !== null && <span className="text-on-surface-low"> · cycle {f.cycle}</span>}
                    </span>
                    <span data-type="caption" className="text-on-surface-low tabular-nums">{text(f.state)}</span>
                  </div>
                  <dl data-type="caption" className="grid grid-cols-2 gap-xs text-on-surface-low sm:grid-cols-4">
                    <div><dt className="inline">Cost </dt><dd className="inline tabular-nums">{cell(f.cost_usd, (n) => `~$${n.toFixed(4)}`)}</dd></div>
                    <div><dt className="inline">Tokens </dt><dd className="inline tabular-nums">{cell(f.tokens, (n) => n.toLocaleString())}</dd></div>
                    <div><dt className="inline">Took </dt><dd className="inline tabular-nums">{cell(f.duration_secs, fmtElapsed)}</dd></div>
                    <div><dt className="inline">Retries </dt><dd className="inline tabular-nums">{cell(f.retries, String)}</dd></div>
                    <div className="col-span-2"><dt className="inline">Model </dt><dd className="inline">{text(f.model)}</dd></div>
                    <div className="col-span-2"><dt className="inline">Produced </dt><dd className="inline break-all">{text(f.output_ref)}</dd></div>
                  </dl>
                  {f.degraded_reason && (
                    <p data-type="caption" className="text-warning">Degraded: {f.degraded_reason}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {tab === 'verdicts' && (
        <section className="flex flex-col gap-xs">
          <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
            <Gavel size={13} aria-hidden /> Verdict / ROI rail
          </h3>
          {
}
          <p data-type="caption" className="text-on-surface-low">
            Plotted from the judge’s aggregated score. This run’s ledger carries no{' '}
            {totals.absent_scores.join(' or ')} — the loop rail’s axes live in a node’s output, not
            on the ledger row, so they read as {EM_DASH} here rather than as zero.
          </p>
          {totals.overall_series === null ? (
            <p data-type="caption" className="text-on-surface-low">
              {verdicts.length === 0
                ? 'No judge has run on this run yet.'
                : 'A judge ran, but no verdict carried a score to plot.'}
            </p>
          ) : (
            <RoiBars series={totals.overall_series} />
          )}
          {verdicts.length > 0 && (
            <ul className="flex flex-col gap-xs">
              {verdicts.map((v, i) => (
                <li key={`${v.node_id ?? ''}-${v.epoch ?? i}-${i}`} className="flex flex-col gap-xs rounded-lg bg-surface-high p-s">
                  <div className="flex items-baseline justify-between gap-s">
                    <span data-type="label-s" className="text-on-surface">{text(v.node_id)}</span>
                    <span data-type="caption" className="text-on-surface-low">{text(v.verdict)}</span>
                  </div>
                  <dl data-type="caption" className="grid grid-cols-2 gap-xs text-on-surface-low sm:grid-cols-4">
                    <div><dt className="inline">Score </dt><dd className="inline tabular-nums">{cell(v.overall, (n) => n.toFixed(2))}</dd></div>
                    <div><dt className="inline">Samples </dt><dd className="inline tabular-nums">{cell(v.sample_count, String)}</dd></div>
                    <div><dt className="inline">Marginal </dt><dd className="inline tabular-nums">{cell(v.marginal_value, (n) => n.toFixed(2))}</dd></div>
                    <div><dt className="inline">Quality </dt><dd className="inline tabular-nums">{cell(v.quality_score, (n) => n.toFixed(2))}</dd></div>
                  </dl>
                  {v.shortfalls && v.shortfalls.length > 0 && (
                    <p data-type="caption" className="text-on-surface-low">
                      Shortfalls: {v.shortfalls.join('; ')}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  )
}
