import { Table, THead, Th, Td } from '../../../shared/ui/Table'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput, Checkbox, Select } from '../../../shared/ui/forms'

type Plan = { id: string; revision: number; name: string; kind: string; instructions: string; source: string; timezone: string; start_date: string; end_date: string | null; weekdays: number[]; archived: boolean }
type RecordRow = { id: string; revision: number; date: string; status: string; observed_at: string; notes: string }
type Summary = { scheduled_days: number; completed_days: number; skipped_days: number; unrecorded_days: number; completion_rate: number | null; recording_rate: number | null; days: { date: string; scheduled: boolean; status: string | null }[] }
const base = '/api/capabilities/wellbeing/interventions'
const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const percent = (value: number | null) => value === null ? 'Not recorded' : `${Math.round(value * 100)}%`

function PlanEditor({ row, saved }: { row: Plan | null; saved: (id: string) => void }) {
  const [name, setName] = useState(row?.name ?? ''), [instructions, setInstructions] = useState(row?.instructions ?? ''), [source, setSource] = useState(''), [kind, setKind] = useState('activity')
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'), [start, setStart] = useState(new Date().toISOString().slice(0, 10)), [end, setEnd] = useState(''), [days, setDays] = useState([0, 1, 2, 3, 4, 5, 6]), [archived, setArchived] = useState(row?.archived ?? false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const receipt = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const payload = row ? { name, instructions, archived, revision: row.revision } : { name, instructions, source, kind, timezone, start_date: start, end_date: end || null, weekdays: days }
    const fingerprint = JSON.stringify(payload)
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { const result = await requestJson<Plan>(base + '/plans' + (row ? `/${row.id}` : ''), row ? 'PUT' : 'POST', { ...payload, request_id: receipt.current.id }); saved(result.id) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-m"><h2 data-type="title-m">{row ? 'Edit intervention' : 'New intervention'}</h2><Field label="Intervention name"><TextInput value={name} onChange={setName} required /></Field><Field label="User supplied instructions"><TextInput value={instructions} onChange={setInstructions} required /></Field>{row ? <><p>Schedule: {row.start_date} to {row.end_date || 'open end'} · {row.timezone} · {row.weekdays.map(day => weekdays[day]).join(', ')}</p><p>Source: {row.source}. Create a new intervention to change its schedule.</p><label><Checkbox checked={archived} onChange={next => setArchived(next)} ariaLabel={"Archived intervention"} />Archived intervention</label></> : <><label className="block">Kind<Select ariaLabel="Intervention kind" value={kind} onChange={next => setKind(next)} options={[...['activity', 'medication', 'supplement', 'other'].map(value => ({ value: value, label: value }))]} /></label><Field label="Intervention source"><TextInput value={source} onChange={setSource} required /></Field><Field label="Intervention timezone"><TextInput value={timezone} onChange={setTimezone} required /></Field><Field label="Start date"><TextInput placeholder="YYYY-MM-DD" value={start} onChange={setStart} required /></Field><Field label="End date (optional)"><TextInput placeholder="YYYY-MM-DD" value={end} onChange={setEnd} /></Field><fieldset><legend>Scheduled weekdays</legend>{weekdays.map((day, index) => <label key={day} className="inline-flex gap-1 mr-3"><Checkbox checked={days.includes(index)} onChange={next => setDays(current => next ? [...current, index].sort() : current.filter(value => value !== index))} ariaLabel={day} />{day}</label>)}</fieldset></>}{error && <p role="alert">{error}</p>}<Button type="submit" loading={busy}>Save intervention</Button></form>
}

function RecordEditor({ plan, row, saved }: { plan: Plan; row: RecordRow | null; saved: () => void }) {
  const [day, setDay] = useState(row?.date ?? new Intl.DateTimeFormat('en-CA', { timeZone: plan.timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date()))
  const [status, setStatus] = useState(row?.status ?? 'completed'), [observed, setObserved] = useState(row?.observed_at ?? new Date().toISOString()), [notes, setNotes] = useState(row?.notes ?? '')
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const receipt = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const payload = { status, observed_at: observed, notes, ...(row ? { revision: row.revision } : { date: day }) }
    const fingerprint = JSON.stringify(payload)
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(row ? `${base}/records/${row.id}` : `${base}/plans/${plan.id}/records`, row ? 'PUT' : 'POST', { ...payload, request_id: receipt.current.id }); saved() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-m"><h2 data-type="title-m">{row ? 'Correct adherence record' : 'Record adherence'}</h2><Field label="Scheduled date" hint={!!row ? 'Create a new observation to use another date' : undefined}><TextInput placeholder="YYYY-MM-DD" value={day} onChange={setDay} readOnly={!!row} readOnlyReason={!!row ? 'Create a new observation to use another date' : undefined} required /></Field><label className="block">Status<Select ariaLabel="Recorded status" value={status} onChange={next => setStatus(next)} options={[{ value: "completed", label: "Completed" }, { value: "skipped", label: "Skipped" }]} /></label><Field label="Observed at (with offset)"><TextInput value={observed} onChange={setObserved} required /></Field><Field label="Adherence notes"><TextInput value={notes} onChange={setNotes} /></Field>{error && <p role="alert">{error}</p>}<Button type="submit" loading={busy}>Save adherence record</Button></form>
}

export default function Interventions() {
  const { query: params, setQuery: setRouteQuery } = useHashRoute('capabilities')
  const setParams = (values: Record<string, string>) => setRouteQuery({ plan: null, record: null, ...values })
  const identity = params.plan, recordId = params.record
  const [plans, setPlans] = useState<Plan[]>([]), [selected, setSelected] = useState<Plan | null>(null), [records, setRecords] = useState<RecordRow[]>([]), [record, setRecord] = useState<RecordRow | null>(null)
  const [history, setHistory] = useState<RecordRow[]>([]), [planHistory, setPlanHistory] = useState<Plan[]>([]), [summary, setSummary] = useState<Summary | null>(null), [archived, setArchived] = useState(false)
  const [generation, setGeneration] = useState(0), [loading, setLoading] = useState(true), [error, setError] = useState('')
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setSelected(null); setRecord(null); setSummary(null)
    Promise.all([requestJson<{ plans: Plan[] }>(`${base}/plans?include_archived=${archived}`), identity ? requestJson<Plan>(`${base}/plans/${identity}`) : Promise.resolve(null), identity ? requestJson<{ records: RecordRow[] }>(`${base}/plans/${identity}/records`) : Promise.resolve({ records: [] }), identity ? requestJson<Summary>(`${base}/plans/${identity}/summary`) : Promise.resolve(null), recordId ? requestJson<RecordRow>(`${base}/records/${recordId}`) : Promise.resolve(null), recordId ? requestJson<{ history: RecordRow[] }>(`${base}/records/${recordId}/history`) : Promise.resolve({ history: [] }), identity ? requestJson<{ history: Plan[] }>(`${base}/plans/${identity}/history`) : Promise.resolve({ history: [] })])
      .then(([catalog, plan, rows, totals, row, revisions, planRevisions]) => { if (active) { setPlans(catalog.plans); setSelected(plan); setRecords(rows.records); setSummary(totals); setRecord(row); setHistory(revisions.history); setPlanHistory(planRevisions.history) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, recordId, archived, generation])
  return <main style={{ maxWidth: 'var(--content-width)' }} className="mx-auto w-full space-y-2xl px-l py-2xl text-on-surface"><h2 data-type="title-m">Interventions and adherence</h2><p>Record your own plans and observations. Unrecorded days remain unknown; completion percentages describe only explicitly recorded days.</p><Button onClick={() => setParams({})}>New intervention</Button><label className="block"><Checkbox checked={archived} onChange={next => setArchived(next)} ariaLabel={"Include archived interventions"} />Include archived interventions</label>{error && <p role="alert">{error}</p>}{loading && <p role="status">Loading interventions…</p>}<section aria-label="Intervention list" className="space-y-l rounded-lg bg-surface-container p-l">{plans.map(plan => <button key={plan.id} className="block rounded-lg border border-outline-variant/20 bg-surface-container p-l my-s" onClick={() => setParams({ plan: plan.id })}>{plan.name}{plan.archived ? ' (archived)' : ''}</button>)}{!loading && !plans.length && <p>No interventions.</p>}</section>
    {!loading && (!identity || selected) && <PlanEditor key={selected ? `${selected.id}:${selected.revision}` : 'new'} row={selected} saved={id => { setParams({ plan: id }); setGeneration(n => n + 1) }} />}
    {selected && <section className="space-y-l"><h2 data-type="title-m">Adherence for {selected.name}</h2>{(!selected.archived || record) && <RecordEditor key={`${record?.id ?? 'new'}:${record?.revision ?? generation}`} plan={selected} row={record} saved={() => setGeneration(n => n + 1)} />}{selected.archived && !record && <p>Archived interventions accept corrections to existing observations.</p>}{record && <Button onClick={() => setParams({ plan: selected.id })}>New adherence record</Button>}{records.map(row => <button key={row.id} className="block rounded-lg bg-surface-container p-3" onClick={() => setParams({ plan: selected.id, record: row.id })}>{row.date}: {row.status}</button>)}{!records.length && <p>No adherence records.</p>}</section>}
    {!!history.length && <section aria-label="Adherence history" className="space-y-l rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Adherence history</h2>{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.status} · {row.notes}</p>)}</section>}{!!planHistory.length && <section aria-label="Plan history" className="space-y-l rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Plan history</h2>{planHistory.map(row => <p key={row.revision}>Plan revision {row.revision}: {row.name} · {row.archived ? 'archived' : 'active'}</p>)}</section>}
    {summary && <section className="space-y-m"><h2 data-type="title-m">Last 30 local calendar days</h2><p>Completed: {summary.completed_days} · Skipped: {summary.skipped_days} · Unrecorded: {summary.unrecorded_days}</p><p>Completion among recorded days: {percent(summary.completion_rate)}</p><p>Recorded schedule coverage: {percent(summary.recording_rate)}</p><Table wrapClassName="overflow-x-auto" caption="Intervention observations for the last 30 local calendar days" className="w-full text-left"><THead><tr><Th>Date</Th><Th>Observation</Th></tr></THead><tbody>{summary.days.map(day => <tr key={day.date}><Td>{day.date}</Td><Td>{day.scheduled ? day.status ?? 'Unrecorded' : 'Not scheduled'}</Td></tr>)}</tbody></Table></section>}
  </main>
}
