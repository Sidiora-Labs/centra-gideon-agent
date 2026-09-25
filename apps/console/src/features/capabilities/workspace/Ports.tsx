import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Reservation = { id: string; project_id: string; port: number; status: string; revision: number; created_at: string; released_at: string | null }
type Inventory = { host: string; transport: string; ports: { port: number; available: boolean; reservation_id: string | null }[] }
const base = '/api/capabilities/workspace/ports'
export default function Ports() {
  const [rows, setRows] = useState<Reservation[]>([])
  const [inventory, setInventory] = useState<Inventory | null>(null)
  const [project, setProject] = useState('')
  const [port, setPort] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [retry, setRetry] = useState<{ body: string; id: string } | null>(null)
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('reservation') || '')
  useEffect(() => {
    let active = true
    requestJson<Reservation[]>(base).then(r => { if (active) { setRows(r); setLoaded(true) } }).catch(e => { if (active) setError(String(e)) })
    const changed = () => setSelected(new URLSearchParams(location.hash.split('?')[1] || '').get('reservation') || '')
    addEventListener('hashchange', changed)
    return () => { active = false; removeEventListener('hashchange', changed) }
  }, [])
  async function act(work: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  function choose(id: string) {
    const query = new URLSearchParams(location.hash.split('?')[1] || '')
    query.set('reservation', id)
    location.hash = `/capabilities/workspace?${query}`
    setSelected(id)
  }
  async function reserve() {
    const payload = { project_id: project, ...(port ? { port: Number(port) } : {}) }
    const body = JSON.stringify(payload), request_id = retry?.body === body ? retry.id : crypto.randomUUID()
    setRetry({ body, id: request_id })
    const row = await requestJson<Reservation>(base, 'POST', { ...payload, request_id })
    setRows(old => [row, ...old.filter(x => x.id !== row.id)]); choose(row.id); setRetry(null); setInventory(null)
  }
  const row = rows.find(item => item.id === selected)
  return <section className="space-y-3 border-t border-outline pt-4"><h2 className="text-lg font-semibold">Port reservations</h2>
    <p>A reservation holds a loopback TCP port. Release it before starting an application that needs to bind it. Availability is a current observation.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(reserve) }}>
      <label htmlFor="port-project" className="grid gap-1">Reservation project ID<input id="port-project" required value={project} onChange={e => setProject(e.target.value)} className="min-w-0 rounded border border-outline bg-surface p-2" /></label>
      <label htmlFor="port-number" className="grid gap-1">Port (optional)<input id="port-number" type="number" min="1024" max="65535" value={port} onChange={e => setPort(e.target.value)} className="min-w-0 rounded border border-outline bg-surface p-2" /></label>
      <Button type="submit" loading={busy}>Reserve port</Button>
    </form>
    {!loaded && !error && <p role="status">Loading reservations…</p>}
    {loaded && rows.length === 0 && <p>No port reservations.</p>}
    <Button loading={busy} variant="secondary" onClick={() => void act(async () => setInventory(await requestJson<Inventory>(`${base}/inventory`)))}>Inspect port availability</Button>
    {inventory && <div className="max-h-64 overflow-auto"><p>{inventory.host} · {inventory.transport}</p><ul>{inventory.ports.map(item => <li key={item.port}>{item.port}: {item.reservation_id ? 'Reserved here' : item.available ? 'Available' : 'Unavailable'}</li>)}</ul></div>}
    <ul>{rows.map(item => <li key={item.id}><Button variant="ghost" disabled={busy} onClick={() => choose(item.id)}>{item.project_id} · {item.port} · {item.status}</Button></li>)}</ul>
    {selected && !row && loaded && <p>Reservation not found in this page.</p>}
    {row && <article className="space-y-2"><h3>{row.project_id}</h3><p aria-live="polite">Port {row.port} · {row.status}</p><p>{row.created_at}</p>
      <Button variant="danger" loading={busy} disabled={row.status !== 'held'} onClick={() => void act(async () => { const released = await requestJson<Reservation>(`${base}/${row.id}/release`, 'POST', { revision: row.revision }); setRows(old => old.map(item => item.id === row.id ? released : item)); setInventory(null) })}>Release port</Button>
    </article>}
  </section>
}
