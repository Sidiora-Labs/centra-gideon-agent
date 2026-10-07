import { TextInput, Field, Select, TextArea } from '../../../shared/ui/forms'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { BUSY_REASON } from '../../../shared/ui/unavailable'

type Source = { id: string; name: string; kind: string; calendar_id: string; credential_ref: string; timezone: string; revision: number; sync: { state: string; coverage: string; error?: string } }
type Review = { timezone: string; coverage: string; events: { id: string; source_id: string; title: string; start: string; end: string; all_day: boolean; location: string; recurrence_unexpanded: boolean }[] }
const base = '/api/capabilities/communications/calendar'
const empty = { name: '', kind: 'ics', calendar_id: 'primary', credential_ref: '', timezone: 'UTC' }
export function CalendarPanel() {
  const calendarKindReason = 'Calendar kind cannot change while editing an existing source.'
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
  return <section aria-label="Calendar daily review" className="space-y-l"><h2 data-type="title-m">Calendar daily review</h2><p data-type="body-s" className="text-on-surface-low">Review mirrored events by local day. Snapshot coverage is separate from live calendar access. ICS recurrence, exclusions, additions, overrides and cancellations are expanded within the selected bounded window.</p>{error && <p role="alert" className="rounded-lg bg-error-container p-m text-on-error-container">{error}</p>}
    <form className="space-y-m rounded-lg bg-surface-container px-l py-l" onSubmit={e => { e.preventDefault(); void run(async () => { const data = await requestJson<{ source: Source }>(base + '/sources' + (editing ? '/' + editing.id : ''), editing ? 'PUT' : 'POST', editing ? { ...form, revision: editing.revision } : form); await reload(); select(data.source.id); setForm(empty); setEditing(null) }) }}>
      <Field label={"Calendar name"}><TextInput required value={form.name} onChange={nextValue => setForm({ ...form, name: nextValue })} /></Field><Field label="Calendar kind"><Select value={form.kind} readOnly={Boolean(editing)} readOnlyReason={calendarKindReason} onChange={nextValue => setForm({ ...form, kind: nextValue })} options={[{ value: 'ics', label: "ICS export" }, { value: 'google', label: "Google Calendar" }, { value: 'outlook', label: "Outlook" }]} /></Field>
      <Field label={"Calendar source timezone"}><TextInput required value={form.timezone} onChange={nextValue => setForm({ ...form, timezone: nextValue })} /></Field>{form.kind !== 'ics' && <><Field label={"Remote calendar ID"}><TextInput required value={form.calendar_id} onChange={nextValue => setForm({ ...form, calendar_id: nextValue })} /></Field><Field label={"Calendar credential reference"}><TextInput required value={form.credential_ref} onChange={nextValue => setForm({ ...form, credential_ref: nextValue })} /></Field></>}<Button type="submit" disabled={busy} disabledReason={busy ? BUSY_REASON : undefined}>{editing ? 'Save calendar changes' : 'Create calendar source'}</Button>{editing && <Button type="button" onClick={() => { setEditing(null); setForm(empty) }}>Cancel calendar edit</Button>}
    </form>
    <Field label={"Calendar source"}><Select value={sourceId} onChange={nextValue => select(nextValue)} options={[{ value: "", label: "Select calendar" }, ...(sources.map(row => ({ value: row.id, label: String(row.name) })))]} /></Field>
    {selected && <div><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => { setEditing({ id: selected.id, revision: selected.revision }); setForm({ name: selected.name, kind: selected.kind, calendar_id: selected.calendar_id, credential_ref: selected.credential_ref, timezone: selected.timezone }) }}>Edit calendar source</Button><p>Calendar sync: {selected.sync.state}; {selected.sync.coverage}</p>{selected.sync.error && <p>{selected.sync.error}</p>}{selected.kind === 'ics' ? <><Field label={"ICS recurrence window start"}><TextInput type="date" value={windowStart} onChange={nextValue => setWindowStart(nextValue)} /></Field><Field label={"ICS recurrence window end"}><TextInput type="date" value={windowEnd} onChange={nextValue => setWindowEnd(nextValue)} /></Field><Field label={"ICS export content"}><TextArea value={content} onChange={nextValue => setContent(nextValue)} /></Field><Button disabled={busy || !content || !windowStart || !windowEnd} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => { await requestJson(`${base}/sources/${selected.id}/upload`, 'POST', { content, revision: selected.revision, window_start: windowStart + 'T00:00:00+00:00', window_end: windowEnd + 'T00:00:00+00:00' }); setContent(''); await reload() })}>Import calendar export</Button></> : <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => { try { const start = new Date(day + 'T00:00:00Z'); const end = new Date(start.getTime() + 7 * 86400000); await requestJson(`${base}/sources/${selected.id}/sync`, 'POST', { start: start.toISOString(), end: end.toISOString() }) } finally { await reload() } })}>Sync calendar week</Button>}</div>}
    <Field label={"Review date"}><TextInput type="date" value={day} onChange={nextValue => setDay(nextValue)} /></Field><Field label={"Review timezone"}><TextInput value={timezone} onChange={nextValue => setTimezone(nextValue)} /></Field><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => setReview(await requestJson<Review>(`${base}/daily?date=${encodeURIComponent(day)}&timezone=${encodeURIComponent(timezone)}`)))}>Review calendar day</Button>
    {review && <div><p>Review coverage: {review.coverage}</p>{review.events.length === 0 && <p>No mirrored events on this date.</p>}{review.events.map(row => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={row.source_id + row.id}><h3 data-type="title-s">{row.title || '(Untitled event)'}</h3><p>{row.all_day ? 'All day' : new Date(row.start).toLocaleString(undefined, { timeZone: review.timezone })} — {row.all_day ? row.end : new Date(row.end).toLocaleString(undefined, { timeZone: review.timezone })}</p><p>{row.location}</p></article>)}</div>}
  </section>
}
