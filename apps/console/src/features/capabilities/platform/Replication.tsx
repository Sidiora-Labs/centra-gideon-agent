import { Select, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

interface Peer { id: string; label: string; enabled: boolean; send_categories: string[] }
interface Conflict { id: string; entry_id: string; entity_id: string; surface: string; detected_at: string; remote_row?: { data?: Record<string, unknown> } }
interface Status { version: number; domains: { scope: string; entries: string[] }[]; cursors: { peer_id: string; domain: string; sequence: number; updated_at: string }[]; conflicts: Conflict[]; peers: Peer[] }

export default function Replication({ baseUrl = '' }: { baseUrl?: string }) {
  const url = `${baseUrl}/api/capabilities/platform/replication`
  const [data, setData] = useState<Status>(); const [peer, setPeer] = useState(''); const [domain, setDomain] = useState('workspace.records'); const [error, setError] = useState(''); const [receipt, setReceipt] = useState(''); const [busy, setBusy] = useState(false)
  const [restore, setRestore] = useState<Record<string, string>>({})
  const load = async () => { try { setData(await readJson<Status>(await gatewayRequest(url))); setError('') } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void load() }, [baseUrl])
  const push = async () => {
    setBusy(true); setError(''); setReceipt('')
    try {
      const result = await readJson<{ sequence: number; entries: { conflicts: number }[] }>(await gatewayRequest(`${url}/peers/${encodeURIComponent(peer)}/push`, 'POST', { domain }))
      setReceipt(`Accepted sequence ${result.sequence}; ${result.entries.reduce((sum, row) => sum + row.conflicts, 0)} new conflicts.`); await load()
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const restoreFields = async (conflict: Conflict) => {
    const fields = (restore[conflict.id] || '').split(',').map(value => value.trim()).filter(Boolean)
    setBusy(true); setError('')
    try { await readJson(await gatewayRequest(`${url}/conflicts/${conflict.id}/restore-fields`, 'POST', { fields })); setReceipt(`Restored ${fields.join(', ')} from the peer version.`); await load() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const eligible = data?.peers.filter(item => item.enabled && item.send_categories.includes(domain)) || []
  return <section aria-label="Domain replication" className="grid gap-l">
    <h2 data-type="title-m">Direct domain replication</h2><p>Signed peer batches reconcile canonical domain stores. Peer identity, policy, secrets, and local sync state never enter a batch.</p>
    {error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading replication status…</p>}
    {data && <><div className="flex flex-wrap gap-m"><label data-type="label-s" className="grid gap-xs">Domain<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Replication domain" value={domain} onChange={value => { setDomain(value); setPeer('') }} options={[...(data.domains.map(item => ({ value: [item.scope].join(''), label: [item.scope].join('') })) ?? [])]} /></label>
      <label data-type="label-s" className="grid gap-xs">Peer<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Replication peer" value={peer} onChange={value => setPeer(value)} options={[{ value: "", label: ["Choose peer"].join('') }, ...(eligible.map(item => ({ value: item.id, label: [item.label].join('') })) ?? [])]} /></label></div>
      <Button loading={busy} disabled={!peer} disabledReason="Choose an enabled peer permitted for this domain" onClick={() => void push()}>Push canonical domain</Button><Button onClick={() => void load()}>Reload replication</Button>
      {receipt && <p role="status">{receipt}</p>}
      <h3 data-type="headline-s">Coverage</h3><ul>{data.domains.map(item => <li key={item.scope}>{item.scope}: {item.entries.join(', ')}</li>)}</ul>
      <h3 data-type="headline-s">Inbound cursors</h3>{!data.cursors.length ? <p>No peer batches received.</p> : <ul>{data.cursors.map(item => <li key={`${item.peer_id}:${item.domain}`}>{item.peer_id} · {item.domain} · sequence {item.sequence}</li>)}</ul>}
      <h3 data-type="headline-s">Conflicts</h3>{!data.conflicts.length ? <p>No unresolved replication conflicts.</p> : <ul>{data.conflicts.map(item => <li key={item.id}>{item.entry_id}/{item.entity_id} · <a href="#/settings/durability">Review in {item.surface}</a>{item.remote_row?.data && <><label data-type="label-s" className="grid gap-xs">Peer fields<TextInput className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel={`Fields for ${item.entity_id}`} value={restore[item.id] || ''} onChange={value => setRestore(values => ({ ...values, [item.id]: value }))} placeholder="title, content" /></label><Button disabled={busy || !(restore[item.id] || '').trim()} disabledReason={busy ? BUSY_REASON : "Enter comma-separated fields"} onClick={() => void restoreFields(item)} ariaLabel={`Restore selected fields for ${typeof item.remote_row?.data?.title === "string" ? item.remote_row.data.title : typeof item.remote_row?.data?.name === "string" ? item.remote_row.data.name : item.surface + " conflict"}`}>Restore selected fields</Button></>}</li>)}</ul>}
    </>}
  </section>
}
