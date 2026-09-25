import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { records, type Measurement } from './api'
import Privacy from './Privacy'

const display = (row: Measurement) => row.kind === 'body_weight' ? `${row.values.weight} kg` : `${row.values.systolic}/${row.values.diastolic} mmHg`

function Editor({ record, onSaved }: { record: Measurement | null; onSaved: (row: Measurement) => void }) {
  const [kind, setKind] = useState(record?.kind ?? 'body_weight')
  const [observed, setObserved] = useState(record?.observed_at ?? new Date().toISOString())
  const [first, setFirst] = useState(String(record?.values.weight ?? record?.values.systolic ?? ''))
  const [second, setSecond] = useState(String(record?.values.diastolic ?? ''))
  const [source, setSource] = useState(record?.source ?? '')
  const [notes, setNotes] = useState(record?.notes ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const request = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    const body = { observed_at: observed, unit: kind === 'body_weight' ? 'kg' : 'mmHg', values: kind === 'body_weight' ? { weight: Number(first) } : { systolic: Number(first), diastolic: Number(second) }, notes, ...(record ? { revision: record.revision } : { kind, source }) }
    const fingerprint = JSON.stringify(body)
    if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { onSaved(await records.save(record?.id ?? null, { ...body, request_id: request.current.id })) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-4">
    <h2 data-type="title-m">{record ? 'Correct measurement' : 'Enter measurement'}</h2>
    {!record && <Field label="Measurement kind"><select aria-label="Measurement kind" value={kind} onChange={e => setKind(e.target.value as Measurement['kind'])} className="w-full rounded-md bg-surface-container p-2"><option value="body_weight">Body weight</option><option value="blood_pressure">Blood pressure</option></select></Field>}
    <Field label="Observed at" hint="ISO timestamp including offset, for example 2026-09-25T09:00:00+02:00"><TextInput value={observed} onChange={setObserved} required /></Field>
    <Field label={kind === 'body_weight' ? 'Weight (kg)' : 'Systolic (mmHg)'}><TextInput value={first} onChange={setFirst} required /></Field>
    {kind === 'blood_pressure' && <Field label="Diastolic (mmHg)"><TextInput value={second} onChange={setSecond} required /></Field>}
    <Field label="Source"><TextInput value={source} onChange={setSource} disabled={!!record} required /></Field>
    <Field label="Notes"><TextInput value={notes} onChange={setNotes} maxLength={4000} /></Field>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <Button type="submit" loading={busy}>{record ? 'Save correction' : 'Save measurement'}</Button>
  </form>
}

export default function Page() {
  const { query: params, setQuery: setRouteQuery } = useHashRoute('capabilities')
  const setParams = (values: Record<string, string>) => setRouteQuery({ id: null, ...values })
  const identity = params.id
  const [rows, setRows] = useState<Measurement[]>([])
  const [selected, setSelected] = useState<Measurement | null>(null)
  const [history, setHistory] = useState<Measurement[]>([])
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [generation, setGeneration] = useState(0)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [query, setQuery] = useState('')
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setSelected(null); setHistory([])
    Promise.all([records.list(query), identity ? records.get(identity) : Promise.resolve(null), identity ? records.history(identity) : Promise.resolve({ history: [] })])
      .then(([list, record, revisions]) => { if (active) { setRows(list.measurements); setSelected(record); setHistory(revisions.history) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, query, generation])
  async function download() {
    try {
      const exported = await records.export()
      const url = URL.createObjectURL(new Blob([JSON.stringify(exported, null, 2)], { type: 'application/json' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'wellbeing-records.json'; anchor.click(); URL.revokeObjectURL(url)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
  }
  if (params.view === 'privacy') return <Privacy />
  return <main className="h-full overflow-auto p-4 sm:p-6 text-on-surface">
    <div className="flex flex-wrap items-center justify-between gap-3 mb-6"><h1 data-type="headline-s">Wellbeing records</h1><div className="flex gap-2"><Button variant="secondary" onClick={() => setRouteQuery({ view: 'privacy' })}>Privacy</Button><Button onClick={() => setParams({})}>New measurement</Button><Button variant="secondary" onClick={download}>Export records</Button></div></div>
    <form className="grid gap-3 sm:grid-cols-3 mb-6" onSubmit={e => { e.preventDefault(); const next = new URLSearchParams(); if (from) next.set('from', from); if (to) next.set('to', to); setQuery(next.toString()) }}>
      <Field label="From (timestamp with offset)"><TextInput value={from} onChange={setFrom} /></Field><Field label="To (timestamp with offset)"><TextInput value={to} onChange={setTo} /></Field><Button type="submit">Filter dates</Button>
    </form>
    {error && <div role="alert" className="mb-4 text-danger">{error} <Button variant="secondary" onClick={() => setGeneration(n => n + 1)}>Reload</Button></div>}
    <div className="grid gap-6 lg:grid-cols-2">
      <section aria-label="Measurements" className="min-w-0 space-y-3">
        {loading && <p role="status">Loading measurements…</p>}
        {!loading && !error && !rows.length && <p>No measurements in this date range.</p>}
        {rows.map(row => <button key={row.id} onClick={() => setParams({ id: row.id })} className="block w-full text-left rounded-lg bg-surface-container p-4 break-words"><strong>{display(row)}</strong><p>{row.observed_at}</p><p>{row.source} · revision {row.revision}</p></button>)}
        {rows.length === 100 && <Button variant="secondary" onClick={() => { const next = new URLSearchParams(query); next.set('offset', String(Number(next.get('offset') ?? 0) + 100)); setQuery(next.toString()) }}>Next page</Button>}
      </section>
      <section className="min-w-0">
        {(!identity || selected) && <Editor key={selected ? `${selected.id}:${selected.revision}` : 'new'} record={selected} onSaved={row => { setParams({ id: row.id }); setGeneration(n => n + 1) }} />}
        {!!history.length && <section aria-label="Correction history" className="mt-6 space-y-3"><h2 data-type="title-m">Correction history</h2>{history.map(row => <article key={row.revision} className="rounded-lg bg-surface-container p-3 break-words"><p>Revision {row.revision}: {display(row)}</p><p>{row.observed_at} · {row.source}</p><p>{row.notes}</p></article>)}</section>}
      </section>
    </div>
  </main>
}
