import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Product = { id: string; kind: 'alcohol' | 'nicotine'; name: string; details: { volume_ml?: number; abv_percent?: number; mg_per_unit?: number }; revision: number; deleted: boolean }
type Entry = Product & { observed_at: string; source: string; notes: string; count: number; ethanol_g: number | null; nicotine_mg: number | null }
type Summary = { totals: { ethanol_g: number | null; nicotine_mg: number | null }; logged_days: { alcohol: number; nicotine: number }; averages_per_logged_day: { ethanol_g: number | null; nicotine_mg: number | null }; days: { date: string; entry_count: number; ethanol_g: number | null; nicotine_mg: number | null }[] }
const base = '/api/capabilities/wellbeing/substances'
const quantity = (value: number | null) => value === null ? 'Not recorded' : String(Math.round(value * 1000) / 1000)

function Editor({ row, presets, presetMode, saved }: { row: Product | Entry | null; presets: Product[]; presetMode: boolean; saved: () => void }) {
  const entry = row && 'observed_at' in row ? row : null
  const [kind, setKind] = useState(row?.kind ?? 'alcohol')
  const [name, setName] = useState(row?.name ?? '')
  const [volume, setVolume] = useState(String(row?.details.volume_ml ?? ''))
  const [abv, setAbv] = useState(String(row?.details.abv_percent ?? ''))
  const [mg, setMg] = useState(String(row?.details.mg_per_unit ?? ''))
  const [count, setCount] = useState(String(entry?.count ?? 1))
  const [observed, setObserved] = useState(entry?.observed_at ?? new Date().toISOString())
  const [source, setSource] = useState(entry?.source ?? '')
  const [notes, setNotes] = useState(entry?.notes ?? '')
  const [presetId, setPresetId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const receipt = useRef({ fingerprint: '', request_id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const fields = { name, details: kind === 'alcohol' ? { volume_ml: Number(volume), abv_percent: Number(abv) } : { mg_per_unit: Number(mg) } }
    const body = { ...(presetId ? { preset_id: presetId } : { ...fields, ...(row ? {} : { kind }) }), ...(row ? { revision: row.revision } : {}), ...(!presetMode ? { observed_at: observed, count: Number(count), notes, ...(row ? {} : { source }) } : {}) }
    const fingerprint = JSON.stringify(body)
    if (fingerprint !== receipt.current.fingerprint) receipt.current = { fingerprint, request_id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(`${base}/${presetMode ? 'presets' : 'entries'}${row ? `/${row.id}` : ''}`, row ? 'PUT' : 'POST', { ...body, request_id: receipt.current.request_id }); saved() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-3">
    <h2 data-type="title-m">{row ? 'Edit' : 'New'} {presetMode ? 'product preset' : 'consumption entry'}</h2>
    {!presetMode && !row && <label className="block">Product preset<select aria-label="Product preset" value={presetId} onChange={e => setPresetId(e.target.value)} className="block w-full rounded-md bg-surface-container p-2"><option value="">Enter product details</option>{presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}
    {!presetId && <>
      {!row && <label className="block">Kind<select aria-label={presetMode ? 'Preset kind' : 'Entry kind'} value={kind} onChange={e => setKind(e.target.value as Product['kind'])} className="block rounded-md bg-surface-container p-2"><option value="alcohol">Alcohol</option><option value="nicotine">Nicotine</option></select></label>}
      <Field label="Product name"><TextInput value={name} onChange={setName} required /></Field>
      {kind === 'alcohol' ? <div className="grid gap-3 sm:grid-cols-2"><Field label="Volume per serving (mL)"><TextInput value={volume} onChange={setVolume} required /></Field><Field label="ABV (%)"><TextInput value={abv} onChange={setAbv} required /></Field></div> : <Field label="Labeled nicotine per unit (mg)"><TextInput value={mg} onChange={setMg} required /></Field>}
    </>}
    {!presetMode && <><Field label="Servings or units"><TextInput value={count} onChange={setCount} required /></Field><Field label="Observed at (with offset)"><TextInput value={observed} onChange={setObserved} required /></Field><Field label="Entry source"><TextInput value={source} onChange={setSource} required disabled={!!row} /></Field><Field label="Entry notes"><TextInput value={notes} onChange={setNotes} /></Field></>}
    {error && <p role="alert" className="text-danger">{error}</p>}
    <Button type="submit" loading={busy} disabled={row?.deleted}>{presetMode ? 'Save product preset' : 'Save consumption entry'}</Button>
  </form>
}

export default function Substances() {
  const { query: params, setQuery: setRouteQuery } = useHashRoute('capabilities')
  const setParams = (values: Record<string, string>) => setRouteQuery({ id: null, preset: null, mode: null, ...values })
  const identity = params.id, presetId = params.preset
  const presetMode = params.mode === 'presets'
  const [entries, setEntries] = useState<Entry[]>([])
  const [presets, setPresets] = useState<Product[]>([])
  const [selected, setSelected] = useState<Entry | null>(null)
  const [history, setHistory] = useState<Entry[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [timezone, setTimezone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const [zone, setZone] = useState(timezone)
  const [query, setQuery] = useState('')
  const [generation, setGeneration] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [deleting, setDeleting] = useState(false)
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setSelected(null); setHistory([]); setSummary(null)
    Promise.all([requestJson<{ entries: Entry[] }>(`${base}/entries?${query}`), requestJson<{ presets: Product[] }>(`${base}/presets`), requestJson<Summary>(`${base}/summary?${new URLSearchParams({ timezone: zone, days: '30' })}`), identity ? requestJson<Entry>(`${base}/entries/${encodeURIComponent(identity)}`) : Promise.resolve(null), identity ? requestJson<{ history: Entry[] }>(`${base}/entries/${encodeURIComponent(identity)}/history`) : Promise.resolve({ history: [] })])
      .then(([list, products, totals, row, revisions]) => { if (active) { setEntries(list.entries); setPresets(products.presets); setSummary(totals); setSelected(row); setHistory(revisions.history) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, presetId, query, generation, zone])
  async function remove(row: Product, preset: boolean) {
    if (deleting) return
    setDeleting(true); setError('')
    try { await requestJson(`${base}/${preset ? 'presets' : 'entries'}/${row.id}?${new URLSearchParams({ request_id: crypto.randomUUID(), revision: String(row.revision) })}`, 'DELETE'); setParams(preset ? { mode: 'presets' } : {}); setGeneration(n => n + 1) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setDeleting(false) }
  }
  const product = presets.find(p => p.id === presetId) ?? null
  const edited = presetMode ? product : selected
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface">
    <h1 data-type="headline-s">Alcohol and nicotine records</h1>
    <p>Ethanol totals use volume × ABV × 0.789 g/mL. Nicotine totals use labeled product content. Missing days remain unrecorded.</p>
    <div className="flex flex-wrap gap-3"><Button onClick={() => setParams({})}>New entry</Button><Button variant="secondary" onClick={() => setParams({ mode: 'presets' })}>Manage product presets</Button></div>
    {error && <p role="alert" className="text-danger">{error} <Button onClick={() => setGeneration(n => n + 1)}>Reload records</Button></p>}
    {loading && <p role="status">Loading consumption records…</p>}
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="space-y-3 min-w-0">
        {!loading && (presetMode ? !presetId || product : !identity || selected) && <Editor key={`${presetMode}:${edited?.id ?? 'new'}:${edited?.revision ?? generation}`} row={edited} presets={presets} presetMode={presetMode} saved={() => setGeneration(n => n + 1)} />}
        {presetMode && presetId && !product && !loading && <p>Product preset not found.</p>}
        {edited && !edited.deleted && <Button variant="danger" loading={deleting} onClick={() => remove(edited, presetMode)}>Delete {presetMode ? 'product preset' : 'consumption entry'}</Button>}
        {!!history.length && !presetMode && <section aria-label="Entry history"><h2 data-type="title-m">Entry history</h2>{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.name} · {row.count} units · {quantity(row.ethanol_g ?? row.nicotine_mg)} {row.kind === 'alcohol' ? 'g ethanol' : 'mg nicotine'} · {row.notes}{row.deleted ? ' · deleted' : ''}</p>)}</section>}
      </section>
      <section className="space-y-3 min-w-0" aria-label={presetMode ? 'Product presets' : 'Consumption entries'}>
        {presetMode ? presets.map(row => <button key={row.id} onClick={() => setParams({ mode: 'presets', preset: row.id })} className="block w-full text-left rounded-lg bg-surface-container p-3">{row.name} · {row.kind}</button>) : entries.map(row => <button key={row.id} onClick={() => setParams({ id: row.id })} className="block w-full text-left rounded-lg bg-surface-container p-3 break-words">{row.name}: {quantity(row.ethanol_g ?? row.nicotine_mg)} {row.kind === 'alcohol' ? 'g ethanol' : 'mg nicotine'}<p>{row.observed_at} · {row.source}</p></button>)}
        {!loading && !(presetMode ? presets : entries).length && <p>{presetMode ? 'No product presets.' : 'No consumption entries.'}</p>}
        {!presetMode && entries.length === 100 && <Button onClick={() => { const next = new URLSearchParams(query); next.set('offset', String(Number(next.get('offset') ?? 0) + 100)); setQuery(next.toString()) }}>Next entries page</Button>}
      </section>
    </div>
    <form className="flex flex-wrap items-end gap-3" onSubmit={e => { e.preventDefault(); setZone(timezone) }}><Field label="Summary timezone"><TextInput value={timezone} onChange={setTimezone} /></Field><Button type="submit">Apply summary timezone</Button></form>
    {summary && <section className="space-y-3"><h2 data-type="title-m">Last 30 local calendar days</h2><p>Ethanol: {quantity(summary.totals.ethanol_g)} g · {summary.logged_days.alcohol} logged days</p><p>Labeled nicotine: {quantity(summary.totals.nicotine_mg)} mg · {summary.logged_days.nicotine} logged days</p><p>Per logged day: {quantity(summary.averages_per_logged_day.ethanol_g)} g ethanol; {quantity(summary.averages_per_logged_day.nicotine_mg)} mg nicotine.</p><div className="overflow-x-auto"><table className="w-full text-left"><thead><tr><th>Date</th><th>Entries</th><th>Ethanol (g)</th><th>Nicotine (mg)</th></tr></thead><tbody>{summary.days.map(day => <tr key={day.date}><td>{day.date}</td><td>{day.entry_count}</td><td>{quantity(day.ethanol_g)}</td><td>{quantity(day.nicotine_mg)}</td></tr>)}</tbody></table></div></section>}
  </main>
}
