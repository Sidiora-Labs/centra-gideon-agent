import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Snapshot = { preview_id: string; sections: Record<string, { source_id: string; title: string; detail: string; source_link: string }[]>; limitations: string[]; truncated: string[]; window_start: string; window_end: string }
type Receipt = { request_id: string; source_link: string; date: string; period: string; scheduled: boolean }
type Schedule = { id: string; revision: number; period: string; timezone: string; time: string; weekday: number; enabled: boolean; next_fire_at: string }
const root = '/api/capabilities/knowledge/reviews'

export default function ReviewsPage() {
  const [period, setPeriod] = useState('daily')
  const [date, setDate] = useState(new Date().toISOString().slice(0, 10))
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [reflection, setReflection] = useState('')
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [offset, setOffset] = useState(0)
  const [next, setNext] = useState<number | null>(null)
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [selected, setSelected] = useState<Schedule | null>(null)
  const [clock, setClock] = useState('09:00')
  const [weekday, setWeekday] = useState(0)
  const [enabled, setEnabled] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState<Receipt | null>(null)
  const requestId = useRef(crypto.randomUUID())
  const scheduleId = useRef(crypto.randomUUID())
  async function loadHistory(page = offset) { const data = await requestJson<{ items: Receipt[]; next_offset: number | null }>(`${root}?offset=${page}`); setReceipts(data.items); setNext(data.next_offset) }
  useEffect(() => { void loadHistory().catch(e => setError(e.message)); requestJson<{ items: Schedule[] }>(root + '/schedules').then(data => setSchedules(data.items)).catch(e => setError(e.message)) }, [offset])
  function changed() { setSnapshot(null); setSaved(null); requestId.current = crypto.randomUUID(); scheduleId.current = crypto.randomUUID() }
  async function act(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  async function preview() { await act(async () => { setSnapshot(await requestJson<Snapshot>(`${root}/preview?${new URLSearchParams({ period, date, timezone: zone })}`)); setSaved(null); requestId.current = crypto.randomUUID() }) }
  async function save() { if (!snapshot) return; await act(async () => { const result = await requestJson<Receipt>(root, 'POST', { request_id: requestId.current, period, date, timezone: zone, preview_id: snapshot.preview_id, reflection }); setSaved(result); await loadHistory(0); setOffset(0) }) }
  function choose(schedule: Schedule | null) { setSelected(schedule); setClock(schedule?.time || '09:00'); setWeekday(schedule?.weekday || 0); setEnabled(schedule?.enabled ?? true); if (schedule) { setPeriod(schedule.period); setZone(schedule.timezone) } changed() }
  async function saveSchedule() { await act(async () => { const result = await requestJson<Schedule>(root + '/schedules', 'POST', { request_id: scheduleId.current, period, timezone: zone, time: clock, weekday, enabled, ...(selected ? { id: selected.id, revision: selected.revision } : {}) }); setSelected(result); setSchedules(current => [result, ...current.filter(item => item.id !== result.id)]); scheduleId.current = crypto.randomUUID() }) }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Obligation reviews</h1><p>Review actual tasks, contact history, notes and unreviewed captures. Saved reviews retain source citations and your reflection.</p>
    {error && <p role="alert">{error}</p>}
    <label className="block">Period<select aria-label="Period" value={period} disabled={busy} onChange={e => { setPeriod(e.target.value); changed() }}><option value="daily">Daily</option><option value="weekly">Weekly</option></select></label>
    <label className="block">Review date<input aria-label="Review date" type="date" value={date} disabled={busy} onChange={e => { setDate(e.target.value); changed() }} /></label>
    <label className="block">Timezone<input aria-label="Timezone" value={zone} disabled={busy} onChange={e => { setZone(e.target.value); changed() }} /></label>
    <Button disabled={busy || !date || !zone} onClick={() => void preview()}>Preview review</Button>
    {snapshot && <section aria-label="Review preview"><p>{snapshot.window_start} – {snapshot.window_end}</p>{snapshot.limitations.map(text => <p key={text}>{text}</p>)}{snapshot.truncated.length > 0 && <p role="status">Source scan limit reached: {snapshot.truncated.join(', ')}</p>}{Object.entries(snapshot.sections).map(([section, rows]) => <section key={section}><h2>{section.replaceAll('_', ' ')}</h2>{rows.length === 0 && <p>No matching records</p>}{rows.map(row => <article key={row.source_id}><a href={row.source_link}>{row.title}</a><p>{row.detail}</p></article>)}</section>)}<label className="block">Reflection<textarea aria-label="Reflection" value={reflection} disabled={busy} onChange={e => { setReflection(e.target.value); setSaved(null); requestId.current = crypto.randomUUID() }} /></label><Button disabled={busy || !!saved} onClick={() => void save()}>Save review</Button></section>}
    {saved && <p role="status"><a href={saved.source_link}>Open saved review</a></p>}
    <section aria-label="Review schedules"><h2>Recurring reviews</h2><p>Scheduled reviews cover the previous local day or seven days ending then. They save source snapshots without a reflection or generated summary.</p>{schedules.map(item => <Button key={item.id} disabled={busy} onClick={() => choose(item)}>{item.period} {item.time} {item.timezone} · {item.enabled ? 'enabled' : 'disabled'}</Button>)}<Button disabled={busy} onClick={() => choose(null)}>New schedule</Button><label className="block">Schedule time<input aria-label="Schedule time" type="time" value={clock} disabled={busy} onChange={e => { setClock(e.target.value); scheduleId.current = crypto.randomUUID() }} /></label>{period === 'weekly' && <label className="block">Weekday<select aria-label="Weekday" value={weekday} disabled={busy} onChange={e => { setWeekday(Number(e.target.value)); scheduleId.current = crypto.randomUUID() }}>{['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label>}<label><input aria-label="Schedule enabled" type="checkbox" checked={enabled} disabled={busy} onChange={e => { setEnabled(e.target.checked); scheduleId.current = crypto.randomUUID() }} />Enabled</label><Button disabled={busy || !zone || !clock} onClick={() => void saveSchedule()}>Save schedule</Button>{selected && <p role="status">{selected.enabled ? `Next fire: ${selected.next_fire_at}` : 'Schedule disabled'}</p>}</section>
    <section aria-label="Saved reviews"><h2>Saved reviews</h2>{receipts.map(row => <p key={row.request_id}><a href={row.source_link}>{row.period} {row.date}{row.scheduled ? ' · scheduled' : ''}</a></p>)}<Button disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous reviews</Button><Button disabled={busy || next === null} onClick={() => setOffset(next!)}>Next reviews</Button></section>
  </main>
}
