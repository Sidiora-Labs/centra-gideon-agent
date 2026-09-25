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
  return <section aria-label="Remote media execution" className="space-y-m">
    <h2>Remote media execution</h2><p>Send bounded image jobs to a direct peer. Queue admission is recorded here; model execution and artifacts remain attributable to that peer.</p>
    {error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading remote media…</p>}
    {data && <>
      <label>Execution peer<select aria-label="Execution peer" value={peerId} onChange={event => setPeerId(event.target.value)}><option value="">Select a peer</option>{data.peers.map(peer => <option key={peer.id} value={peer.id}>{peer.label}</option>)}</select></label>
      <label>Request ID<input aria-label="Request ID" value={requestId} maxLength={40} onChange={event => setRequestId(event.target.value)} /></label>
      <label>Image prompt<textarea aria-label="Image prompt" value={prompt} maxLength={4000} onChange={event => setPrompt(event.target.value)} /></label>
      <Button disabled={busy || !peerId || !requestId || !prompt.trim()} onClick={() => void dispatch()}>Dispatch remote job</Button>
      {!data.items.length && <p>No remote executions recorded.</p>}
      <ul aria-label="Remote executions">{data.items.map(item => <li key={item.id}>
        <strong>{item.request_id}</strong> · {item.status} · peer <code>{item.peer_id}</code> · job <code>{item.remote_job_id}</code>
        {item.result?.artifact_id && <span> · artifact <code>{item.result.artifact_id}@{item.result.version}</code></span>}{item.error && <span> · {item.error}</span>}
        <Button disabled={busy} onClick={() => void action(item, 'refresh')}>Refresh</Button>
        <Button variant="danger" disabled={busy || ['succeeded', 'failed', 'cancelled'].includes(item.status)} onClick={() => void action(item, 'cancel')}>Cancel</Button>
      </li>)}</ul>
    </>}
  </section>
}
