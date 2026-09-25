import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import PrivacyOrganizations from './PrivacyOrganizations'
import PrivacyBrokers from './PrivacyBrokers'

type Subject = { id: string; alias: string; relationship: string; source: string }
type Consent = { scope: string; revision: number; granted: boolean; method: string }
type Fact = { id: string; subject_id: string; revision: number; type: string; label: string; source: string; masked_value: string; archived: boolean; use_for_scans: boolean }
type Audit = { id: string; operation: string; outcome?: string; at: string }
const base = '/api/capabilities/wellbeing/privacy'

export default function Privacy() {
  const { query, setQuery } = useHashRoute('capabilities')
  const [subjects, setSubjects] = useState<Subject[]>([]), [consents, setConsents] = useState<Consent[]>([]), [facts, setFacts] = useState<Fact[]>([]), [audit, setAudit] = useState<Audit[]>([]), [history, setHistory] = useState<Fact[]>([])
  const [alias, setAlias] = useState(''), [relationship, setRelationship] = useState('self'), [subjectSource, setSubjectSource] = useState('')
  const [scope, setScope] = useState('vault'), [method, setMethod] = useState(''), [granted, setGranted] = useState(true)
  const [type, setType] = useState('email'), [label, setLabel] = useState(''), [source, setSource] = useState(''), [value, setValue] = useState(''), [password, setPassword] = useState(''), [scans, setScans] = useState(false), [archived, setArchived] = useState(false)
  const [revealPassword, setRevealPassword] = useState(''), [reason, setReason] = useState(''), [revealed, setRevealed] = useState('')
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const selection = useRef('')
  selection.current = `${query.subject ?? ''}:${query.fact ?? ''}`
  const selected = facts.find(fact => fact.id === query.fact)
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setRevealed(''); setValue(''); setPassword(''); setRevealPassword('')
    Promise.all([requestJson<{ subjects: Subject[] }>(base + '/subjects'), query.subject ? requestJson<{ consents: Consent[] }>(`${base}/subjects/${query.subject}/consents`) : Promise.resolve({ consents: [] }), query.subject ? requestJson<{ facts: Fact[] }>(`${base}/subjects/${query.subject}/facts`) : Promise.resolve({ facts: [] }), query.subject ? requestJson<{ audit: Audit[] }>(`${base}/subjects/${query.subject}/audit`) : Promise.resolve({ audit: [] }), query.fact ? requestJson<{ history: Fact[] }>(`${base}/facts/${query.fact}/history`) : Promise.resolve({ history: [] })])
      .then(([list, permissions, records, events, revisions]) => { if (active) { setSubjects(list.subjects); setConsents(permissions.consents); setFacts(records.facts); setAudit(events.audit); setHistory(revisions.history); const row = records.facts.find(fact => fact.id === query.fact); setLabel(row?.label ?? ''); setType(row?.type ?? 'email'); setSource(row?.source ?? ''); setScans(row?.use_for_scans ?? false); setArchived(row?.archived ?? false) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [query.subject, query.fact, generation])
  async function mutate(kind: string, event: FormEvent) {
    event.preventDefault(); if (busy) return
    const context = selection.current
    setBusy(true); setError(''); setRevealed('')
    try {
      if (kind === 'subject') {
        const row = await requestJson<Subject>(base + '/subjects', 'POST', { request_id: crypto.randomUUID(), alias, relationship, source: subjectSource }); setQuery({ subject: row.id, fact: null }); setAlias(''); setSubjectSource('')
      } else if (kind === 'consent') {
        const revision = Math.max(0, ...consents.filter(row => row.scope === scope).map(row => row.revision))
        await requestJson(`${base}/subjects/${query.subject}/consents`, 'POST', { request_id: crypto.randomUUID(), revision, scope, granted, method })
      } else if (kind === 'fact') {
        const payload = { request_id: crypto.randomUUID(), value, passphrase: password, label, use_for_scans: scans, ...(selected ? { revision: selected.revision, archived } : { type, source }) }
        const row = await requestJson<Fact>(selected ? `${base}/facts/${selected.id}` : `${base}/subjects/${query.subject}/facts`, selected ? 'PUT' : 'POST', payload)
        setQuery({ fact: row.id })
      } else {
        const result = await requestJson<{ value: string }>(`${base}/facts/${selected!.id}/reveal`, 'POST', { passphrase: revealPassword, reason }); if (selection.current === context) setRevealed(result.value)
        const events = await requestJson<{ audit: Audit[] }>(`${base}/subjects/${query.subject}/audit`); if (selection.current === context) setAudit(events.audit)
      }
      if (kind !== 'reveal') setGeneration(n => n + 1)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false); setPassword(''); setValue(''); setRevealPassword('') }
  }
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface"><h1 data-type="headline-s">Private identity facts</h1><p>Facts are encrypted locally. Enter the same passphrase for every correction or reveal; Gideon does not keep it. Aliases, labels and source descriptions are visible metadata: use generic descriptions. Facts are excluded from health exports, agent tools and automatic sharing.</p>{error && <p role="alert">{error}</p>}{loading && <p role="status">Loading private records…</p>}
    <section aria-label="Privacy subjects"><h2 data-type="title-m">Subjects</h2>{subjects.map(row => <Button key={row.id} variant="secondary" onClick={() => setQuery({ subject: row.id, fact: null })}>{row.alias}</Button>)}</section>
    <form onSubmit={event => mutate('subject', event)} className="space-y-3"><h2 data-type="title-m">Create subject</h2><Field label="Subject alias"><TextInput value={alias} onChange={setAlias} required /></Field><label>Relationship<select aria-label="Relationship" value={relationship} onChange={event => setRelationship(event.target.value)}>{['self', 'household', 'other'].map(item => <option key={item}>{item}</option>)}</select></label><Field label="Subject source"><TextInput value={subjectSource} onChange={setSubjectSource} required /></Field><Button type="submit" disabled={busy}>Create privacy subject</Button></form>
    {query.subject && !loading && <><form onSubmit={event => mutate('consent', event)} className="space-y-3"><h2 data-type="title-m">Explicit scoped consent</h2><label>Consent scope<select aria-label="Consent scope" value={scope} onChange={event => setScope(event.target.value)}>{['vault', 'reveal', 'broker_scan', 'broker_submit', 'twin_share'].map(item => <option key={item}>{item}</option>)}</select></label><label><input type="checkbox" checked={granted} onChange={event => setGranted(event.target.checked)} />Grant consent</label><Field label="Consent method"><TextInput value={method} onChange={setMethod} required /></Field><Button type="submit" disabled={busy}>Record consent decision</Button><ul>{consents.map(row => <li key={`${row.scope}:${row.revision}`}>{row.scope} revision {row.revision}: {row.granted ? 'granted' : 'revoked'} · {row.method}</li>)}</ul></form>
      <section aria-label="Masked facts"><h2 data-type="title-m">Masked facts</h2><Button onClick={() => setQuery({ fact: null })}>New private fact</Button>{facts.map(row => <button key={row.id} className="block rounded-lg bg-surface-container p-3 my-2 text-left" onClick={() => setQuery({ fact: row.id })}>{row.label} · {row.masked_value} · revision {row.revision}{row.archived ? ' · archived' : ''}</button>)}</section>
      <form onSubmit={event => mutate('fact', event)} className="space-y-3"><h2 data-type="title-m">{selected ? 'Correct selected private fact' : 'Create encrypted fact'}</h2><label>Fact type<select aria-label="Fact type" value={type} disabled={!!selected} onChange={event => setType(event.target.value)}>{['legal_name', 'email', 'phone', 'address', 'birth_date', 'tax_id', 'passport', 'other'].map(item => <option key={item}>{item}</option>)}</select></label><Field label="Fact label"><TextInput value={label} onChange={setLabel} required /></Field><Field label="Fact source"><TextInput value={source} onChange={setSource} required disabled={!!selected} /></Field><Field label="Private value"><TextInput type="password" value={value} onChange={setValue} required /></Field><Field label="Encryption passphrase"><TextInput type="password" value={password} onChange={setPassword} required /></Field><label><input type="checkbox" checked={scans} onChange={event => setScans(event.target.checked)} />Allow use in explicitly consented scans</label>{selected && <label><input type="checkbox" checked={archived} onChange={event => setArchived(event.target.checked)} />Archive private fact</label>}<Button type="submit" disabled={busy}>Save encrypted fact</Button></form>
      {selected && <form onSubmit={event => mutate('reveal', event)} className="space-y-3"><h2 data-type="title-m">Explicit reveal</h2><Field label="Reveal passphrase"><TextInput type="password" value={revealPassword} onChange={setRevealPassword} required /></Field><Field label="Reveal reason"><TextInput value={reason} onChange={setReason} required /></Field><Button type="submit" disabled={busy || selected.archived}>Reveal selected fact</Button>{revealed && <section aria-label="Revealed private value"><p>{revealed}</p><Button onClick={() => setRevealed('')}>Hide revealed value</Button></section>}</form>}
      {!!history.length && <section aria-label="Private fact history">{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.label} · {row.masked_value}</p>)}</section>}<PrivacyOrganizations subject={query.subject} facts={facts} /><PrivacyBrokers subject={query.subject} /><section aria-label="Privacy audit"><h2 data-type="title-m">Privacy audit</h2>{audit.map(row => <p key={row.id}>{row.operation} {row.outcome ?? ''} · {row.at}</p>)}</section></>}
  </main>
}
