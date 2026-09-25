import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

interface Connection { id: string; label: string; base_url: string; credential_ref: string | null; model_access: { mode: string; patterns: string[] }; revision: number; bindings: string[] }
interface Inventory { version: number; connections: Connection[]; providers: { name: string; connection_id?: string }[]; credentials: string[] }
const empty = (): Connection => ({ id: '', label: '', base_url: '', credential_ref: null, model_access: { mode: 'all', patterns: [] }, revision: 0, bindings: [] })
const prefix = '/api/capabilities/platform/connections'

export function ProviderConnections({ selected, onSelect, baseUrl = '' }: { selected: string; onSelect: (id: string) => void; baseUrl?: string }) {
  const [inventory, setInventory] = useState<Inventory>()
  const [draft, setDraft] = useState(empty)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [provider, setProvider] = useState('')
  const [reload, setReload] = useState(0)
  useEffect(() => {
    let live = true
    gatewayRequest(`${baseUrl}${prefix}`).then(readJson<Inventory>).then(value => { if (live) { setInventory(value); setError('') } }).catch(reason => { if (live) setError(String(reason)) })
    return () => { live = false }
  }, [baseUrl, reload])
  useEffect(() => { setDraft(inventory?.connections.find(row => row.id === selected) || empty()) }, [inventory, selected])
  const change = (key: 'id' | 'label' | 'base_url' | 'credential_ref', value: string) => setDraft(row => ({ ...row, [key]: value }))
  const write = async (path: string, method: 'PUT' | 'DELETE', body?: unknown) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      const value = await gatewayRequest(`${baseUrl}${prefix}${path}`, method, body).then(readJson<Inventory>)
      setInventory(value)
      onSelect(method === 'DELETE' ? '' : draft.id)
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
    finally { setBusy(false) }
  }
  const key = encodeURIComponent(draft.id)
  return <section aria-label="Provider connections" className="grid min-w-0 gap-m py-l text-on-surface">
    <h2>Shared provider connections</h2>
    <p>Bind existing providers to a shared endpoint and credential reference. Model access limits choices without replacing the catalog.</p>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-s"><Button onClick={() => { onSelect(''); setDraft(empty()) }}>New connection</Button><Button onClick={() => setReload(value => value + 1)}>Reload connections</Button></div>
    {!inventory && !error && <p role="status">Loading connections…</p>}
    {inventory && <>
      {!inventory.connections.length && <p>No shared connections configured.</p>}
      <ul>{inventory.connections.map(row => <li key={row.id}><button className="underline" onClick={() => onSelect(row.id)}>{row.label}</button></li>)}</ul>
      <div className="grid gap-m sm:grid-cols-2">
        <label>Connection ID<input aria-label="Connection ID" className="block w-full bg-surface-high p-s" value={draft.id} disabled={draft.revision > 0} onChange={event => change('id', event.target.value)} /></label>
        <label>Connection label<input aria-label="Connection label" className="block w-full bg-surface-high p-s" value={draft.label} onChange={event => change('label', event.target.value)} /></label>
        <label>Endpoint<input aria-label="Endpoint" className="block w-full bg-surface-high p-s" value={draft.base_url} onChange={event => change('base_url', event.target.value)} /></label>
        <label>Credential reference<select aria-label="Credential reference" className="block w-full bg-surface-high p-s" value={draft.credential_ref || ''} onChange={event => change('credential_ref', event.target.value)}><option value="">No credential</option>{inventory.credentials.map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Model access<select aria-label="Model access" className="block w-full bg-surface-high p-s" value={draft.model_access.mode} onChange={event => setDraft(row => ({ ...row, model_access: { ...row.model_access, mode: event.target.value } }))}>{['all', 'allow', 'deny'].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Model patterns<textarea aria-label="Model patterns" className="block w-full bg-surface-high p-s" value={draft.model_access.patterns.join('\n')} onChange={event => setDraft(row => ({ ...row, model_access: { ...row.model_access, patterns: event.target.value.split('\n') } }))} /></label>
      </div>
      <Button loading={busy} onClick={() => void write(`/${key}`, 'PUT', { ...draft, model_access: { ...draft.model_access, patterns: draft.model_access.patterns.filter(value => value.trim()) } })}>Save connection</Button>
      {draft.revision > 0 && <>
        <h3>Bound providers</h3>
        <ul>{draft.bindings.map(name => <li key={name}>{name} <Button disabled={busy} onClick={() => void write(`/${key}/bindings/${encodeURIComponent(name)}`, 'PUT', { revision: draft.revision, bound: false })}>Unbind {name}</Button></li>)}</ul>
        <label>Provider to bind<select aria-label="Provider to bind" value={provider} onChange={event => setProvider(event.target.value)} className="bg-surface-high p-s"><option value="">Choose provider</option>{inventory.providers.filter(row => !row.connection_id).map(row => <option key={row.name}>{row.name}</option>)}</select></label>
        <Button disabled={!provider || busy} disabledReason="Choose an unbound provider" onClick={() => void write(`/${key}/bindings/${encodeURIComponent(provider)}`, 'PUT', { revision: draft.revision, bound: true })}>Bind provider</Button>
        <Button variant="danger" disabled={busy || draft.bindings.length > 0} disabledReason="Unbind providers first" onClick={() => void write(`/${key}?revision=${draft.revision}`, 'DELETE')}>Delete connection</Button>
      </>}
    </>}
  </section>
}
