import { Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

interface Peer { id: string; label: string }
interface Execution { id: string; request_id: string; peer_id: string; remote_job_id: string; remote_state_revision: number; status: string; error?: string | null; result?: { artifact_id?: string; version?: number } | null }
interface Projection { items: Execution[]; peers: Peer[] }

export default function RemoteMedia({ baseUrl = '' }: { baseUrl?: string }) {
  const endpoint = `${baseUrl}/api/capabilities/platform/remote-media`
  const [data, setData] = useState<Projection>()
  const [peerId, setPeerId] = useState(''); const [prompt, setPrompt] = useState(''); const [requestId, setRequestId] = useState('')
  const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const load = async () => { try { const next = await readJson<Projection>(await gatewayRequest(endpoint)); setData(next); setPeerId(value => value || next.peers[0]?.id || ''); setError('') } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void load() }, [baseUrl])
  const dispatch = async () => {
    setBusy(true); setError('')
    try {
      await readJson(await gatewayRequest(endpoint, 'POST', { peer_id: peerId, request: { request_id: requestId, operation: 'image_generate', input: { prompt } } }))
      setPrompt(''); setRequestId(''); await load()
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const action = async (item: Execution, name: 'refresh' | 'cancel') => {
    setBusy(true); setError('')
    try { await readJson(await gatewayRequest(`${endpoint}/${encodeURIComponent(item.id)}/${name}`, 'POST', name === 'cancel' ? { state_revision: item.remote_state_revision } : {})); await load() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Remote media execution" className="grid gap-l">
    <h2 data-type="title-m">Remote media execution</h2><p>Send bounded image jobs to a direct peer. Queue admission is recorded here; model execution and artifacts remain attributable to that peer.</p>
    {error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading remote media…</p>}
    {data && <>
      <label className="grid gap-xs text-sm">Execution peer<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Execution peer" value={peerId} onChange={value => setPeerId(value)} options={[{ value: "", label: ["Select a peer"].join('') }, ...(data.peers.map(peer => ({ value: peer.id, label: [peer.label].join('') })) ?? [])]} /></label>
      <label className="grid gap-xs text-sm">Request ID<TextInput className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Request ID" value={requestId} maxLength={40} onChange={value => setRequestId(value)} /></label>
      <label className="grid gap-xs text-sm">Image prompt<TextArea className="min-h-24 w-full resize-y rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Image prompt" value={prompt} maxLength={4000} onChange={value => setPrompt(value)} /></label>
      <Button disabled={busy || !peerId || !requestId || !prompt.trim()} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void dispatch()}>Dispatch remote job</Button>
      {!data.items.length && <p>No remote executions recorded.</p>}
      <ul aria-label="Remote executions">{data.items.map(item => <li key={item.id}>
        <strong>{item.request_id}</strong> · {item.status} · peer <code>{item.peer_id}</code> · job <code>{item.remote_job_id}</code>
        {item.result?.artifact_id && <span> · artifact <code>{item.result.artifact_id}@{item.result.version}</code></span>}{item.error && <span> · {item.error}</span>}
        <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void action(item, 'refresh')} ariaLabel={`Refresh remote media for ${data.peers.find(peer => peer.id === item.peer_id)?.label || "peer"}`}>Refresh</Button>
        <Button variant="danger" disabled={busy || ['succeeded', 'failed', 'cancelled'].includes(item.status)} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void action(item, 'cancel')} ariaLabel={`Cancel remote media for ${data.peers.find(peer => peer.id === item.peer_id)?.label || "peer"}`}>Cancel</Button>
      </li>)}</ul>
    </>}
  </section>
}
