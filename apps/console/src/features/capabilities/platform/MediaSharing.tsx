import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Share = { id: string; peer_id: string; artifact_id: string; artifact_version: number; sha256: string; bytes: number; status: string; revision: number; remote_artifact_id: string; error: string }
type Receipt = { sender: string; share_id: string; artifact_id: string; sha256: string; status: string }
type Snapshot = { scope: string; max_bytes: number; outbound: Share[]; inbound: Receipt[] }

export default function MediaSharing({ baseUrl = '' }: { baseUrl?: string }) {
  const url = `${baseUrl}/api/capabilities/platform/media-shares`
  const [data, setData] = useState<Snapshot>()
  const [peer, setPeer] = useState(''), [artifact, setArtifact] = useState(''), [version, setVersion] = useState(1)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const load = async () => { try { setData(await readJson<Snapshot>(await gatewayRequest(url))); setError('') } catch (cause) { setError(String(cause)) } }
  useEffect(() => { void load() }, [baseUrl])
  async function run(work: () => Promise<unknown>) { setBusy(true); setError(''); try { await work(); await load() } catch (cause) { setError(String(cause)) } finally { setBusy(false) } }
  return <section aria-label="Selective media sharing" className="grid gap-l">
    <h2 data-type="title-m">Selective media sharing</h2>
    <p>Send one explicit immutable image or video version to one enabled direct peer. The peer must opt in to <code>media.assets</code>; Gideon sends no library index, folders, annotations, or other datastore records.</p>
    {error && <p role="alert">{error}</p>}{!data && !error && <p role="status">Loading media shares…</p>}
    {data && <>
      <p>Maximum transfer: {Math.floor(data.max_bytes / 1048576)} MiB. Signed transport verifies the exact byte count and SHA-256.</p>
      <div className="grid gap-m sm:grid-cols-2"><label className="grid gap-xs text-sm">Peer ID<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" value={peer} onChange={event => setPeer(event.target.value)} /></label><label className="grid gap-xs text-sm">Canonical artifact ID<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" value={artifact} onChange={event => setArtifact(event.target.value)} /></label><label className="grid gap-xs text-sm">Exact artifact version<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" type="number" min="1" value={version} onChange={event => setVersion(Number(event.target.value))} /></label></div>
      <Button disabled={busy || !peer || !artifact || version < 1} onClick={() => void run(async () => readJson(await gatewayRequest(url, 'POST', { request_id: crypto.randomUUID(), peer_id: peer, artifact_id: artifact, artifact_version: version })))}>Share selected version</Button>
      <Button disabled={busy} onClick={() => void load()}>Refresh receipts</Button>
      <h3 data-type="headline-s">Sent shares</h3>{!data.outbound.length && <p>No media has been shared.</p>}
      <ul>{data.outbound.map(row => <li key={row.id}><code>{row.artifact_id}@{row.artifact_version}</code> → <code>{row.peer_id}</code> · {row.status} · {row.bytes} bytes · SHA-256 {row.sha256}{row.error && <> · {row.error}</>} {row.status === 'active' && <Button variant="danger" disabled={busy} onClick={() => void run(async () => readJson(await gatewayRequest(`${url}/${row.id}/revoke`, 'POST', { revision: row.revision })))}>Revoke remote copy</Button>}</li>)}</ul>
      <h3 data-type="headline-s">Received shares</h3>{!data.inbound.length && <p>No peer media received.</p>}
      <ul>{data.inbound.map(row => <li key={row.sender + row.share_id}><code>{row.artifact_id}</code> from <code>{row.sender}</code> · {row.status} · SHA-256 {row.sha256}</li>)}</ul>
    </>}
  </section>
}
