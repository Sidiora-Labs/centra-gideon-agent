import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Foundation = { id: string; title: string; state: string; revision: number; style: Record<string, unknown> | null; provenance: { kind: string } }
type Controller = { id: string; foundation_id: string; world: string; state: string; desired_state: string; revision: number; last_receipt: { complete?: boolean } | null }
type Snapshot = { foundations: Foundation[]; controllers: Controller[] }

export default function WorldFoundations({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')
  const refresh = async () => setSnapshot(await requestJson<Snapshot>(baseUrl + '/world-foundations'))
  useEffect(() => { void refresh().catch(error => setError(String(error))) }, [baseUrl])
  async function control(row: Controller, operation: string) {
    setBusy(row.id); setError('')
    try { await requestJson(baseUrl + '/world-foundations/controllers/' + row.id + '/' + operation, 'POST', { revision: row.revision }); await refresh() }
    catch (error) { setError(String(error)) } finally { setBusy('') }
  }
  return <section aria-label="World foundations" className="space-y-3 rounded-lg border p-4">
    <h2>World foundations and controllers</h2>
    <p>Foundation style remains local. Controller status reflects operations acknowledged by the installed world engine.</p>
    {error && <p role="alert">{error}</p>}
    {!snapshot ? <p>Loading foundations…</p> : <>
      <p role="status">{snapshot.foundations.length} foundations · {snapshot.controllers.length} controllers</p>
      <ul>{snapshot.foundations.map(row => <li key={row.id}><strong>{row.title}</strong> — {row.state} · {row.provenance.kind}{row.style === null ? ' · no transferred style' : ''}</li>)}</ul>
      <ul>{snapshot.controllers.map(row => <li key={row.id}><strong>{row.world}</strong> — {row.state} (desired {row.desired_state}) {row.last_receipt?.complete && '· engine acknowledged'} <Button disabled={busy === row.id || row.state === 'retired'} onClick={() => void control(row, row.desired_state === 'armed' ? 'stop' : 'arm')}>{row.desired_state === 'armed' ? 'Stop controller' : 'Arm controller'}</Button>{row.desired_state === 'armed' && <Button disabled={busy === row.id} onClick={() => void control(row, 'restart')}>Restart controller</Button>}</li>)}</ul>
      <Button disabled={Boolean(busy)} onClick={() => void refresh().catch(error => setError(String(error)))}>Refresh lifecycle</Button>
    </>}
  </section>
}
