import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Checkbox, Field, TextArea, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

interface Peer { id: string; label: string; endpoint: string; public_key: string; enabled: boolean; send_categories: string[]; receive_categories: string[]; revision: number; last_probe: string | null }
interface Projection { version: number; self: { peer_id: string; public_key: string }; categories: string[]; peers: Peer[] }
const blank = (): Peer => ({ id: '', label: '', endpoint: '', public_key: '', enabled: true, send_categories: [], receive_categories: [], revision: 0, last_probe: null })

export default function Peers({ baseUrl = '' }: { baseUrl?: string }) {
  const url = `${baseUrl}/api/capabilities/platform/peers`
  const [data, setData] = useState<Projection>(); const [draft, setDraft] = useState(blank); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const load = async () => { try { setData(await readJson<Projection>(await gatewayRequest(url))); setError('') } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void load() }, [baseUrl])
  const save = async () => {
    setBusy(true); setError('')
    try {
      const { id } = draft
      const body = { label: draft.label, endpoint: draft.endpoint, public_key: draft.public_key, enabled: draft.enabled, send_categories: draft.send_categories, receive_categories: draft.receive_categories, revision: draft.revision }
      const saved = await readJson<Peer>(await gatewayRequest(`${url}/${encodeURIComponent(id)}`, 'PUT', body))
      setDraft(saved); await load()
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true); setError('')
    try { await readJson<Projection>(await gatewayRequest(`${url}/${encodeURIComponent(draft.id)}?revision=${draft.revision}`, 'DELETE')); setDraft(blank()); await load() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const categories = (key: 'send_categories' | 'receive_categories', value: string) => setDraft(row => ({ ...row, [key]: value.split('\n').map(item => item.trim()).filter(Boolean) }))
  return <section aria-label="Direct peers" className="grid gap-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div><h2 data-type="title-m">Direct peers</h2><p data-type="body-s" className="mt-1 max-w-[48rem] text-on-surface-low">Configure Ed25519 peer identities and independent send and receive scopes. Private identity material stays on this machine.</p></div><div className="flex gap-s"><Button onClick={() => setDraft(blank())}>New peer</Button><Button variant="secondary" onClick={() => void load()}>Refresh</Button></div></div>
    {error && <p role="alert" className="rounded-lg bg-danger/10 px-m py-s text-sm text-danger">{error}</p>}{!data && !error && <p role="status" className="text-sm text-on-surface-low">Loading peers…</p>}
    {data && <><p data-type="body-s">Local peer: <code className="rounded bg-surface-container px-s py-xs">{data.self.peer_id}</code></p>
      {!data.peers.length ? <p>No direct peers configured.</p> : <div className="grid gap-s" aria-label="Configured peers">{data.peers.map(peer => <div key={peer.id} className="flex flex-wrap items-center gap-s"><Button ariaLabel={peer.label} variant={draft.id === peer.id ? 'tonal' : 'secondary'} onClick={() => setDraft(peer)}>{peer.label}</Button><span data-type="caption" className="text-on-surface-low">{peer.enabled ? 'enabled' : 'disabled'} · send {peer.send_categories.length} / receive {peer.receive_categories.length}</span></div>)}</div>}
      <Surface className="grid gap-l p-l"><div className="grid gap-m sm:grid-cols-2">
        <Field label="Peer ID"><TextInput ariaLabel="Peer ID" mono value={draft.id} disabled={draft.revision > 0} onChange={value => setDraft(row => ({ ...row, id: value }))} /></Field>
        <Field label="Label"><TextInput ariaLabel="Peer label" value={draft.label} onChange={value => setDraft(row => ({ ...row, label: value }))} /></Field>
        <Field label="Endpoint"><TextInput ariaLabel="Peer endpoint" value={draft.endpoint} onChange={value => setDraft(row => ({ ...row, endpoint: value }))} /></Field>
        <Field label="Ed25519 public key"><TextInput ariaLabel="Peer public key" mono value={draft.public_key} disabled={draft.revision > 0} onChange={value => setDraft(row => ({ ...row, public_key: value }))} /></Field>
        <Field label="Send scopes"><TextArea ariaLabel="Send scopes" mono value={draft.send_categories.join('\n')} onChange={value => categories('send_categories', value)} /></Field>
        <Field label="Receive scopes"><TextArea ariaLabel="Receive scopes" mono value={draft.receive_categories.join('\n')} onChange={value => categories('receive_categories', value)} /></Field>
        <Field label="Status"><label className="flex min-h-10 items-center gap-s rounded-md border border-outline-variant/30 bg-surface-container px-m"><Checkbox ariaLabel="Peer enabled" checked={draft.enabled} onChange={enabled => setDraft(row => ({ ...row, enabled }))} /><span data-type="body-s">Enabled</span></label></Field>
      </div><div className="flex flex-wrap gap-s"><Button disabled={busy || !draft.id} onClick={() => void save()}>Save peer policy</Button><Button variant="danger" disabled={busy || !draft.revision} onClick={() => void remove()}>Remove peer</Button></div></Surface>
    </>}
  </section>
}
