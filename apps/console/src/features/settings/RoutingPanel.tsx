import { ArrowDown, ArrowUp, Trophy } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api, type RoutingPolicyRow, type RoutingProposal, type TelemetryRow } from '../../shared/data/api'
import { useQuery } from '../../shared/data/data'
import { useQueryParam, type RouteProps } from '../../app/shell/useQueryState'
import { Button } from '../../shared/ui/Button'
import { StatusPill } from '../../shared/ui/StatusPill'
import { Segmented } from '../../shared/ui/Segmented'
import { Field, FieldError, Select } from '../../shared/ui/forms'
import { unavailableWhen } from '../../shared/ui/unavailable'
import { PanelHeader, Section } from './settingsUI'


const USE_CASES = [
  { key: 'chat', label: 'Chat' },
  { key: 'code_tools', label: 'Code & tools' },
  { key: 'reasoning', label: 'Reasoning' },
] as const

const MEASURED_USE_CASES = ['reasoning', 'background', 'loops', 'orchestration'] as const

const QUERY_CLASSES = [
  { value: 'short_chat', label: 'Short chat' },
  { value: 'code', label: 'Code' },
  { value: 'summarize', label: 'Summarize' },
  { value: 'extract_structured', label: 'Extract structured' },
  { value: 'long_reasoning', label: 'Long reasoning' },
] as const

const DEFAULT_USE_CASE = USE_CASES[0].key
const DEFAULT_QUERY_CLASS = QUERY_CLASSES[0].value

export function fmtPct(fraction: number): string {
  return `${Math.round(fraction * 100)}%`
}

export function fmtFeedback(fraction: number): string {
  return fraction > 0 ? fmtPct(fraction) : '—'
}

export function fmtMs(ms: number): string {
  return ms > 0 ? Math.round(ms).toLocaleString() : '—'
}

export function fmtCost(usd: number): string {
  if (usd <= 0) return 'free'
  return usd >= 1 ? `$${usd.toFixed(2)}` : `$${usd.toFixed(4)}`
}

export function sortByFrontier(rows: TelemetryRow[]): TelemetryRow[] {
  return [...rows].sort((a, b) => Number(b.on_frontier) - Number(a.on_frontier))
}

export function RoutingPanel({ query, setQuery }: Pick<RouteProps, 'query' | 'setQuery'>) {
  const [useCase, setUseCase] = useQueryParam(query, setQuery, 'uc', DEFAULT_USE_CASE, { replace: true })
  const [queryClass, setQueryClass] = useQueryParam(query, setQuery, 'qc', DEFAULT_QUERY_CLASS, { replace: true })

  const { data } = useQuery(
    `settings:routing-telemetry:${useCase}:${queryClass}`,
    () => api.modelsTelemetry({ use_case: useCase, query_class: queryClass })
      .then((d) => ({ rows: d.rows }))
      .catch(() => null),
    { persist: false },
  )

  const rows = data ? sortByFrontier(data.rows) : []
  const frontierCount = rows.filter((r) => r.on_frontier).length

  return (
    <div className="flex flex-col" style={{ minHeight: 0 }}>
      <PanelHeader title="Routing & Efficiency"
        hint="Real per-model efficiency for each kind of request — success rate, feedback, latency, and cost per call, measured as models handle work. A model is on the frontier when no other model beats it on all of quality, speed, and cost. Routing policy, below, turns that observation into a decision: which of your bound models this use case tries first." />

      <div className="mb-l flex flex-wrap items-end gap-l">
        <Field label="Use case">
          <Segmented
            ariaLabel="Routing use case"
            options={USE_CASES.map((u) => ({ key: u.key, label: u.label }))}
            value={useCase}
            onChange={setUseCase}
          />
        </Field>
        <div className="min-w-[13rem]">
          <Field label="Request kind">
            <Select
              value={queryClass}
              onChange={setQueryClass}
              options={QUERY_CLASSES.map((q) => ({ value: q.value, label: q.label }))}
            />
          </Field>
        </div>
      </div>

      {
}
      <Section title="Model efficiency">
        {data === null ? (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var" role="status">
            Couldn't read routing telemetry right now. It's a read-only view — try switching the bucket or reloading.
          </div>
        ) : data === undefined ? (
          <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low">Loading…</div>
        ) : rows.length === 0 ? (
          <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
            {(MEASURED_USE_CASES as readonly string[]).includes(useCase)
              ? 'No routing telemetry recorded for this yet — it fills in as models handle this kind of request.'
              : 'Nothing is measured for this axis. Routing telemetry comes from unattended work — reasoning, background, loops and orchestration — because interactive requests deliberately stay outside the model-call guard.'}
          </div>
        ) : (
          <>
            <TelemetryTable rows={rows} />
            <p data-type="caption" className="mt-m text-on-surface-low">
              <Trophy size={11} className="mr-1 inline text-ok" aria-hidden />
              {frontierCount} of {rows.length} {rows.length === 1 ? 'model is' : 'models are'} on the frontier
              — not beaten by another on all of quality, speed, and cost.
            </p>
          </>
        )}
      </Section>

      <RoutingProposalsSection />

      <RoutingPolicySection useCase={useCase} queryClass={queryClass} />
    </div>
  )
}

function RoutingProposalsSection() {
  const [props_, setProps] = useState<RoutingProposal[] | null | undefined>(undefined)
  const [busy, setBusy] = useState('')
  const [note, setNote] = useState('')
  const [said, setSaid] = useState('')

  const load = useCallback(() => {
    api.routingProposals()
      .then((d) => setProps(d.proposals))
      .catch(() => setProps(null))
  }, [])
  useEffect(load, [load])

  const decide = async (p: RoutingProposal, accept: boolean) => {
    setBusy(p.id)
    setNote('')
    try {
      if (accept) {
        const r = await api.acceptRoutingProposal(p.id)
        setSaid(r.applied
          ? `Applied: ${p.use_case} / ${p.query_class} now tries ${p.proposed[0]} first.`
          : `Not applied — ${r.reason ?? 'this order was set by hand.'}`)
      } else {
        await api.rejectRoutingProposal(p.id)
        setSaid(`Dismissed. This suggestion won't come back for a while.`)
      }
      load()
    } catch {
      setNote("Couldn't record that — nothing changed.")
    } finally {
      setBusy('')
    }
  }

  return (
    <Section title="Proposed routing changes">
      {props_ === null ? (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var" role="status">
          Couldn't read the proposal queue right now. Nothing is pending action — your routing
          table is unchanged either way.
        </div>
      ) : props_ === undefined ? (
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-low">Loading…</div>
      ) : props_.length === 0 ? (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
          Nothing proposed. When measurements show one of your models clearly beating another for a
          request kind, the change is proposed here — routing never rewrites your table on its own.
        </div>
      ) : (
        <>
        {
}
        <p data-type="body-s" className="mb-m text-on-surface-var">
          {props_.length} proposed {props_.length === 1 ? 'change' : 'changes'} waiting on you.
          Routing measured these — it has not applied them.
        </p>
        <ul className="flex flex-col gap-2">
          {props_.map((p) => (
            <li key={p.id} className="rounded-lg bg-surface-container px-3 py-2.5">
              <p data-type="body-s" className="text-on-surface">
                For <span className="text-on-surface-var">{p.use_case} / {p.query_class}</span>, try{' '}
                <span className="font-mono">{p.proposed[0]}</span> before{' '}
                <span className="font-mono">{p.current[0]}</span>.
              </p>
              <ProposalEvidence evidence={p.evidence} promoted={p.proposed[0]} demoted={p.current[0]} />
              <div className="mt-s flex items-center gap-s">
                <Button size="xs" variant="primary" loading={busy === p.id}
                  onClick={() => void decide(p, true)}
                  ariaLabel={`Apply: try ${p.proposed[0]} first for ${p.use_case} ${p.query_class}`}>
                  Apply
                </Button>
                <Button size="xs" variant="ghost" loading={busy === p.id}
                  onClick={() => void decide(p, false)}
                  ariaLabel={`Dismiss the proposal for ${p.use_case} ${p.query_class}`}>
                  Dismiss
                </Button>
              </div>
            </li>
          ))}
        </ul>
        </>
      )}
      {
}
      <p role="status" aria-live="polite" data-type={said ? 'body-s' : undefined}
        className={said ? 'mt-m text-on-surface-var' : ''}>{said}</p>
      {note && <FieldError className="mt-s">{note}</FieldError>}
    </Section>
  )
}

function ProposalEvidence({ evidence, promoted, demoted }: {
  evidence: RoutingProposal['evidence']
  promoted: string
  demoted: string
}) {
  const scores = evidence.scores ?? {}
  const counts = evidence.n ?? {}
  const p50 = evidence.p50_delta_ms
  const cost = evidence.cost_delta_usd
  const bits: string[] = []
  if (scores[promoted] !== undefined && scores[demoted] !== undefined) {
    bits.push(`scored ${fmtPct(scores[promoted])} vs ${fmtPct(scores[demoted])}`)
  }
  if (counts[promoted] !== undefined && counts[demoted] !== undefined) {
    bits.push(`over ${counts[promoted]} and ${counts[demoted]} calls`)
  }
  if (p50 !== undefined && p50 !== 0) {
    bits.push(`${fmtMs(Math.abs(p50))}ms ${p50 < 0 ? 'faster' : 'slower'}`)
  }
  if (cost !== undefined && cost !== 0) {
    bits.push(`${fmtCost(Math.abs(cost))} ${cost < 0 ? 'cheaper' : 'dearer'} per call`)
  }
  if (bits.length === 0) return null
  return <p data-type="caption" className="mt-1 text-on-surface-low">{bits.join(' · ')}.</p>
}

function RoutingPolicySection({ useCase, queryClass }: { useCase: string; queryClass: string }) {
  const [rows, setRows] = useState<RoutingPolicyRow[] | null | undefined>(undefined)
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [moved, setMoved] = useState('')

  const load = useCallback(() => {
    api.routingPolicy()
      .then((d) => { setRows(d.use_cases); setEnabled(d.enabled) })
      .catch(() => setRows(null))
  }, [])
  useEffect(load, [load])

  const row = rows?.find((r) => r.use_case === useCase)

  const save = async (body: Parameters<typeof api.setRoutingPolicy>[0]): Promise<boolean> => {
    setBusy(true)
    setNote('')
    try {
      await api.setRoutingPolicy(body)
      load()
      return true
    } catch {
      setNote("Couldn't save that — nothing changed.")
      return false
    } finally {
      setBusy(false)
    }
  }

  if (rows === null) {
    return (
      <Section title="Routing policy">
        <div data-type="body-s" className="rounded-lg bg-surface-container px-3 py-2.5 text-on-surface-var" role="status">
          Couldn't read the routing policy right now. Your bound models are unaffected — resolution
          falls back to the order you bound them in.
        </div>
      </Section>
    )
  }

  const recorded = row?.classes?.[queryClass]
  const order = recorded?.order ?? []
  const candidates = row?.candidates ?? []
  const shown = [
    ...order.filter((ref) => candidates.some((c) => c.ref === ref)),
    ...candidates.map((c) => c.ref).filter((ref) => !order.includes(ref)),
  ]

  const move = (index: number, delta: number) => {
    const next = [...shown]
    const target = index + delta
    if (target < 0 || target >= next.length) return
    ;[next[index], next[target]] = [next[target], next[index]]
    void save({ use_case: useCase, query_class: queryClass, order: next })
      .then((ok) => { if (ok) setMoved(`${next[target]} moved to position ${target + 1} of ${next.length}`) })
  }

  return (
    <Section title="Routing policy">
      <p data-type="body-s" className="mb-m text-on-surface-var">
        Which of your bound models this use case tries first. Routing only reorders the models you
        already bound — it never adds or removes one, and an unavailable model still reports an
        error rather than being quietly swapped.
        {!enabled && ' Routing is currently off globally, so this order is not applied yet.'}
      </p>

      {!row ? (
        <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
          Routing doesn't apply to this use case — it runs on background work (reasoning, loops,
          orchestration), not on interactive chat.
        </div>
      ) : (
        <>
          <div className="mb-l flex flex-wrap items-end gap-l">
            <div className="min-w-[13rem]">
              <Field label="Mode" hint="How the first model gets chosen.">
                <Select
                  value={row.mode}
                  disabled={busy}
                  onChange={(v) => void save({ use_case: useCase, mode: v as RoutingPolicyRow['mode'] })}
                  options={[
                    { value: 'off', label: 'Off — use my order' },
                    { value: 'heuristic', label: 'Prefer local' },
                    { value: 'learned', label: 'Learn from results' },
                  ]}
                />
              </Field>
            </div>
            <div className="min-w-[15rem]">
              <Field label="Pin" hint="Overrules the mode for this use case.">
                <Select
                  value={row.pin}
                  disabled={busy}
                  onChange={(v) => void save({ use_case: useCase, pin: v })}
                  options={[
                    { value: '', label: 'No pin' },
                    { value: 'local', label: 'Always local' },
                    { value: 'cloud', label: 'Always cloud' },
                    ...candidates.map((c) => ({ value: c.ref, label: `Always ${c.ref}` })),
                  ]}
                />
              </Field>
            </div>
          </div>

          {shown.length === 0 ? (
            <div data-type="body-s" className="rounded-lg border border-dashed border-outline-variant/50 bg-surface-container px-4 py-5 text-center text-on-surface-low">
              No models bound to this use case yet. Bind two — one local, one cloud — to give routing
              a choice to make.
            </div>
          ) : (
            <ol className="flex flex-col gap-1.5">
              {shown.map((ref, i) => {
                const local = candidates.find((c) => c.ref === ref)?.local
                return (
                  <li key={ref} data-type="body-s" className="flex items-center gap-2 rounded-lg bg-surface-container px-3 py-2">
                    <span className="w-5 text-right tabular-nums text-on-surface-low">{i + 1}</span>
                    <span className="flex-1 truncate font-mono text-on-surface" title={ref}>{ref}</span>
                    <span data-type="caption" className="text-on-surface-low">{local ? 'local' : 'cloud'}</span>
                    {
}
                    <button type="button"
                      {...unavailableWhen(i === 0, 'Already tried first', { busy })}
                      onClick={() => move(i, -1)}
                      className="grid size-7 place-items-center rounded-md text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
                      aria-label={`Move ${ref} earlier`}>
                      <ArrowUp size={13} aria-hidden />
                    </button>
                    <button type="button"
                      {...unavailableWhen(i === shown.length - 1, 'Already tried last', { busy })}
                      onClick={() => move(i, 1)}
                      className="grid size-7 place-items-center rounded-md text-on-surface-var hover:bg-surface-high aria-disabled:opacity-40 disabled:opacity-40"
                      aria-label={`Move ${ref} later`}>
                      <ArrowDown size={13} aria-hidden />
                    </button>
                  </li>
                )
              })}
            </ol>
          )}

          <p data-type="caption" className="mt-m text-on-surface-low">
            {row.pin
              ? `Pinned to ${row.pin} — the order below is recorded but not applied while the pin is set.`
              : recorded
                ? `Order recorded for ${queryClass} · decided by ${String(recorded.basis?.source ?? 'unknown')}.`
                : `No order recorded for ${queryClass} yet — ${row.mode === 'off' ? 'your bound order applies' : 'the prefer-local rule applies'}.`}
          </p>
          {
}
          <p role="status" aria-live="polite" className="sr-only">{moved}</p>
          {
}
          {note && <FieldError className="mt-s">{note}</FieldError>}
        </>
      )}
    </Section>
  )
}

export function TelemetryTable({ rows }: { rows: TelemetryRow[] }) {
  const th = 'border-b border-outline-variant/40 px-2 py-1.5 font-normal'
  const td = 'border-b border-outline-variant/25 px-2 py-1.5'
  return (
    <div data-table-surface role="region" aria-label="Routing telemetry" tabIndex={0} className="w-full min-w-0 max-w-full overflow-x-auto overscroll-x-contain">
      <table data-type="body-s" className="w-max min-w-full border-collapse">
        <caption className="sr-only">Routing telemetry</caption>
        <thead>
          <tr className="text-on-surface-low">
            <th scope="col" className={`${th} text-left`}>Model</th>
            <th scope="col" className={`${th} text-right`}>Calls</th>
            <th scope="col" className={`${th} text-right`}>Success</th>
            <th scope="col" className={`${th} text-right`}>Feedback</th>
            <th scope="col" className={`${th} text-right`}>p50 ms</th>
            <th scope="col" className={`${th} text-right`}>p95 ms</th>
            <th scope="col" className={`${th} text-right`}>Cost/call</th>
            <th scope="col" className={`${th} text-right`}>Frontier</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.ref} className="text-on-surface-var">
              <td className={`${td} font-mono text-on-surface max-w-64 break-all`}>{r.ref}</td>
              <td className={`${td} text-right tabular-nums`}>{r.n.toLocaleString()}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtPct(r.success)}</td>
              <td className={`${td} text-right tabular-nums text-on-surface-low`}>{fmtFeedback(r.feedback)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtMs(r.p50_ms)}</td>
              <td className={`${td} text-right tabular-nums text-on-surface-low`}>{fmtMs(r.p95_ms)}</td>
              <td className={`${td} text-right tabular-nums`}>{fmtCost(r.avg_cost_usd)}</td>
              <td className={`${td} text-right`}>
                {r.on_frontier ? (
                  <StatusPill tone="ok" sized={false} data-type="caption" className="gap-1 py-0.5"
                    title="On the Pareto frontier — no other model beats this one on all of quality, speed, and cost.">
                    <Trophy size={9} aria-hidden /> frontier
                  </StatusPill>
                ) : (
                  <span className="text-on-surface-low" title="Dominated — another model beats this one on quality, speed, and cost.">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
