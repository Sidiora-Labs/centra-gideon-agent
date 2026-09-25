import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Surface } from '../../../shared/ui/Surface'
import { StatusPill } from '../../../shared/ui/StatusPill'
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
  return <Surface className="p-m"><section aria-label="World engine" className="space-y-m">
    <div className="flex flex-wrap items-center gap-s"><h2 data-type="title-m">World engine</h2>{status&&<StatusPill tone={status.state==='running'?'ok':status.state==='failed'||status.state==='unavailable'?'danger':'neutral'}>{status.state}</StatusPill>}</div><p className="text-on-surface-variant">Open your persistent world through the separately installed engine.</p>
    {error && <p role="alert">{error}</p>}
    {!status ? <p>Loading world engine…</p> : <>
      <p role="status">{status.state}: {status.reason}</p>
      <div className="flex flex-wrap gap-s"><Button disabled={busy || status.state === 'unavailable' || status.state === 'running'} onClick={() => void control('start')}>Start world engine</Button><Button disabled={busy || !['running', 'starting'].includes(status.state)} onClick={() => void control('stop')}>Stop world engine</Button><Button disabled={busy} onClick={() => void refresh().catch(e => setError(String(e)))}>Refresh world engine</Button><Button disabled={!status.engine_url} onClick={() => setOpen(value => !value)}>{open ? 'Close world view' : 'Open world'}</Button></div>
      {open && status.engine_url && <iframe title="Persistent world" src={status.engine_url} className="h-[70vh] w-full rounded-lg border border-outline-variant/30" allow="fullscreen; microphone; camera" />}
    </>}
    <p className="text-on-surface-variant">The world engine runs as a separately installed local application.</p>
  </section></Surface>
}
