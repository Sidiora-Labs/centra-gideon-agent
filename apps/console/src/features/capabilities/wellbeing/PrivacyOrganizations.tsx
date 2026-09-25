import { useEffect, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

export type PrivateFactMetadata = { id: string; revision: number; label: string; masked_value: string; archived: boolean }
type Organization = { id: string; revision: number; name: string; category: string; website: string; contact: string; source: string; archived: boolean }
type Holding = { id: string; revision: number; org_id: string; fact_id: string; fact_revision: number; status: string; source: string; change_id: string | null }
type Target = { org_id: string; revision: number; status: string; evidence: string; attestation: string }
type Change = { id: string; fact_id: string; from_revision: number; to_revision: number; source: string; targets: Target[]; progress: { pending: number; updated: number; removed: number; total: number } }
const base = '/api/capabilities/wellbeing/privacy'

export default function PrivacyOrganizations({ subject, facts }: { subject: string; facts: PrivateFactMetadata[] }) {
  const { query, setQuery } = useHashRoute('capabilities')
  const [orgs, setOrgs] = useState<Organization[]>([]), [holdings, setHoldings] = useState<Holding[]>([]), [changes, setChanges] = useState<Change[]>([])
  const [orgHistory, setOrgHistory] = useState<Organization[]>([]), [itemHistory, setItemHistory] = useState<Array<Holding | Target>>([])
  const [name, setName] = useState(''), [category, setCategory] = useState('other'), [website, setWebsite] = useState(''), [contact, setContact] = useState(''), [source, setSource] = useState('owner supplied'), [archived, setArchived] = useState(false)
  const [factId, setFactId] = useState(facts[0]?.id ?? ''), [holdingStatus, setHoldingStatus] = useState('held'), [fromRevision, setFromRevision] = useState('1'), [evidence, setEvidence] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const selectedOrg = orgs.find(row => row.id === query.org)
  const selectedChange = changes.find(row => row.id === query.change)
  useEffect(() => {
    let active = true; setError('')
    Promise.all([
      requestJson<{ organizations: Organization[] }>(`${base}/subjects/${subject}/organizations`),
      requestJson<{ changes: Change[] }>(`${base}/subjects/${subject}/changes`),
      query.org ? requestJson<{ holdings: Holding[] }>(`${base}/organizations/${query.org}/holdings`) : Promise.resolve({ holdings: [] }),
      query.org ? requestJson<{ history: Organization[] }>(`${base}/organizations/${query.org}/history`) : Promise.resolve({ history: [] }),
    ]).then(([organizations, events, held, history]) => {
      if (!active) return
      setOrgs(organizations.organizations); setChanges(events.changes); setHoldings(held.holdings); setOrgHistory(history.history)
      const row = organizations.organizations.find(item => item.id === query.org)
      if (row) { setName(row.name); setCategory(row.category); setWebsite(row.website); setContact(row.contact); setSource(row.source); setArchived(row.archived) }
      else { setName(''); setCategory('other'); setWebsite(''); setContact(''); setSource('owner supplied'); setArchived(false) }
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : String(reason)) })
    return () => { active = false }
  }, [subject, query.org, generation])
  useEffect(() => { if (!facts.some(row => row.id === factId)) setFactId(facts[0]?.id ?? '') }, [facts, factId])
  async function mutate(action: () => Promise<unknown>) {
    if (busy) return
    setBusy(true); setError('')
    try { await action(); setEvidence(''); setItemHistory([]); setGeneration(value => value + 1) }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  function saveOrg(event: FormEvent) {
    event.preventDefault()
    void mutate(async () => {
      const body = selectedOrg
        ? { request_id: crypto.randomUUID(), revision: selectedOrg.revision, name, category, website, contact, archived }
        : { request_id: crypto.randomUUID(), name, category, website, contact, source }
      const row = await requestJson<Organization>(selectedOrg ? `${base}/organizations/${selectedOrg.id}` : `${base}/subjects/${subject}/organizations`, selectedOrg ? 'PUT' : 'POST', body)
      setQuery({ org: row.id, change: null })
    })
  }
  function saveHolding(event: FormEvent) {
    event.preventDefault(); const fact = facts.find(row => row.id === factId); if (!selectedOrg || !fact) return
    const prior = holdings.find(row => row.fact_id === fact.id)
    void mutate(() => requestJson(`${base}/organizations/${selectedOrg.id}/holdings`, 'POST', { request_id: crypto.randomUUID(), revision: prior?.revision ?? 0, fact_id: fact.id, fact_revision: fact.revision, status: holdingStatus, source }))
  }
  function declare(event: FormEvent) {
    event.preventDefault(); const fact = facts.find(row => row.id === factId); if (!fact) return
    void mutate(async () => {
      const row = await requestJson<Change>(`${base}/subjects/${subject}/changes`, 'POST', { request_id: crypto.randomUUID(), fact_id: fact.id, from_revision: Number(fromRevision), to_revision: fact.revision, source })
      setQuery({ change: row.id })
    })
  }
  function settle(org: string, status: 'updated' | 'removed', revision: number) {
    if (!selectedChange) return
    void mutate(() => requestJson(`${base}/changes/${selectedChange.id}/organizations/${org}`, 'POST', { request_id: crypto.randomUUID(), revision, status, evidence }))
  }
  async function showHoldingHistory(id: string) {
    try { setItemHistory((await requestJson<{ history: Holding[] }>(`${base}/holdings/${id}/history`)).history) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  async function showTargetHistory(change: string, org: string) {
    try { setItemHistory((await requestJson<{ history: Target[] }>(`${base}/changes/${change}/organizations/${org}/history`)).history) } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  return <section aria-label="Organizations and change propagation" className="space-y-5">
    <h2 data-type="title-m">Organizations and changed facts</h2><p>Track which organizations hold masked private facts. Status and evidence are owner-attested; no message is sent and no private value leaves this device.</p>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={() => setQuery({ org: null, change: null })}>New organization</Button>{orgs.map(row => <Button key={row.id} variant="secondary" onClick={() => setQuery({ org: row.id, change: null })}>{row.name} · revision {row.revision}</Button>)}</div>
    <form onSubmit={saveOrg} className="grid gap-3 sm:grid-cols-2"><h3 className="sm:col-span-2">{selectedOrg ? 'Edit organization' : 'Record organization'}</h3><Field label="Organization name"><TextInput value={name} onChange={setName} required /></Field><Field label="Organization category"><TextInput value={category} onChange={setCategory} required /></Field><Field label="Organization website"><TextInput value={website} onChange={setWebsite} /></Field><Field label="Organization contact"><TextInput value={contact} onChange={setContact} /></Field><Field label="Organization source"><TextInput value={source} onChange={setSource} required disabled={!!selectedOrg} /></Field>{selectedOrg && <label><input type="checkbox" checked={archived} onChange={event => setArchived(event.target.checked)} />Archive organization</label>}<Button type="submit" disabled={busy}>Save organization</Button></form>
    {!!orgHistory.length && <section aria-label="Organization history">{orgHistory.map(row => <p key={row.revision}>Revision {row.revision}: {row.name} · {row.archived ? 'archived' : 'active'}</p>)}</section>}
    {selectedOrg && <form onSubmit={saveHolding} className="space-y-3"><h3>Record a holding</h3><label>Private fact<select aria-label="Holding private fact" value={factId} onChange={event => setFactId(event.target.value)}>{facts.filter(row => !row.archived).map(row => <option key={row.id} value={row.id}>{row.label} · {row.masked_value} · revision {row.revision}</option>)}</select></label><label>Holding status<select aria-label="Holding status" value={holdingStatus} onChange={event => setHoldingStatus(event.target.value)}><option value="held">held</option><option value="unknown">unknown</option><option value="removed">removed</option></select></label><Button type="submit" disabled={busy || !factId}>Save holding</Button><ul>{holdings.map(row => <li key={row.id}>{facts.find(fact => fact.id === row.fact_id)?.label ?? row.fact_id} · revision {row.revision} · {row.status} <Button variant="secondary" onClick={() => void showHoldingHistory(row.id)}>Holding history</Button></li>)}</ul></form>}
    <form onSubmit={declare} className="space-y-3"><h3>Declare a changed fact</h3><label>Changed private fact<select aria-label="Changed private fact" value={factId} onChange={event => setFactId(event.target.value)}>{facts.filter(row => !row.archived).map(row => <option key={row.id} value={row.id}>{row.label} · current revision {row.revision}</option>)}</select></label><Field label="Previous fact revision"><TextInput value={fromRevision} onChange={setFromRevision} required /></Field><Button type="submit" disabled={busy || !factId}>Start change checklist</Button></form>
    <div className="flex flex-wrap gap-2">{changes.map(row => <Button key={row.id} variant="secondary" onClick={() => setQuery({ change: row.id })}>Revision {row.from_revision} → {row.to_revision}: {row.progress.pending}/{row.progress.total} pending</Button>)}</div>
    {selectedChange && <section aria-label="Change progress" className="space-y-3"><h3>Derived progress</h3><p>{selectedChange.progress.pending} pending · {selectedChange.progress.updated} updated · {selectedChange.progress.removed} removed · {selectedChange.progress.total} total</p><Field label="Disposition evidence"><TextInput value={evidence} onChange={setEvidence} maxLength={2000} /></Field>{selectedChange.targets.map(target => <article key={target.org_id}><p>{orgs.find(row => row.id === target.org_id)?.name ?? target.org_id} · {target.status} · {target.attestation}{target.evidence ? ` · ${target.evidence}` : ''}</p>{target.status === 'pending' && <div className="flex gap-2"><Button onClick={() => settle(target.org_id, 'updated', target.revision)} disabled={busy}>Attest updated</Button><Button variant="secondary" onClick={() => settle(target.org_id, 'removed', target.revision)} disabled={busy}>Attest removed</Button></div>}<Button variant="secondary" onClick={() => void showTargetHistory(selectedChange.id, target.org_id)}>Disposition history</Button></article>)}</section>}
    {!!itemHistory.length && <section aria-label="Holding and disposition history">{itemHistory.map((row, index) => <p key={`${row.revision}:${index}`}>Revision {row.revision} · {row.status}{'evidence' in row && row.evidence ? ` · ${row.evidence}` : ''}</p>)}</section>}
  </section>
}
