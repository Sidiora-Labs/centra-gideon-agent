import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

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
  return <section aria-label="Direct peers" className="space-y-m">
    <h2>Direct peer identity</h2><p>Each peer has an Ed25519 identity and separate send and receive scopes. Private identity material and proof nonces remain on this machine.</p>
    {error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading peers…</p>}
    {data && <><p>Local peer: <code>{data.self.peer_id}</code></p><Button onClick={() => setDraft(blank())}>New peer</Button><Button onClick={() => void load()}>Reload peers</Button>
      {!data.peers.length && <p>No direct peers configured.</p>}
      <ul aria-label="Configured peers">{data.peers.map(peer => <li key={peer.id}><button className="underline" onClick={() => setDraft(peer)}>{peer.label}</button> · {peer.enabled ? 'enabled' : 'disabled'} · send {peer.send_categories.length} / receive {peer.receive_categories.length}</li>)}</ul>
      <div className="grid gap-m sm:grid-cols-2">
        <label>Peer ID<input aria-label="Peer ID" value={draft.id} disabled={draft.revision > 0} onChange={event => setDraft(row => ({ ...row, id: event.target.value }))} /></label>
        <label>Label<input aria-label="Peer label" value={draft.label} onChange={event => setDraft(row => ({ ...row, label: event.target.value }))} /></label>
        <label>Endpoint<input aria-label="Peer endpoint" value={draft.endpoint} onChange={event => setDraft(row => ({ ...row, endpoint: event.target.value }))} /></label>
        <label>Ed25519 public key<input aria-label="Peer public key" value={draft.public_key} disabled={draft.revision > 0} onChange={event => setDraft(row => ({ ...row, public_key: event.target.value }))} /></label>
        <label>Send scopes<textarea aria-label="Send scopes" value={draft.send_categories.join('\n')} onChange={event => categories('send_categories', event.target.value)} /></label>
        <label>Receive scopes<textarea aria-label="Receive scopes" value={draft.receive_categories.join('\n')} onChange={event => categories('receive_categories', event.target.value)} /></label>
        <label><input aria-label="Peer enabled" type="checkbox" checked={draft.enabled} onChange={event => setDraft(row => ({ ...row, enabled: event.target.checked }))} /> Enabled</label>
      </div>
      <Button disabled={busy || !draft.id} onClick={() => void save()}>Save peer policy</Button>
      <Button variant="danger" disabled={busy || !draft.revision} onClick={() => void remove()}>Remove peer</Button>
    </>}
  </section>
}
