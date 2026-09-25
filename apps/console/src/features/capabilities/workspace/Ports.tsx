import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

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
  return <section className="mx-auto w-full space-y-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}><header><h2 data-type="title-m">Port reservations</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-low">A reservation holds a loopback TCP port. Release it before starting an application that needs to bind it. Availability is a current observation.</p></header>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <Surface className="p-l"><form className="grid gap-m sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(reserve) }}>
      <Field label="Reservation project ID"><TextInput id="port-project" required value={project} onChange={setProject}/></Field>
      <Field label="Port (optional)"><TextInput id="port-number" type="number" min={1024} max={65535} value={port} onChange={setPort}/></Field>
      <Button type="submit" loading={busy}>Reserve port</Button>
    </form></Surface>
    {!loaded && !error && <p role="status">Loading reservations…</p>}
    {loaded && rows.length === 0 && <p>No port reservations.</p>}
    <Button loading={busy} variant="secondary" onClick={() => void act(async () => setInventory(await requestJson<Inventory>(`${base}/inventory`)))}>Inspect port availability</Button>
    {inventory && <Surface className="max-h-64 overflow-auto p-l"><p data-type="label-m">{inventory.host} · {inventory.transport}</p><ul className="mt-m divide-y divide-outline-variant/20">{inventory.ports.map(item => <li key={item.port} className="flex justify-between gap-m py-s"><span className="font-mono">{item.port}</span><span className="text-on-surface-low">{item.reservation_id ? 'Reserved here' : item.available ? 'Available' : 'Unavailable'}</span></li>)}</ul></Surface>}
    <ul className="space-y-s">{rows.map(item => <li key={item.id}><Button className="w-full justify-start" variant={selected === item.id ? 'tonal' : 'secondary'} disabled={busy} onClick={() => choose(item.id)}>{item.project_id} · {item.port} · {item.status}</Button></li>)}</ul>
    {selected && !row && loaded && <p>Reservation not found in this page.</p>}
    {row && <Surface className="space-y-m p-l"><h3 data-type="title-m">{row.project_id}</h3><p aria-live="polite">Port {row.port} · {row.status}</p><p data-type="body-s" className="text-on-surface-low">{row.created_at}</p>
      <Button variant="danger" loading={busy} disabled={row.status !== 'held'} onClick={() => void act(async () => { const released = await requestJson<Reservation>(`${base}/${row.id}/release`, 'POST', { revision: row.revision }); setRows(old => old.map(item => item.id === row.id ? released : item)); setInventory(null) })}>Release port</Button>
    </Surface>}
  </section>
}
