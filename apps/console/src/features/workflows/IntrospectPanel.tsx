import { useEffect, useMemo, useState } from 'react'
import { CircleCheck, CircleHelp, Clock, DollarSign, ScanSearch, ShieldQuestion, Split, TriangleAlert } from 'lucide-react'
import { SidePanel } from '../../shared/ui/SidePanel'
import { Segmented } from '../../shared/ui/Segmented'
import { FormSkeleton } from '../../shared/ui/ListScaffold'
import { InlineError } from '../../shared/ui/InlineError'
import { api, type WorkflowIntrospection, type WorkflowTimelineRow } from '../../shared/data/api'
import { fmtElapsed } from './workflowMeta'
import { runCostStat, runCostText, templateCostStat } from '../../shared/data/runCost'
import { UNRECORDED_LABEL, runTokensStat } from '../../shared/data/unrecorded'

export function IntrospectPanel({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [data, setData] = useState<WorkflowIntrospection | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tab, setTab] = useState<'summary' | 'timeline' | 'proof'>('summary')

  useEffect(() => {
    let live = true
    setLoading(true)
    setError(null)
    api
      .workflowRunIntrospect(runId)
      .then((body) => { if (live) setData(body) })
      .catch((e) => { if (live) setError(e instanceof Error ? e.message : 'could not read this run') })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [runId])

  const fakeChecks = useMemo(
    () => Object.values(data?.gates ?? {}).filter((g) => !!g.fake_check_warning),
    [data],
  )

  return (
    <SidePanel title="Introspection" icon={<ScanSearch size={18} />} onClose={onClose} fillHeight>
      {loading ? (
        <FormSkeleton sections={1} rows={4} title={false} />
      ) : error ? (
        <InlineError>{error}</InlineError>
      ) : !data ? (
        <p data-type="body-s" className="text-on-surface-low">This run could not be introspected.</p>
      ) : (
        <div className="flex flex-col gap-l">
          {
}
          {data.checklist_gaps.length > 0 && (
            <div data-type="caption" className="flex items-start gap-xs rounded-lg bg-surface-high p-s">
              <TriangleAlert size={14} className="text-warning shrink-0" aria-hidden />
              <div>
                <p className="text-on-surface">
                  {data.checklist_gaps.length} of 9 questions cannot be answered from this run's state:
                </p>
                <ul className="mt-2xs flex flex-col gap-xs text-on-surface-low">
                  {data.checklist_gaps.map((gap) => <li key={gap}>{gap}</li>)}
                </ul>
              </div>
            </div>
          )}

          <Segmented
            ariaLabel="Introspection view"
            value={tab}
            onChange={(v) => setTab(v as 'summary' | 'timeline' | 'proof')}
            options={[
              { key: 'summary', label: 'Summary' },
              { key: 'timeline', label: 'Timeline' },
              { key: 'proof', label: 'Proof' },
            ]}
          />

          {tab === 'summary' && (
            <>
              {
}
              <section className="flex flex-col gap-xs">
                <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
                  <DollarSign size={13} aria-hidden /> Cost and latency
                </h3>
                <dl data-type="caption" className="grid grid-cols-2 gap-xs sm:grid-cols-4">
                  {
}
                  <Stat label="Cost (est.)" value={runCostStat(data.stats.cost_usd, data.stats.priced)} />
                  <Stat label="Tokens" value={runTokensStat(data.stats.tokens, data.stats.tokens_recorded)} />
                  <Stat label="Duration" value={fmtElapsed(data.stats.duration_secs)} />
                  <Stat label="To first output" value={`${Math.round(data.stats.first_byte_ms)} ms`} />
                  <Stat label="Steps done" value={String(data.stats.steps_completed)} />
                  <Stat label="Steps failed" value={String(data.stats.steps_failed)} />
                  <Stat label="Cache hits" value={`${Math.round(data.stats.cache_hit_rate * 100)}%`} />
                  <Stat label="Models" value={data.stats.models.join(', ') || 'none recorded'} />
                </dl>
                {!data.stats.tokens_recorded && (
                  <p data-type="caption" className="text-on-surface-low">
                    One or more completed steps did not record token usage, so this run&apos;s token total is{' '}
                    <span className="text-on-surface-var">{UNRECORDED_LABEL}</span> rather than zero.
                  </p>
                )}
              </section>

              {
}
              <section className="flex flex-col gap-xs">
                <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
                  <Clock size={13} aria-hidden /> Template: {data.template_card.template || 'unnamed'}
                </h3>
                <p data-type="caption" className="text-on-surface-low">
                  Across {data.template_card.runs} run{data.template_card.runs === 1 ? '' : 's'}
                  {data.template_card.runs === 1 ? ' — p50 and p95 are that one run' : ''}
                </p>
                <dl data-type="caption" className="grid grid-cols-2 gap-xs sm:grid-cols-4">
                  {
}
                  <Stat label="Cost p50" value={templateCostStat(data.template_card.cost_p50, data.template_card.priced)} />
                  <Stat label="Cost p95" value={templateCostStat(data.template_card.cost_p95, data.template_card.priced)} />
                  <Stat label="Duration p50" value={fmtElapsed(data.template_card.duration_p50)} />
                  <Stat label="Duration p95" value={fmtElapsed(data.template_card.duration_p95)} />
                </dl>
                <p data-type="caption" className="text-on-surface-low">
                  {Math.round(data.template_card.failure_rate * 100)}% of these runs had a failed step
                </p>
              </section>

              {
}
              <section className="flex flex-col gap-xs">
                <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
                  <ShieldQuestion size={13} aria-hidden /> Gates
                </h3>
                {Object.keys(data.gates).length === 0 ? (
                  <p data-type="caption" className="text-on-surface-low">
                    This run resolved no gates, so there is nothing to judge.
                  </p>
                ) : (
                  <ul data-type="caption" className="flex flex-col gap-xs">
                    {Object.values(data.gates).map((g) => (
                      <li key={g.node_id} className="flex flex-col gap-xs rounded-lg bg-surface-high p-s">
                        <span className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono text-on-surface">{g.node_id}</span>
                          <span className="text-on-surface-low tabular-nums">
                            {g.passes} passed · {g.rejects} rejected
                            {g.retries_consumed ? ` · ${g.retries_consumed} retries` : ''}
                          </span>
                          {g.fake_check_warning ? (
                            <span className="text-warning inline-flex items-center gap-xs">
                              <TriangleAlert size={12} aria-hidden /> never said no
                            </span>
                          ) : null}
                        </span>
                        {g.fake_check_warning ? (
                          <span className="text-on-surface-low">{g.fake_check_warning}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {
}
              <section className="flex flex-col gap-xs">
                <h3 data-type="label-s" className="flex items-center gap-xs text-on-surface fw-500">
                  <Split size={13} aria-hidden /> Edges
                </h3>
                {Object.keys(data.edges.branches).length === 0 &&
                Object.keys(data.edges.judges).length === 0 ? (
                  <p data-type="caption" className="text-on-surface-low">
                    This template has no branch or judge edges, so there is no routing to distribute.
                  </p>
                ) : (
                  <ul data-type="caption" className="flex flex-col gap-xs">
                    {Object.values(data.edges.branches).map((b) => (
                      <li key={`b:${b.path}`} className="flex flex-col gap-xs rounded-lg bg-surface-high p-s">
                        <span className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono text-on-surface">{b.path}</span>
                          <span className="text-on-surface-low tabular-nums">
                            {Object.entries(b.cases)
                              .map(([label, n]) => `${label}: ${n}`)
                              .join(' · ') || 'no cases seen'}
                            {` · ${b.routed_runs} routed`}
                          </span>
                          {b.degenerate_warning || b.never_taken.length > 0 ? (
                            <span className="text-warning inline-flex items-center gap-xs">
                              <TriangleAlert size={12} aria-hidden />{' '}
                              {b.degenerate_warning ? 'does no work' : 'dead case'}
                            </span>
                          ) : null}
                        </span>
                        {b.degenerate_warning ? (
                          <span className="text-on-surface-low">{b.degenerate_warning}</span>
                        ) : null}
                        {b.never_taken.length > 0 ? (
                          <span className="text-on-surface-low">
                            Never taken: {b.never_taken.join(', ')}
                          </span>
                        ) : null}
                      </li>
                    ))}
                    {Object.values(data.edges.judges).map((j) => (
                      <li key={`j:${j.node_id}`} className="flex flex-col gap-xs rounded-lg bg-surface-high p-s">
                        <span className="flex flex-wrap items-center gap-xs">
                          <span className="font-mono text-on-surface">{j.node_id}</span>
                          <span className="text-on-surface-low tabular-nums">
                            {Object.entries(j.verdicts)
                              .map(([v, n]) => `${v || '(none)'}: ${n}`)
                              .join(' · ')}
                          </span>
                          {j.degenerate_warning ? (
                            <span className="text-warning inline-flex items-center gap-xs">
                              <TriangleAlert size={12} aria-hidden /> one verdict
                            </span>
                          ) : null}
                        </span>
                        {j.degenerate_warning ? (
                          <span className="text-on-surface-low">{j.degenerate_warning}</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {
}
              <section className="flex flex-col gap-xs">
                <h3 data-type="label-s" className="text-on-surface fw-500">The nine questions</h3>
                <dl data-type="caption" className="flex flex-col gap-xs">
                  <Answer
                    q="What is running now, and why"
                    a={`${data.answers.running.status} — ${data.answers.running.workflow || 'unnamed template'}, ${data.answers.running.nodes.length} node${data.answers.running.nodes.length === 1 ? '' : 's'} active`}
                  />
                  <Answer q="What changed" a={`${data.timeline.length} journal event${data.timeline.length === 1 ? '' : 's'} — see Timeline`} />
                  <Answer
                    q="What is blocked"
                    a={data.answers.blocked.length
                      ? `${data.answers.blocked.length} node${data.answers.blocked.length === 1 ? '' : 's'} waiting on something external`
                      : 'Nothing is blocked'}
                  />
                  <Answer
                    q="What needs my approval"
                    a={data.answers.approval.length
                      ? data.answers.approval.map((c) => c.node_id).join(', ')
                      : 'Nothing is waiting on you'}
                  />
                  <Answer
                    q="What failed"
                    a={data.answers.failed.length
                      ? `${data.answers.failed.length} node${data.answers.failed.length === 1 ? '' : 's'} failed`
                      : 'Nothing failed'}
                  />
                  <Answer q="What is costing money" a={runCostText(data.stats.cost_usd, data.stats.priced)} />
                  <Answer
                    q="What is risky"
                    a={riskyText(data.answers.risky.degraded.length, fakeChecks.length, data.stats.verification_debt)}
                  />
                  {
}
                  <Answer q="What happens next if I say nothing" a={data.answers.next.detail} />
                  <Answer
                    q="Were the checks that passed real checks"
                    a={fakeChecks.length
                      ? `${fakeChecks.length} gate${fakeChecks.length === 1 ? '' : 's'} have never rejected over a real sample`
                      : 'No gate shows the fake-check pattern'}
                  />
                </dl>
              </section>
            </>
          )}

          {
}
          {
}
          {tab === 'timeline' && data.touched.length > 0 && (
            <section className="flex flex-col gap-xs">
              <h3 data-type="label-s" className="text-on-surface fw-500">Touched</h3>
              <ul data-type="caption" className="flex flex-col gap-xs">
                {data.touched.map((t) => (
                  <li key={`${t.kind}-${t.ref}-${t.ts}`} className="flex flex-wrap items-baseline gap-xs rounded-lg bg-surface-high p-s">
                    <span className="text-on-surface-low">{t.kind === 'file' ? 'file in' : 'artifact'}</span>
                    <span className="font-mono text-on-surface">{t.label || t.ref}</span>
                    {
}
                    {t.action ? <span className="text-on-surface-low">{t.action}</span> : null}
                    {t.detail ? <span className="text-on-surface-low">{t.detail}</span> : null}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {tab === 'timeline' && (
            data.timeline.length === 0 ? (
              <p data-type="caption" className="text-on-surface-low">
                This run has written no journal events yet.
              </p>
            ) : (
              <ol data-type="caption" className="flex flex-col gap-xs">
                {data.timeline.map((row, i) => (
                  <li key={`${row.ts}-${row.kind}-${i}`} className="flex flex-col gap-xs rounded-lg bg-surface-high p-s">
                    <span className="flex flex-wrap items-baseline gap-xs">
                      <span className="text-on-surface fw-500">{row.kind}</span>
                      {row.node_id ? <span className="font-mono text-on-surface-low">{row.node_id}</span> : null}
                      {
}
                      {typeof row.attempt === 'number' && row.attempt > 1 ? (
                        <span className="text-warning">attempt {row.attempt}</span>
                      ) : null}
                      {row.ts ? <span className="text-on-surface-low tabular-nums">{row.ts}</span> : null}
                    </span>
                    <span className="flex flex-wrap gap-xs text-on-surface-low tabular-nums">
                      {row.model ? <span>{row.model}</span> : null}
                      {typeof row.tokens === 'number' && row.tokens ? <span>{row.tokens.toLocaleString()} tokens</span> : null}
                      {
}
                      {typeof row.cost_usd === 'number' && row.cost_usd ? <span>~${row.cost_usd.toFixed(4)}</span> : null}
                      {typeof row.duration_secs === 'number' ? <span>{fmtElapsed(row.duration_secs)}</span> : null}
                      {typeof row.approved === 'boolean' ? <span>{row.approved ? 'approved' : 'rejected'}</span> : null}
                    </span>
                    {row.detail ? <span className="text-on-surface-low">{String(row.detail)}</span> : null}
                  </li>
                ))}
              </ol>
            )
          )}

          {
}
          {tab === 'proof' && (
            <section className="flex flex-col gap-xs">
              <p data-type="body-s" className="text-on-surface">{data.proof.summary}</p>
              <dl data-type="caption" className="grid grid-cols-2 gap-xs">
                <Stat label="Verified steps" value={`${data.proof.verified_steps} of ${data.proof.total_steps}`} />
                <Stat label="Coverage" value={`${Math.round(data.proof.coverage * 100)}%`} />
              </dl>
              <h4 data-type="label-s" className="text-on-surface fw-500">Evidence</h4>
              {data.proof.evidence_files.length === 0 ? (
                <p data-type="caption" className="text-on-surface-low">
                  No evidence files were captured.
                </p>
              ) : (
                <ul data-type="caption" className="flex flex-col gap-xs">
                  {data.proof.evidence_files.map((f) => (
                    <li key={f} className="flex items-center gap-xs text-on-surface-low">
                      <CircleCheck size={12} className="text-success shrink-0" aria-hidden />
                      <span className="font-mono">{f}</span>
                    </li>
                  ))}
                </ul>
              )}
              {data.proof.warnings.length > 0 && (
                <>
                  <h4 data-type="label-s" className="text-on-surface fw-500">Caveats</h4>
                  <ul data-type="caption" className="flex flex-col gap-xs">
                    {data.proof.warnings.map((w) => (
                      <li key={w} className="flex items-start gap-xs text-on-surface-low">
                        <TriangleAlert size={12} className="text-warning mt-2xs shrink-0" aria-hidden />
                        <span>{w}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              {
}
              {!data.proof.honest && (
                <p data-type="caption" className="text-warning flex items-center gap-xs">
                  <CircleHelp size={12} aria-hidden />
                  This section has neither evidence nor a stated caveat, so it proves nothing.
                </p>
              )}
            </section>
          )}
        </div>
      )}
    </SidePanel>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col rounded-lg bg-surface-high p-s">
      <dt data-type="caption" className="text-on-surface-low">{label}</dt>
      <dd className="text-on-surface tabular-nums">{value}</dd>
    </div>
  )
}

function Answer({ q, a }: { q: string; a: string }) {
  return (
    <div className="flex flex-col gap-xs border-outline-variant border-b pb-xs last:border-b-0">
      <dt className="text-on-surface-low">{q}</dt>
      <dd className="text-on-surface">{a}</dd>
    </div>
  )
}

export function riskyText(degraded: number, fakeChecks: number, debt: number): string {
  const parts: string[] = []
  if (degraded) parts.push(`${degraded} node${degraded === 1 ? '' : 's'} ran degraded`)
  if (fakeChecks) parts.push(`${fakeChecks} gate${fakeChecks === 1 ? '' : 's'} may not be checking`)
  if (debt > 0) parts.push(`${Math.round(debt * 100)}% of completed steps are unverified`)
  return parts.length ? parts.join('; ') : 'Nothing flagged: no degraded nodes, no unverified steps'
}

export function rowSummary(row: WorkflowTimelineRow): string {
  return row.detail || row.state || row.kind
}
