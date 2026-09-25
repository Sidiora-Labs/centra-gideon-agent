import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

type Destination = { id: string; label: string; category: string }
type Guest = { visit_id: string; peer_id: string; world: string; guest_name: string; state: string; expires_at: number; revision: number }
type Departure = { peer_id: string; label: string; visit_id: string; url: string; expires_at: number }
const identifier = /^[A-Za-z0-9_-]{1,64}$/
const requestId = () => crypto.randomUUID().replaceAll('-', '')

export default function WorldTravel({ baseUrl, world, open }: { baseUrl: string; world: string; open: boolean }) {
  const ticket = new URLSearchParams(location.hash.split('?')[1] || '').get('guest') || ''
  const [destinations, setDestinations] = useState<Destination[]>([])
  const [guest, setGuest] = useState<Guest | null>(null)
  const [departure, setDeparture] = useState<Departure | null>(null)
  const [name, setName] = useState('Visitor')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [checked, setChecked] = useState(false)
  useEffect(() => {
    if (!ticket) return
    let active = true
    setBusy(true); setError('')
    const load = requestJson<Guest>(`${baseUrl}/world-travel/guest/${encodeURIComponent(ticket)}`).then(value => { if (active) setGuest(value) })
    load.catch(cause => { if (active) setError(String(cause)) }).finally(() => { if (active) setBusy(false) })
    return () => { active = false }
  }, [baseUrl, ticket])
  async function loadDestinations() {
    setBusy(true); setError('')
    try { setDestinations((await requestJson<{ destinations: Destination[] }>(`${baseUrl}/world-travel/destinations`)).destinations); setChecked(true) }
    catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function depart(peerId: string) {
    setBusy(true); setError(''); setDeparture(null)
    try {
      setDeparture(await requestJson<Departure>(`${baseUrl}/world-travel/depart`, 'POST', { peer_id: peerId, world, guest_name: name, request_id: requestId() }))
    } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  async function leave() {
    if (!guest) return
    setBusy(true); setError('')
    try {
      await requestJson(`${baseUrl}/world-travel/guest/${encodeURIComponent(ticket)}/leave`, 'POST', { revision: guest.revision, request_id: requestId() })
      setGuest(null); location.hash = '/capabilities/experience'
    } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  if (ticket) return <section aria-label="Guest world travel" className="space-y-m">
    <h3 data-type="title-m">Guest world visit</h3>{busy && <p role="status">Opening scoped guest invitation…</p>}{error && <p role="alert">{error}</p>}
    {guest && <><p>{guest.guest_name} is visiting {guest.world}. This invitation grants only this world visit.</p>
      <iframe title="Scoped guest world" src={`${baseUrl}/world-travel/guest/${encodeURIComponent(ticket)}/host/?world=${encodeURIComponent(guest.world)}&guest=1&name=${encodeURIComponent(guest.guest_name)}`} className="h-[60vh] w-full" allow="fullscreen" />
      <Button disabled={busy} onClick={() => void leave()}>Leave guest world</Button></>}
  </section>
  return <section aria-label="World travel" className="space-y-m">
    <h3 data-type="title-m">World travel</h3><p>Travel only to enabled canonical peers that explicitly allow scoped world guests.</p>
    {busy && <p role="status">Checking world destinations…</p>}{error && <p role="alert">{error}</p>}
    <Button disabled={busy} onClick={() => void loadDestinations()}>Check travel destinations</Button>
    {checked && !busy && destinations.length === 0 && !error && <p>No peers currently allow world guest travel.</p>}
    <div className="max-w-[28rem]"><Field label="Guest name"><TextInput maxLength={64} value={name} onChange={setName} /></Field></div>
    <ul className="grid gap-m md:grid-cols-2">{destinations.map(destination => <li key={destination.id}><Surface className="flex items-center justify-between gap-m p-m"><div><strong>{destination.label}</strong><p className="text-on-surface-variant">{destination.category}</p></div><Button disabled={busy || !open || !identifier.test(world) || !name.trim()} onClick={() => void depart(destination.id)}>Visit {destination.label}</Button></Surface></li>)}</ul>
    {!open && destinations.length > 0 && <p>Open the local world before departing.</p>}
    {departure && <p role="status">Admission ready. <a href={departure.url}>Enter {departure.label}</a></p>}
  </section>
}
