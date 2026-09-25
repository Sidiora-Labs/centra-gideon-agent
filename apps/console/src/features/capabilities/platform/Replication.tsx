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
  return <section aria-label="Domain replication" className="space-y-m">
    <h2>Direct domain replication</h2><p>Signed peer batches reconcile canonical domain stores. Peer identity, policy, secrets, and local sync state never enter a batch.</p>
    {error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading replication status…</p>}
    {data && <><div className="flex flex-wrap gap-m"><label>Domain<select aria-label="Replication domain" value={domain} onChange={event => { setDomain(event.target.value); setPeer('') }}>{data.domains.map(item => <option key={item.scope}>{item.scope}</option>)}</select></label>
      <label>Peer<select aria-label="Replication peer" value={peer} onChange={event => setPeer(event.target.value)}><option value="">Choose peer</option>{eligible.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label></div>
      <Button loading={busy} disabled={!peer} disabledReason="Choose an enabled peer permitted for this domain" onClick={() => void push()}>Push canonical domain</Button><Button onClick={() => void load()}>Reload replication</Button>
      {receipt && <p role="status">{receipt}</p>}
      <h3>Coverage</h3><ul>{data.domains.map(item => <li key={item.scope}>{item.scope}: {item.entries.join(', ')}</li>)}</ul>
      <h3>Inbound cursors</h3>{!data.cursors.length ? <p>No peer batches received.</p> : <ul>{data.cursors.map(item => <li key={`${item.peer_id}:${item.domain}`}>{item.peer_id} · {item.domain} · sequence {item.sequence}</li>)}</ul>}
      <h3>Conflicts</h3>{!data.conflicts.length ? <p>No unresolved replication conflicts.</p> : <ul>{data.conflicts.map(item => <li key={item.id}>{item.entry_id}/{item.entity_id} · <a href="#/settings/durability">Review in {item.surface}</a>{item.remote_row?.data && <><label>Peer fields<input aria-label={`Fields for ${item.entity_id}`} value={restore[item.id] || ''} onChange={event => setRestore(values => ({ ...values, [item.id]: event.target.value }))} placeholder="title, content" /></label><Button disabled={busy || !(restore[item.id] || '').trim()} disabledReason="Enter comma-separated fields" onClick={() => void restoreFields(item)}>Restore selected fields</Button></>}</li>)}</ul>}
    </>}
  </section>
}
