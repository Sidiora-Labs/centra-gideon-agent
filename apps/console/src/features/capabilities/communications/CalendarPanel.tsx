import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'

type Source = { id: string; name: string; kind: string; calendar_id: string; credential_ref: string; timezone: string; revision: number; sync: { state: string; coverage: string; error?: string } }
type Review = { timezone: string; coverage: string; events: { id: string; source_id: string; title: string; start: string; end: string; all_day: boolean; location: string; recurrence_unexpanded: boolean }[] }
const base = '/api/capabilities/communications/calendar'
const empty = { name: '', kind: 'ics', calendar_id: 'primary', credential_ref: '', timezone: 'UTC' }
export function CalendarPanel() {
  const [sources, setSources] = useState<Source[]>([])
  const [form, setForm] = useState(empty)
  const [editing, setEditing] = useState<{ id: string; revision: number } | null>(null)
  const [sourceId, setSourceId] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('calendar_source') || '')
  const [content, setContent] = useState('')
  const year = new Date().getUTCFullYear()
  const [windowStart, setWindowStart] = useState(`${year}-01-01`)
  const [windowEnd, setWindowEnd] = useState(`${year + 1}-01-01`)
  const [day, setDay] = useState(new Date().toISOString().slice(0, 10))
  const [timezone, setTimezone] = useState('UTC')
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const selected = sources.find(row => row.id === sourceId)
  const reload = async () => setSources((await requestJson<{ sources: Source[] }>(base + '/sources')).sources)
  const select = (id: string) => { setSourceId(id); const params = new URLSearchParams(location.hash.split('?')[1] || ''); params.set('calendar_source', id); location.hash = '#/capabilities/communications?' + params.toString() }
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  useEffect(() => { let active = true; requestJson<{ sources: Source[] }>(base + '/sources').then(data => { if (active) setSources(data.sources) }).catch(e => { if (active) setError(String(e)) }); return () => { active = false } }, [])
  return <section aria-label="Calendar daily review" className="space-y-3 rounded border p-4"><h2>Calendar daily review</h2><p>Review mirrored events by local day. Snapshot coverage is separate from live calendar access. ICS recurrence, exclusions, additions, overrides and cancellations are expanded within the selected bounded window.</p>{error && <p role="alert">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); void run(async () => { const data = await requestJson<{ source: Source }>(base + '/sources' + (editing ? '/' + editing.id : ''), editing ? 'PUT' : 'POST', editing ? { ...form, revision: editing.revision } : form); await reload(); select(data.source.id); setForm(empty); setEditing(null) }) }}>
      <label>Calendar name<input required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>Calendar kind<select disabled={Boolean(editing)} value={form.kind} onChange={e => setForm({ ...form, kind: e.target.value })}><option value="ics">ICS export</option><option value="google">Google Calendar</option><option value="outlook">Outlook</option></select></label>
      <label>Calendar source timezone<input required value={form.timezone} onChange={e => setForm({ ...form, timezone: e.target.value })} /></label>{form.kind !== 'ics' && <><label>Remote calendar ID<input required value={form.calendar_id} onChange={e => setForm({ ...form, calendar_id: e.target.value })} /></label><label>Calendar credential reference<input required value={form.credential_ref} onChange={e => setForm({ ...form, credential_ref: e.target.value })} /></label></>}<button disabled={busy}>{editing ? 'Save calendar changes' : 'Create calendar source'}</button>{editing && <button type="button" onClick={() => { setEditing(null); setForm(empty) }}>Cancel calendar edit</button>}
    </form>
    <label>Calendar source<select value={sourceId} onChange={e => select(e.target.value)}><option value="">Select calendar</option>{sources.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}</select></label>
    {selected && <div><button disabled={busy} onClick={() => { setEditing({ id: selected.id, revision: selected.revision }); setForm({ name: selected.name, kind: selected.kind, calendar_id: selected.calendar_id, credential_ref: selected.credential_ref, timezone: selected.timezone }) }}>Edit calendar source</button><p>Calendar sync: {selected.sync.state}; {selected.sync.coverage}</p>{selected.sync.error && <p>{selected.sync.error}</p>}{selected.kind === 'ics' ? <><label>ICS recurrence window start<input type="date" value={windowStart} onChange={e => setWindowStart(e.target.value)} /></label><label>ICS recurrence window end<input type="date" value={windowEnd} onChange={e => setWindowEnd(e.target.value)} /></label><label>ICS export content<textarea value={content} onChange={e => setContent(e.target.value)} /></label><button disabled={busy || !content || !windowStart || !windowEnd} onClick={() => void run(async () => { await requestJson(`${base}/sources/${selected.id}/upload`, 'POST', { content, revision: selected.revision, window_start: windowStart + 'T00:00:00+00:00', window_end: windowEnd + 'T00:00:00+00:00' }); setContent(''); await reload() })}>Import calendar export</button></> : <button disabled={busy} onClick={() => void run(async () => { try { const start = new Date(day + 'T00:00:00Z'); const end = new Date(start.getTime() + 7 * 86400000); await requestJson(`${base}/sources/${selected.id}/sync`, 'POST', { start: start.toISOString(), end: end.toISOString() }) } finally { await reload() } })}>Sync calendar week</button>}</div>}
    <label>Review date<input type="date" value={day} onChange={e => setDay(e.target.value)} /></label><label>Review timezone<input value={timezone} onChange={e => setTimezone(e.target.value)} /></label><button disabled={busy} onClick={() => void run(async () => setReview(await requestJson<Review>(`${base}/daily?date=${encodeURIComponent(day)}&timezone=${encodeURIComponent(timezone)}`)))}>Review calendar day</button>
    {review && <div><p>Review coverage: {review.coverage}</p>{review.events.length === 0 && <p>No mirrored events on this date.</p>}{review.events.map(row => <article key={row.source_id + row.id}><h3>{row.title || '(Untitled event)'}</h3><p>{row.all_day ? 'All day' : new Date(row.start).toLocaleString(undefined, { timeZone: review.timezone })} — {row.all_day ? row.end : new Date(row.end).toLocaleString(undefined, { timeZone: review.timezone })}</p><p>{row.location}</p></article>)}</div>}
  </section>
}
