import { useEffect, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { WhitepagesBrokerPanel } from './WhitepagesBrokerPanel'

type Broker = { id: string; name: string; website: string; optout_url: string; source: string; enabled: boolean }
type Case = { id: string; revision: number; subject_id: string; broker_id: string; state: string; evidence_basis: string; evidence: string; reason: string; next_recheck_at: string; allowed_transitions: string[]; broker?: Broker }
type Event = { revision: number; operation: string; state: string; at: string; evidence_basis?: string }
type SpokeoPlan = { method: string; form_url: string; disclosed_fields: string[]; approval_phrase: string; submission_mode: 'disabled' | 'contract' | 'live'; live_submission_enabled: boolean }
const base = '/api/capabilities/wellbeing/privacy'

export default function PrivacyBrokers({ subject }: { subject: string }) {
  const { query, setQuery } = useHashRoute('capabilities')
  const [brokers, setBrokers] = useState<Broker[]>([]), [cases, setCases] = useState<Case[]>([])
  const [history, setHistory] = useState<Case[]>([]), [events, setEvents] = useState<Event[]>([])
  const [name, setName] = useState(''), [website, setWebsite] = useState(''), [optoutUrl, setOptoutUrl] = useState(''), [source, setSource] = useState('owner supplied')
  const [brokerId, setBrokerId] = useState(''), [outcome, setOutcome] = useState('found'), [evidence, setEvidence] = useState(''), [reason, setReason] = useState('')
  const [firstName, setFirstName] = useState(''), [lastName, setLastName] = useState(''), [city, setCity] = useState(''), [region, setRegion] = useState('')
  const [profileUrl, setProfileUrl] = useState(''), [email, setEmail] = useState(''), [spokeoPlan, setSpokeoPlan] = useState<SpokeoPlan | null>(null)
  const [approved, setApproved] = useState(false), [providerStatus, setProviderStatus] = useState('')
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const selected = cases.find(row => row.id === query.broker_case)

  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    Promise.all([
      requestJson<{ brokers: Broker[] }>(base + '/brokers'),
      requestJson<{ cases: Case[] }>(`${base}/subjects/${subject}/broker-cases`),
    ]).then(([catalog, ledger]) => {
      if (!active) return
      setBrokers(catalog.brokers); setCases(ledger.cases)
      if (!catalog.brokers.some(row => row.id === brokerId)) setBrokerId(catalog.brokers[0]?.id ?? '')
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [subject, generation])

  useEffect(() => {
    let active = true
    if (!query.broker_case) { setHistory([]); setEvents([]); return () => { active = false } }
    Promise.all([
      requestJson<{ history: Case[] }>(`${base}/broker-cases/${query.broker_case}/history`),
      requestJson<{ events: Event[] }>(`${base}/broker-cases/${query.broker_case}/events`),
    ]).then(([revisions, activity]) => { if (active) { setHistory(revisions.history); setEvents(activity.events) } })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { active = false }
  }, [query.broker_case, generation])

  async function mutate(action: () => Promise<unknown>) {
    if (busy) return
    setBusy(true); setError('')
    try { await action(); setEvidence(''); setReason(''); setGeneration(value => value + 1) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setBusy(false) }
  }

  function addBroker(event: FormEvent) {
    event.preventDefault()
    void mutate(async () => {
      const row = await requestJson<Broker>(base + '/brokers', 'POST', { request_id: crypto.randomUUID(), name, website, optout_url: optoutUrl, source })
      setBrokerId(row.id); setName(''); setWebsite(''); setOptoutUrl('')
    })
  }

  function addCase(event: FormEvent) {
    event.preventDefault()
    void mutate(async () => {
      const row = await requestJson<Case>(`${base}/subjects/${subject}/broker-cases`, 'POST', { request_id: crypto.randomUUID(), broker_id: brokerId })
      setQuery({ broker_case: row.id })
    })
  }

  function observe(event: FormEvent) {
    event.preventDefault(); if (!selected) return
    void mutate(() => requestJson(`${base}/broker-cases/${selected.id}/observe`, 'POST', { request_id: crypto.randomUUID(), revision: selected.revision, outcome, evidence }))
  }

  function transition(state: string) {
    if (!selected) return
    void mutate(() => requestJson(`${base}/broker-cases/${selected.id}/transition`, 'POST', { request_id: crypto.randomUUID(), revision: selected.revision, state, reason }))
  }

  function recheck() {
    if (!selected) return
    void mutate(() => requestJson(`${base}/broker-cases/${selected.id}/recheck`, 'POST', { request_id: crypto.randomUUID(), revision: selected.revision }))
  }

  function spokeo(action: 'scan' | 'prepare' | 'submit' | 'verify') {
    if (!selected) return
    void mutate(async () => {
      const identity = { first_name: firstName, last_name: lastName, city, state: region }
      const optout = { profile_url: profileUrl, email }
      const payload = action === 'scan' || action === 'verify' ? identity : optout
      const response = await requestJson<Case | { case: Case; plan: SpokeoPlan }>(
        `${base}/broker-cases/${selected.id}/providers/spokeo/${action}`, 'POST',
        { request_id: crypto.randomUUID(), revision: selected.revision, ...payload,
          ...(action === 'submit' ? { approval: spokeoPlan?.approval_phrase } : {}) })
      if (action === 'prepare' && 'plan' in response) {
        setSpokeoPlan(response.plan)
        setApproved(false)
        setProviderStatus(response.plan.submission_mode === 'contract'
          ? 'Contract server is ready for an owner-approved submission. This is not live-broker readiness; preparation sent no opt-out request.'
          : response.plan.submission_mode === 'live'
            ? 'Live provider submission is enabled and ready for separate owner approval. Preparation sent no opt-out request.'
            : 'Prepared without submitting. Live submission is disabled on this runtime.')
      } else {
        if (action === 'submit') { setSpokeoPlan(null); setApproved(false) }
        setProviderStatus(action === 'scan' ? 'Provider scan recorded.' : action === 'submit' ? 'Provider acknowledged the approved submission.' : 'Provider verification recorded.')
      }
    })
  }

  return <section aria-label="Privacy broker cases" className="space-y-5">
    <h2 data-type="title-m">Privacy broker cases</h2>
    <p>Track owner-observed exposure and opt-out work without sending requests. Owner reports stay labelled user-attested. Confirmed removal is reserved for an integrated verifier re-scan.</p>
    {loading && <p role="status">Loading broker cases…</p>}
    {error && <p role="alert">{error}</p>}
    <form onSubmit={addBroker} className="grid gap-3 sm:grid-cols-2">
      <h3 className="sm:col-span-2">Add broker</h3>
      <Field label="Broker name"><TextInput value={name} onChange={setName} required /></Field>
      <Field label="Broker website"><TextInput value={website} onChange={setWebsite} required /></Field>
      <Field label="Broker opt-out URL"><TextInput value={optoutUrl} onChange={setOptoutUrl} /></Field>
      <Field label="Broker source"><TextInput value={source} onChange={setSource} required /></Field>
      <Button type="submit" disabled={busy}>Save broker</Button>
    </form>
    <form onSubmit={addCase} className="space-y-3">
      <h3>Start subject case</h3>
      <label>Broker<select aria-label="Case broker" value={brokerId} onChange={event => setBrokerId(event.target.value)}>{brokers.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
      <Button type="submit" disabled={busy || !brokerId}>Start broker case</Button>
    </form>
    <div className="flex flex-wrap gap-2">{cases.map(row => <Button key={row.id} variant="secondary" onClick={() => setQuery({ broker_case: row.id })}>{row.broker?.name ?? row.broker_id} · {row.state}</Button>)}</div>
    {selected && <section aria-label="Selected broker case" className="space-y-3">
      <h3>{selected.broker?.name ?? selected.broker_id}</h3>
      <p>State: {selected.state} · evidence: {selected.evidence_basis} · revision {selected.revision}</p>
      {selected.evidence && <p>Recorded evidence: {selected.evidence}</p>}
      <p>Next re-check: {selected.next_recheck_at}</p>
      {selected.broker?.name.trim().toLowerCase() === 'spokeo' && <section aria-label="Spokeo provider controls" className="space-y-3">
        <h4>Spokeo provider</h4>
        <p>Each action uses the current provider protocol and writes its durable outcome to this case. No action runs automatically.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Spokeo first name"><TextInput value={firstName} onChange={setFirstName} required /></Field>
          <Field label="Spokeo last name"><TextInput value={lastName} onChange={setLastName} required /></Field>
          <Field label="Spokeo city"><TextInput value={city} onChange={setCity} /></Field>
          <Field label="Spokeo state"><TextInput value={region} onChange={setRegion} required /></Field>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={busy || !firstName || !lastName || !region} onClick={() => spokeo('scan')}>Scan Spokeo</Button>
          <Button variant="secondary" disabled={busy || !firstName || !lastName || !region || !['submitted', 'verification_pending', 'awaiting_processing'].includes(selected.state)} onClick={() => spokeo('verify')}>Verify removal</Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Spokeo profile URL"><TextInput value={profileUrl} onChange={setProfileUrl} required /></Field>
          <Field label="Spokeo contact email"><TextInput value={email} onChange={setEmail} required /></Field>
        </div>
        <Button variant="secondary" disabled={busy || !profileUrl || !email || !['found', 'indirect_exposure'].includes(selected.state)} onClick={() => spokeo('prepare')}>Prepare opt-out</Button>
        {spokeoPlan && <div role="status" className="space-y-2">
          <p>{providerStatus}</p>
          <p>Submission discloses: {spokeoPlan.disclosed_fields.join(', ')}.</p>
          <label><input type="checkbox" checked={approved} onChange={event => setApproved(event.target.checked)} /> I approve this Spokeo opt-out submission</label>
          <Button disabled={busy || !approved || !spokeoPlan.live_submission_enabled} onClick={() => spokeo('submit')}>Submit approved opt-out</Button>
        </div>}
        {!spokeoPlan && providerStatus && <p role="status">{providerStatus}</p>}
      </section>}
      {selected.broker?.name.trim().toLowerCase() === 'whitepages' && <WhitepagesBrokerPanel brokerCase={selected} onChanged={row => setCases(current => current.map(item => item.id === row.id ? { ...item, ...row, broker: item.broker } : item))} />}
      <form onSubmit={observe} className="space-y-3"><label>Owner observation<select aria-label="Owner observation" value={outcome} onChange={event => setOutcome(event.target.value)}>{['found', 'not_found', 'indirect_exposure', 'blocked'].map(value => <option key={value}>{value}</option>)}</select></label><Field label="Observation evidence"><TextInput value={evidence} onChange={setEvidence} required /></Field><Button type="submit" disabled={busy}>Record user-attested observation</Button></form>
      <Field label="Transition reason"><TextInput value={reason} onChange={setReason} /></Field>
      <div className="flex flex-wrap gap-2">{selected.allowed_transitions.map(state => <Button key={state} variant="secondary" disabled={busy} onClick={() => transition(state)}>Move to {state}</Button>)}<Button variant="secondary" disabled={busy} onClick={recheck}>Request re-check</Button></div>
    </section>}
    {!!history.length && <section aria-label="Broker case history"><h3>Revision history</h3>{history.map(row => <p key={row.revision}>Revision {row.revision} · {row.state} · {row.evidence_basis}</p>)}</section>}
    {!!events.length && <section aria-label="Broker case events"><h3>Case events</h3>{events.map(row => <p key={row.revision}>Revision {row.revision} · {row.operation} · {row.state}</p>)}</section>}
  </section>
}
