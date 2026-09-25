import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
type Event = { id: string; kind: string; occurred_at: string; summary: string; person_id: string | null; source: string; href: string; qualification: string }
type Feed = { events: Event[]; next_cursor: string | null; coverage: string; matched_records: number; limits: string[]; timezone: string }
const base = '/api/capabilities/communications'
export function TimelinePanel() {
  const params = new URLSearchParams(location.hash.split('?')[1] || '')
  const [day, setDay] = useState(params.get('activity_date') || new Date().toISOString().slice(0, 10))
  const [timezone, setTimezone] = useState(params.get('activity_timezone') || 'UTC')
  const [person, setPerson] = useState(params.get('activity_person') || '')
  const [kind, setKind] = useState(params.get('activity_kind') || '')
  const [people, setPeople] = useState<{ id: string; name: string }[]>([])
  const [feed, setFeed] = useState<Feed | null>(null)
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  useEffect(() => { let active = true; requestJson<{ people: { id: string; name: string }[] }>(base + '/people').then(result => { if (active) setPeople(result.people) }).catch(e => { if (active) setError(String(e)) }); return () => { active = false } }, [])
  const review = async () => { const search = new URLSearchParams({ date: day, timezone, limit: '25', ...(person ? { person_id: person } : {}), ...(kind ? { kind } : {}) }).toString(); const result = await requestJson<Feed>(base + '/activity-timeline?' + search); setFeed(result); setQuery(search); const selected = new URLSearchParams(location.hash.split('?')[1] || ''); selected.set('activity_date', day); selected.set('activity_timezone', timezone); selected.set('activity_person', person); selected.set('activity_kind', kind); location.hash = '#/capabilities/communications?' + selected.toString() }
  return <section aria-label="Recorded activity timeline" className="space-y-3 rounded border p-4"><h2>Recorded activity timeline</h2><p>Review communications records and scheduled calendar entries. Scheduled events do not prove attendance; this is not a complete record of human activity.</p>{error && <p role="alert">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); void run(review) }}><label>Activity date<input type="date" required value={day} onChange={e => setDay(e.target.value)} /></label><label>Activity timezone<input required value={timezone} onChange={e => setTimezone(e.target.value)} /></label><label>Activity person<select value={person} onChange={e => setPerson(e.target.value)}><option value="">All people</option>{people.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}</select></label><label>Activity kind<select value={kind} onChange={e => setKind(e.target.value)}><option value="">All records</option>{['touchpoint', 'message', 'calendar', 'social', 'assignment'].map(value => <option key={value}>{value}</option>)}</select></label><button disabled={busy}>Review recorded activity</button></form>
    {feed && <div><p>Timeline coverage: {feed.coverage}; matched records: {feed.matched_records}</p>{feed.events.length === 0 && <p>No recorded activity matches this review.</p>}{feed.events.map(event => <article key={event.id}><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString(undefined, { timeZone: feed.timezone })}</time><h3>{event.summary || '(No summary)'}</h3><p>{event.kind} · {event.source} · {event.qualification}</p><a href={event.href}>Open activity source</a></article>)}{feed.next_cursor && <button disabled={busy} onClick={() => void run(async () => { const next = await requestJson<Feed>(base + '/activity-timeline?' + query + '&cursor=' + encodeURIComponent(feed.next_cursor!)); setFeed({ ...next, events: [...feed.events, ...next.events.filter(event => !feed.events.some(existing => existing.id === event.id))] }) })}>Load earlier activity</button>}{feed.limits.map(limit => <p key={limit}>{limit}</p>)}</div>}
  </section>
}
