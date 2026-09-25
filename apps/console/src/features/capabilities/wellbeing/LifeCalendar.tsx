import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Budget = { name: string; hours_per_week: number }
type Config = { revision: number; birth_date: string; horizon_years: number; sleep_hours: number; timezone: string; budgets: Budget[]; source: string; reminder: { enabled: boolean; time: string }; trigger_id: string }
type LifeEvent = { id: string; revision: number; date: string; title: string; notes: string; kind: string; source: string; deleted: boolean }
type Projection = { configured: boolean; horizon_date: string; total_days: number; elapsed_days: number; remaining_days: number; weeks_total: number; weeks_elapsed: number; sleep_hours_remaining: number; waking_hours_remaining: number; budgets: (Budget & { remaining_hours: number })[] }
const base = '/api/capabilities/wellbeing/life'

function ConfigEditor({ config, saved }: { config: Config | null; saved: () => void }) {
  const [birth, setBirth] = useState(config?.birth_date ?? ''), [years, setYears] = useState(String(config?.horizon_years ?? 80)), [sleep, setSleep] = useState(String(config?.sleep_hours ?? 8)), [zone, setZone] = useState(config?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone ?? 'UTC'), [source, setSource] = useState(config?.source ?? '')
  const [budgets, setBudgets] = useState<Budget[]>(config?.budgets ?? []), [enabled, setEnabled] = useState(config?.reminder.enabled ?? false), [time, setTime] = useState(config?.reminder.time ?? '18:00')
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const receipt = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const payload = { revision: config?.revision ?? 0, birth_date: birth, horizon_years: Number(years), sleep_hours: Number(sleep), timezone: zone, source, budgets, reminder: { enabled, time } }
    const fingerprint = JSON.stringify(payload)
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(base + '/config', 'PUT', { ...payload, request_id: receipt.current.id }); saved() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-m"><h2 data-type="title-m">Projection assumptions</h2><Field label="Birth date"><TextInput value={birth} onChange={setBirth} placeholder="YYYY-MM-DD" required /></Field><Field label="Declared horizon in years"><TextInput value={years} onChange={setYears} required /></Field><Field label="Assumed sleep hours per day"><TextInput value={sleep} onChange={setSleep} required /></Field><Field label="Life calendar timezone"><TextInput value={zone} onChange={setZone} required /></Field><Field label="Assumption source"><TextInput value={source} onChange={setSource} required /></Field>{budgets.map((budget, index) => <div key={index} className="grid gap-s sm:grid-cols-3"><Field label={`Activity ${index + 1}`}><TextInput value={budget.name} onChange={name => setBudgets(rows => rows.map((row, i) => i === index ? { ...row, name } : row))} /></Field><Field label={`Weekly hours ${index + 1}`}><TextInput value={String(budget.hours_per_week)} onChange={value => setBudgets(rows => rows.map((row, i) => i === index ? { ...row, hours_per_week: Number(value) } : row))} /></Field><Button variant="secondary" onClick={() => setBudgets(rows => rows.filter((_, i) => i !== index))}>Remove activity {index + 1}</Button></div>)}<Button variant="secondary" onClick={() => setBudgets(rows => [...rows, { name: '', hours_per_week: 0 }])}>Add activity budget</Button><label className="block"><input className="size-4 rounded border-outline-variant/40 text-primary focus:ring-primary" type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />Enable daily completion reminder</label><Field label="Reminder local time"><TextInput value={time} onChange={setTime} placeholder="HH:MM" /></Field><p>A local inbox reminder is suppressed when a cognitive practice session is completed that day.</p>{error && <p role="alert">{error}</p>}<Button type="submit" loading={busy}>Save projection assumptions</Button></form>
}

function EventEditor({ row, saved }: { row: LifeEvent | null; saved: () => void }) {
  const [day, setDay] = useState(row?.date ?? new Date().toISOString().slice(0, 10)), [title, setTitle] = useState(row?.title ?? ''), [notes, setNotes] = useState(row?.notes ?? ''), [kind, setKind] = useState(row?.kind ?? 'recorded'), [source, setSource] = useState(row?.source ?? ''), [deleted, setDeleted] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const receipt = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const payload = { date: day, title, notes, kind, ...(row ? { revision: row.revision, deleted } : { source }) }
    const fingerprint = JSON.stringify(payload)
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(base + '/events' + (row ? `/${row.id}` : ''), row ? 'PUT' : 'POST', { ...payload, request_id: receipt.current.id }); saved() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-m"><h2 data-type="title-m">{row ? 'Edit life event' : 'New life event'}</h2><Field label="Life event date"><TextInput value={day} onChange={setDay} placeholder="YYYY-MM-DD" required /></Field><Field label="Life event title"><TextInput value={title} onChange={setTitle} required /></Field><Field label="Life event notes"><TextInput value={notes} onChange={setNotes} /></Field><Field label="Life event source"><TextInput value={source} onChange={setSource} disabled={!!row} required /></Field><label className="block">Event kind<select className="h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Life event kind" value={kind} onChange={e => setKind(e.target.value)}><option value="recorded">Recorded</option><option value="planned">Planned</option></select></label>{row && <label><input className="size-4 rounded border-outline-variant/40 text-primary focus:ring-primary" type="checkbox" checked={deleted} onChange={e => setDeleted(e.target.checked)} />Delete life event (keep history)</label>}{error && <p role="alert">{error}</p>}<Button type="submit" loading={busy}>Save life event</Button></form>
}

export default function LifeCalendar() {
  const { query, setQuery } = useHashRoute('capabilities')
  const identity = query.event
  const [config, setConfig] = useState<Config | null>(null), [projection, setProjection] = useState<Projection | null>(null), [events, setEvents] = useState<LifeEvent[]>([]), [history, setHistory] = useState<LifeEvent[]>([])
  const [generation, setGeneration] = useState(0), [loading, setLoading] = useState(true), [error, setError] = useState(''), [reminder, setReminder] = useState(''), [busy, setBusy] = useState(false)
  useEffect(() => {
    let active = true; setLoading(true); setError('')
    Promise.all([requestJson<Config | null>(base + '/config'), requestJson<Projection>(base + '/projection'), requestJson<{ events: LifeEvent[] }>(base + '/events'), identity ? requestJson<{ history: LifeEvent[] }>(`${base}/events/${identity}/history`) : Promise.resolve({ history: [] })])
      .then(([settings, summary, rows, revisions]) => { if (active) { setConfig(settings); setProjection(summary); setEvents(rows.events); setHistory(revisions.history) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, generation])
  async function check() {
    if (busy) return
    setBusy(true); setError('')
    try { const result = await requestJson<{ status: string }>(base + '/reminder/check', 'POST', {}); setReminder(result.status) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  const selected = events.find(row => row.id === identity) ?? null
  const labels: Record<string, string> = { disabled: 'Reminder is disabled.', not_due: 'Local reminder time has not arrived.', completed: 'Practice completed today; reminder suppressed.', already_sent: 'Reminder already exists for this local day.', sent: 'Reminder saved to your local inbox.' }
  return <main style={{ maxWidth: 'var(--content-width)' }} className="mx-auto w-full space-y-2xl px-l py-2xl text-on-surface"><h2 data-type="title-m">Life calendar</h2><p>Your declared horizon is a planning assumption, not a lifespan prediction. Sleep and activity totals are projections, not measured observations.</p>{error && <p role="alert">{error}</p>}{loading && <p role="status">Loading life calendar…</p>}{!loading && <ConfigEditor key={config?.revision ?? 0} config={config} saved={() => setGeneration(n => n + 1)} />}
    {projection?.configured && <section className="space-y-m" aria-label="Lifetime projection"><h2 data-type="title-m">Declared horizon: {projection.horizon_date}</h2><p>{projection.elapsed_days} elapsed days · {projection.remaining_days} projected remaining days</p><p>{projection.weeks_elapsed} elapsed weeks of {projection.weeks_total} projected weeks</p><div aria-label="Life week grid" className="grid gap-px max-w-3xl" style={{ gridTemplateColumns: 'repeat(52,minmax(0,1fr))' }}>{Array.from({ length: projection.weeks_total }, (_, index) => <span key={index} title={`Week ${index + 1}: ${index < projection.weeks_elapsed ? 'elapsed' : 'projected'}`} className={`aspect-square ${index < projection.weeks_elapsed ? 'bg-primary' : 'bg-surface-high'}`} />)}</div><p>Projected remaining sleep: {Math.round(projection.sleep_hours_remaining)} hours · Waking time: {Math.round(projection.waking_hours_remaining)} hours</p>{projection.budgets.map(row => <p key={row.name}>{row.name}: {row.hours_per_week} hours/week · {Math.round(row.remaining_hours)} projected remaining hours</p>)}</section>}
    <section className="space-y-2"><h2 data-type="title-m">Completion reminder</h2><Button onClick={check} loading={busy}>Check reminder now</Button>{reminder && <p role="status">{labels[reminder] || reminder}</p>}<a href="#/inbox">Open local inbox</a></section>
    <section className="space-y-m"><h2 data-type="title-m">Life events</h2><Button onClick={() => setQuery({ event: null })}>New life event</Button>{events.map(row => <button key={row.id} className="block rounded-lg bg-surface-container p-3 text-left" onClick={() => setQuery({ event: row.id })}>{row.date}: {row.title} · {row.kind}</button>)}{!loading && !events.length && <p>No life events.</p>}{!loading && (!identity || selected) && <EventEditor key={selected ? `${selected.id}:${selected.revision}` : generation} row={selected} saved={() => { setQuery({ event: null }); setGeneration(n => n + 1) }} />}{!!history.length && <section aria-label="Life event history" className="space-y-l rounded-lg bg-surface-container p-l">{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.title} · {row.date}{row.deleted ? ' · deleted' : ''}</p>)}</section>}</section>
  </main>
}
