import { useEffect, useState, type FormEvent } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Plan = { id: string; revision: number; provider: string; name: string; source: string; cycle_start: string; cycle_end: string; token_limit: number | null; dollar_limit: number | null; monthly_cost_usd: number | null; archived: boolean; quota_basis: string }
type Reservation = { id: string; revision: number; run_id: string; purpose: string; expected_tokens: number; expected_dollars: number; expires_at: string; status: string; evidence_basis: string }
type Summary = { plan: Plan; usage: { tokens: number; dollars: number; turns: number; unpriced_turns: number; basis: string }; reservations: { active: number; expired_unreleased: number; reserved_tokens: number; reserved_dollars: number }; available: { tokens: number | null; dollars: number | null }; provider_evidence: null | { captured_at: string; source: string; evidence_basis: string; limits: Array<{ key: string; label: string; percent_used: number; resets_at: string }> }; coverage: string }
const base = '/api/capabilities/platform/quotas'
const numeric = (value: string) => value.trim() ? Number(value) : null

export default function Quotas({ baseUrl = '' }: { baseUrl?: string }) {
  const [plans, setPlans] = useState<Plan[]>([]), [selectedId, setSelectedId] = useState(''), [summary, setSummary] = useState<Summary>(), [reservations, setReservations] = useState<Reservation[]>([])
  const [provider, setProvider] = useState(''), [name, setName] = useState(''), [source, setSource] = useState('owner plan terms'), [cycleStart, setCycleStart] = useState(''), [cycleEnd, setCycleEnd] = useState(''), [tokenLimit, setTokenLimit] = useState(''), [dollarLimit, setDollarLimit] = useState(''), [monthlyCost, setMonthlyCost] = useState('')
  const [runId, setRunId] = useState(''), [purpose, setPurpose] = useState(''), [expectedTokens, setExpectedTokens] = useState('0'), [expectedDollars, setExpectedDollars] = useState('0'), [expiresAt, setExpiresAt] = useState(''), [releaseReason, setReleaseReason] = useState('work finished')
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const url = (path: string) => baseUrl + base + path

  useEffect(() => {
    let active = true; setLoading(true); setError('')
    requestJson<{ plans: Plan[] }>(url('/plans')).then(result => { if (active) { setPlans(result.plans); if (!result.plans.some(row => row.id === selectedId)) setSelectedId(result.plans[0]?.id ?? '') } }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [baseUrl, generation])

  useEffect(() => {
    let active = true
    if (!selectedId) { setSummary(undefined); setReservations([]); return () => { active = false } }
    Promise.all([requestJson<Summary>(url(`/plans/${selectedId}/summary`)), requestJson<{ reservations: Reservation[] }>(url(`/plans/${selectedId}/reservations`))]).then(([view, held]) => { if (active) { setSummary(view); setReservations(held.reservations) } }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { active = false }
  }, [baseUrl, selectedId, generation])

  async function mutate(action: () => Promise<unknown>) {
    if (busy) return
    setBusy(true); setError('')
    try { await action(); setGeneration(value => value + 1) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }

  function savePlan(event: FormEvent) {
    event.preventDefault()
    void mutate(async () => {
      const row = await requestJson<Plan>(url('/plans'), 'POST', { request_id: crypto.randomUUID(), provider, name, source, cycle_start: cycleStart, cycle_end: cycleEnd, token_limit: numeric(tokenLimit), dollar_limit: numeric(dollarLimit), monthly_cost_usd: numeric(monthlyCost) })
      setSelectedId(row.id)
    })
  }

  function reserve(event: FormEvent) {
    event.preventDefault(); if (!selectedId) return
    void mutate(() => requestJson(url(`/plans/${selectedId}/reservations`), 'POST', { request_id: crypto.randomUUID(), run_id: runId, purpose, expected_tokens: Number(expectedTokens), expected_dollars: Number(expectedDollars), expires_at: expiresAt }))
  }

  function release(row: Reservation) {
    void mutate(() => requestJson(url(`/reservations/${row.id}/release`), 'POST', { request_id: crypto.randomUUID(), revision: row.revision, reason: releaseReason }))
  }

  return <section aria-label="Subscription quota plans" className="space-y-5">
    <h2>Subscription quota plans and reservations</h2>
    <p>Limits and plan prices are owner-configured. Observed consumption comes from Gideon’s canonical local usage ledger. Provider quota evidence appears only when a provider adapter supplied it.</p>
    {loading && <p role="status">Loading quota plans…</p>}{error && <p role="alert">{error}</p>}
    <form onSubmit={savePlan} className="grid gap-3 sm:grid-cols-2"><h3 className="sm:col-span-2">Record plan terms</h3><Field label="Provider binding"><TextInput value={provider} onChange={setProvider} required /></Field><Field label="Plan name"><TextInput value={name} onChange={setName} required /></Field><Field label="Plan source"><TextInput value={source} onChange={setSource} required /></Field><Field label="Cycle start"><TextInput value={cycleStart} onChange={setCycleStart} required /></Field><Field label="Cycle end"><TextInput value={cycleEnd} onChange={setCycleEnd} required /></Field><Field label="Configured token limit"><TextInput value={tokenLimit} onChange={setTokenLimit} /></Field><Field label="Configured dollar limit"><TextInput value={dollarLimit} onChange={setDollarLimit} /></Field><Field label="Monthly plan cost"><TextInput value={monthlyCost} onChange={setMonthlyCost} /></Field><Button type="submit" disabled={busy}>Save quota plan</Button></form>
    <div className="flex flex-wrap gap-2">{plans.map(row => <Button key={row.id} variant="secondary" onClick={() => setSelectedId(row.id)}>{row.name} · {row.provider}</Button>)}</div>
    {summary && <section aria-label="Quota availability" className="space-y-3"><h3>{summary.plan.name}</h3><p>Quota basis: {summary.plan.quota_basis}. Usage basis: {summary.usage.basis}.</p><p>{summary.usage.tokens} observed tokens · ${summary.usage.dollars.toFixed(6)} recorded cost · {summary.usage.turns} turns · {summary.usage.unpriced_turns} unpriced</p><p>{summary.reservations.reserved_tokens} reserved tokens · ${summary.reservations.reserved_dollars.toFixed(6)} reserved · {summary.reservations.active} active · {summary.reservations.expired_unreleased} expired unreleased</p><p>{summary.available.tokens == null ? 'No configured token ceiling' : `${summary.available.tokens} tokens available`} · {summary.available.dollars == null ? 'No configured dollar ceiling' : `$${summary.available.dollars.toFixed(6)} available`}</p><p>{summary.coverage}</p>{summary.provider_evidence ? <section aria-label="Provider quota evidence"><p>{summary.provider_evidence.evidence_basis} · {summary.provider_evidence.source} · {summary.provider_evidence.captured_at}</p>{summary.provider_evidence.limits.map(limit => <p key={limit.key}>{limit.label}: {limit.percent_used}% used · resets {limit.resets_at}</p>)}</section> : <p>No provider-reported quota evidence.</p>}</section>}
    {selectedId && <form onSubmit={reserve} className="grid gap-3 sm:grid-cols-2"><h3 className="sm:col-span-2">Reserve configured capacity</h3><Field label="Run ID"><TextInput value={runId} onChange={setRunId} required /></Field><Field label="Reservation purpose"><TextInput value={purpose} onChange={setPurpose} required /></Field><Field label="Expected tokens"><TextInput value={expectedTokens} onChange={setExpectedTokens} required /></Field><Field label="Expected dollars"><TextInput value={expectedDollars} onChange={setExpectedDollars} required /></Field><Field label="Reservation expiry"><TextInput value={expiresAt} onChange={setExpiresAt} required /></Field><Button type="submit" disabled={busy}>Reserve capacity</Button></form>}
    {!!reservations.length && <section aria-label="Quota reservations"><Field label="Release reason"><TextInput value={releaseReason} onChange={setReleaseReason} /></Field>{reservations.map(row => <article key={row.id}><p>{row.run_id} · {row.purpose} · {row.status} · {row.evidence_basis} · {row.expected_tokens} tokens · ${row.expected_dollars.toFixed(6)}</p>{row.status === 'held' && <Button variant="secondary" onClick={() => release(row)} disabled={busy}>Release {row.run_id}</Button>}</article>)}</section>}
  </section>
}
