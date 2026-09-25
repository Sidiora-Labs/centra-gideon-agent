import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListSkeleton } from '../../../shared/ui/ListScaffold'
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
import { Cable } from 'lucide-react'

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
  return <section aria-label="Provider connections" className="grid min-w-0 gap-l text-on-surface">
    <div className="flex flex-wrap items-start justify-between gap-m"><div><h2 data-type="title-m">Provider connections</h2><p data-type="body-s" className="mt-1 max-w-[48rem] text-on-surface-low">Connect provider endpoints, named credentials, and model access rules for this machine.</p></div><div className="flex flex-wrap gap-s"><Button onClick={() => { onSelect(''); setDraft(empty()) }}>New connection</Button><Button variant="secondary" onClick={() => setReload(value => value + 1)}>Refresh</Button></div></div>
    {error && <p role="alert" className="rounded-lg bg-danger/10 px-m py-s text-sm text-danger">{error}</p>}
    {!inventory && !error && <ListSkeleton rows={2} what="provider connections" />}
    {inventory && <>
      {!inventory.connections.length ? <EmptyState icon={Cable} title="No shared connections configured." hint="Create a connection to bind an installed provider to an endpoint and credential." /> : <div className="flex flex-wrap gap-s" aria-label="Configured provider connections">{inventory.connections.map(row => <Button key={row.id} variant={selected === row.id ? 'tonal' : 'secondary'} onClick={() => onSelect(row.id)}>{row.label}</Button>)}</div>}
      <Surface className="grid gap-l p-l"><div><h3 data-type="headline-s">{draft.revision ? `Edit ${draft.label}` : 'Connection details'}</h3><p data-type="caption" className="mt-1 text-on-surface-low">Credential values remain in the credential store; this form saves only their names.</p></div><div className="grid gap-m sm:grid-cols-2">
        <Field label="Connection ID"><TextInput ariaLabel="Connection ID" mono value={draft.id} disabled={draft.revision > 0} disabledReason="Connection IDs cannot change after creation" onChange={value => change('id', value)} /></Field>
        <Field label="Connection label"><TextInput ariaLabel="Connection label" value={draft.label} onChange={value => change('label', value)} /></Field>
        <Field label="Endpoint"><TextInput ariaLabel="Endpoint" value={draft.base_url} onChange={value => change('base_url', value)} /></Field>
        <Field label="Credential reference"><Select ariaLabel="Credential reference" value={draft.credential_ref || ''} onChange={value => change('credential_ref', value)} options={[{ value: '', label: 'No credential' }, ...inventory.credentials.map(value => ({ value, label: value }))]} /></Field>
        <Field label="Model access"><Select ariaLabel="Model access" value={draft.model_access.mode} onChange={value => setDraft(row => ({ ...row, model_access: { ...row.model_access, mode: value } }))} options={['all', 'allow', 'deny'].map(value => ({ value, label: value }))} /></Field>
        <Field label="Model patterns" hint="Enter one model pattern per line."><TextArea ariaLabel="Model patterns" mono rows={4} value={draft.model_access.patterns.join('\n')} onChange={value => setDraft(row => ({ ...row, model_access: { ...row.model_access, patterns: value.split('\n') } }))} /></Field>
      </div><div><Button loading={busy} disabled={!draft.id.trim() || !draft.label.trim() || !draft.base_url.trim()} onClick={() => void write(`/${key}`, 'PUT', { ...draft, model_access: { ...draft.model_access, patterns: draft.model_access.patterns.filter(value => value.trim()) } })}>Save connection</Button></div></Surface>
      {draft.revision > 0 && <>
        <Surface className="grid gap-m p-l"><h3 data-type="headline-s">Bound providers</h3>{draft.bindings.length ? <div className="grid gap-s">{draft.bindings.map(name => <div key={name} className="flex items-center justify-between gap-m rounded-lg bg-surface px-m py-s"><span>{name}</span><Button size="sm" variant="secondary" disabled={busy} onClick={() => void write(`/${key}/bindings/${encodeURIComponent(name)}`, 'PUT', { revision: draft.revision, bound: false })}>Unbind {name}</Button></div>)}</div> : <p data-type="body-s" className="text-on-surface-low">No providers use this connection.</p>}<div className="grid gap-m sm:grid-cols-[minmax(0,1fr)_auto]"><Field label="Provider to bind"><Select ariaLabel="Provider to bind" value={provider} onChange={setProvider} options={[{ value: '', label: 'Choose provider' }, ...inventory.providers.filter(row => !row.connection_id).map(row => ({ value: row.name, label: row.name }))]} /></Field><div className="flex items-end"><Button disabled={!provider || busy} disabledReason="Choose an unbound provider" onClick={() => void write(`/${key}/bindings/${encodeURIComponent(provider)}`, 'PUT', { revision: draft.revision, bound: true })}>Bind provider</Button></div></div><div><Button variant="danger" disabled={busy || draft.bindings.length > 0} disabledReason="Unbind providers first" onClick={() => void write(`/${key}?revision=${draft.revision}`, 'DELETE')}>Delete connection</Button></div></Surface>
      </>}
    </>}
  </section>
}
