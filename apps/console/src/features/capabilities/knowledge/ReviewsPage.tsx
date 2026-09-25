import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms'

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
  return <main className="h-full"><ListScaffold title="Obligation reviews" bodyClassName="mx-auto px-l py-l"><div className="flex flex-col gap-xl"><p data-type="body-m" className="max-w-[42rem] text-on-surface-var">Review actual tasks, contact history, notes and unreviewed captures. Saved reviews retain source citations and your reflection.</p>
    {error && <p role="alert" className="border-l-2 border-danger/40 pl-s text-danger">{error}</p>}
    <section className="grid gap-m rounded-lg bg-surface-container p-l sm:grid-cols-3"><Field label="Period"><Select ariaLabel="Period" value={period} surface="high" disabled={busy} onChange={value => { setPeriod(value); changed() }} options={[{value:'daily',label:'Daily'},{value:'weekly',label:'Weekly'}]} /></Field>
    <label data-type="label-s" className="grid gap-xs text-on-surface-var">Review date<input className="min-h-10 rounded-md border border-outline-variant/30 bg-surface-high px-m" aria-label="Review date" type="date" value={date} disabled={busy} onChange={e => { setDate(e.target.value); changed() }} /></label>
    <Field label="Timezone"><TextInput ariaLabel="Timezone" value={zone} surface="high" disabled={busy} onChange={value => { setZone(value); changed() }} /></Field>
    <Button disabled={busy || !date || !zone} onClick={() => void preview()}>Preview review</Button>
    </section>{snapshot && <section aria-label="Review preview" className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Review preview</h2><p>{snapshot.window_start} – {snapshot.window_end}</p>{snapshot.limitations.map(text => <p key={text}>{text}</p>)}{snapshot.truncated.length > 0 && <p role="status">Source scan limit reached: {snapshot.truncated.join(', ')}</p>}{Object.entries(snapshot.sections).map(([section, rows]) => <section className="border-t border-outline-variant/20 pt-m" key={section}><h2 data-type="label-l">{section.replaceAll('_', ' ')}</h2>{rows.length === 0 && <p>No matching records</p>}{rows.map(row => <article className="py-s" key={row.source_id}><a className="text-primary underline" href={row.source_link}>{row.title}</a><p className="text-on-surface-var">{row.detail}</p></article>)}</section>)}<Field label="Reflection"><TextArea ariaLabel="Reflection" rows={6} surface="high" value={reflection} disabled={busy} onChange={value => { setReflection(value); setSaved(null); requestId.current = crypto.randomUUID() }} /></Field><Button className="w-fit" disabled={busy || !!saved} onClick={() => void save()}>Save review</Button></section>}
    {saved && <p role="status"><a href={saved.source_link}>Open saved review</a></p>}
    <section aria-label="Review schedules" className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Recurring reviews</h2><p className="text-on-surface-var">Scheduled reviews cover the previous local day or seven days ending then. They save source snapshots without a reflection or generated summary.</p><div className="flex flex-wrap gap-s">{schedules.map(item => <Button variant={selected?.id===item.id?'primary':'ghost'} key={item.id} disabled={busy} onClick={() => choose(item)}>{item.period} {item.time} {item.timezone} · {item.enabled ? 'enabled' : 'disabled'}</Button>)}<Button variant="secondary" disabled={busy} onClick={() => choose(null)}>New schedule</Button></div><label data-type="label-s" className="grid gap-xs text-on-surface-var">Schedule time<input className="min-h-10 rounded-md border border-outline-variant/30 bg-surface-high px-m" aria-label="Schedule time" type="time" value={clock} disabled={busy} onChange={e => { setClock(e.target.value); scheduleId.current = crypto.randomUUID() }} /></label>{period === 'weekly' && <Field label="Weekday"><Select ariaLabel="Weekday" value={String(weekday)} surface="high" disabled={busy} onChange={value => { setWeekday(Number(value)); scheduleId.current = crypto.randomUUID() }} options={['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((day,index)=>({value:String(index),label:day}))} /></Field>}<label className="inline-flex items-center gap-s"><input className="size-4 accent-primary" aria-label="Schedule enabled" type="checkbox" checked={enabled} disabled={busy} onChange={e => { setEnabled(e.target.checked); scheduleId.current = crypto.randomUUID() }} />Enabled</label><Button className="w-fit" disabled={busy || !zone || !clock} onClick={() => void saveSchedule()}>Save schedule</Button>{selected && <p role="status">{selected.enabled ? `Next fire: ${selected.next_fire_at}` : 'Schedule disabled'}</p>}</section>
    <section aria-label="Saved reviews" className="rounded-lg bg-surface-container p-l"><h2 data-type="title-m" className="mb-m">Saved reviews</h2>{receipts.map(row => <p className="border-b border-outline-variant/20 py-s last:border-0" key={row.request_id}><a className="text-primary underline" href={row.source_link}>{row.period} {row.date}{row.scheduled ? ' · scheduled' : ''}</a></p>)}<div className="flex gap-s pt-m"><Button variant="secondary" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous reviews</Button><Button variant="secondary" disabled={busy || next === null} onClick={() => setOffset(next!)}>Next reviews</Button></div></section>
  </div></ListScaffold></main>
}
