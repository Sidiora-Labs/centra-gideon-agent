import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
type Status = { state: string; reason: string; app_id: string; version: Record<string, unknown> | null; engine_url: string | null }
export default function WorldEngine({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const refresh = async () => setStatus(await requestJson<Status>(baseUrl + '/world-engine'))
  useEffect(() => { void refresh().catch(e => setError(String(e))) }, [baseUrl])
  async function control(operation: string) {
    setBusy(true); setError('')
    try { setStatus(await requestJson<Status>(baseUrl + '/world-engine/' + operation, 'POST', {})); if (operation === 'stop') setOpen(false) }
    catch (e) { setError(String(e)) } finally { setBusy(false) }
  }
  return <section aria-label="World engine" className="space-y-3 rounded-lg border p-4">
    <h2>World engine</h2><p>Open your persistent world through the separately installed engine.</p>
    {error && <p role="alert">{error}</p>}
    {!status ? <p>Loading world engine…</p> : <>
      <p role="status">{status.state}: {status.reason}</p>
      <div className="flex flex-wrap gap-2"><Button disabled={busy || status.state === 'unavailable' || status.state === 'running'} onClick={() => void control('start')}>Start world engine</Button><Button disabled={busy || !['running', 'starting'].includes(status.state)} onClick={() => void control('stop')}>Stop world engine</Button><Button disabled={busy} onClick={() => void refresh().catch(e => setError(String(e)))}>Refresh world engine</Button><Button disabled={!status.engine_url} onClick={() => setOpen(value => !value)}>{open ? 'Close world view' : 'Open world'}</Button></div>
      {open && status.engine_url && <iframe title="Persistent world" src={status.engine_url} className="h-[70vh] w-full rounded border" allow="fullscreen; microphone; camera" />}
    </>}
    <p><a href="https://github.com/atomantic/eidoverse-worlds" target="_blank" rel="noreferrer">Eidoverse Worlds</a> runs as a separate AGPL-3.0 application.</p>
  </section>
}
