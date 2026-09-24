import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Lab = { id: string; analyte: string; observed_at: string; value: number; unit: string; reference_low: number | null; reference_high: number | null; notes: string; source: string; artifact: { slug: string; version: number; sha256: string }; revision: number }
type ImportInput = { filename: string; format: string; content: string; source: string }
type Preview = { preview_id: string; rows: Lab[]; duplicates: number }
const base = '/api/capabilities/wellbeing/labs'

function Correction({ row, saved }: { row: Lab; saved: () => void }) {
  const [value, setValue] = useState(String(row.value))
  const [low, setLow] = useState(row.reference_low === null ? '' : String(row.reference_low))
  const [high, setHigh] = useState(row.reference_high === null ? '' : String(row.reference_high))
  const [notes, setNotes] = useState(row.notes)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const receipt = useRef({ fingerprint: '', request_id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const body = { revision: row.revision, value: Number(value), reference_low: low === '' ? null : Number(low), reference_high: high === '' ? null : Number(high), notes }
    const fingerprint = JSON.stringify(body)
    if (fingerprint !== receipt.current.fingerprint) receipt.current = { fingerprint, request_id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(`${base}/${row.id}`, 'PUT', { ...body, request_id: receipt.current.request_id }); saved() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-3">
    <h2 data-type="title-m">Correct {row.analyte}</h2>
    <p>{row.observed_at} · {row.source} · {row.unit}</p>
    <Field label="Result value"><TextInput value={value} onChange={setValue} required /></Field>
    <div className="grid gap-3 sm:grid-cols-2"><Field label="Reference low"><TextInput value={low} onChange={setLow} /></Field><Field label="Reference high"><TextInput value={high} onChange={setHigh} /></Field></div>
    <Field label="Correction notes"><TextInput value={notes} onChange={setNotes} /></Field>
    <a href={`${base}/${row.id}/source`} className="text-primary underline">Original attachment · version {row.artifact.version}</a>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <Button type="submit" loading={busy}>Save laboratory correction</Button>
  </form>
}

export default function Labs() {
  const [params, setParams] = useSearchParams()
  const identity = params.get('id')
  const [filename, setFilename] = useState('')
  const [format, setFormat] = useState('csv')
  const [content, setContent] = useState('')
  const [source, setSource] = useState('')
  const [preview, setPreview] = useState<{ input: ImportInput; result: Preview; request_id: string } | null>(null)
  const [rows, setRows] = useState<Lab[]>([])
  const [history, setHistory] = useState<Lab[]>([])
  const [selected, setSelected] = useState<Lab | null>(null)
  const [trend, setTrend] = useState<Lab[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [generation, setGeneration] = useState(0)
  const [query, setQuery] = useState('')
  const [analyte, setAnalyte] = useState('')
  const [from, setFrom] = useState('')
  const [notice, setNotice] = useState('')
  const input = { filename, format, content, source }
  const previewCurrent = preview && JSON.stringify(preview.input) === JSON.stringify(input)
  useEffect(() => {
    let active = true
    setLoading(true); setError(''); setSelected(null); setHistory([]); setTrend([])
    Promise.all([requestJson<{ records: Lab[] }>(`${base}?${query}`), identity ? requestJson<Lab>(`${base}/${encodeURIComponent(identity)}`) : Promise.resolve(null), identity ? requestJson<{ history: Lab[] }>(`${base}/${encodeURIComponent(identity)}/history`) : Promise.resolve({ history: [] })])
      .then(async ([list, row, revisions]) => {
        const projection = row ? await requestJson<{ records: Lab[] }>(`${base}/trends?${new URLSearchParams({ analyte: row.analyte, unit: row.unit })}`) : { records: [] }
        if (active) { setRows(list.records); setSelected(row); setHistory(revisions.history); setTrend(projection.records) }
      }).catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, query, generation])
  async function importAction(commit: boolean) {
    if (busy || (commit && !previewCurrent)) return
    setBusy(true); setError('')
    try {
      if (commit && preview) {
        const receipt = await requestJson<{ added: number; duplicates: number }>(`${base}/import/commit`, 'POST', { ...preview.input, preview_id: preview.result.preview_id, request_id: preview.request_id })
        setNotice(`Imported ${receipt.added}; duplicates ${receipt.duplicates}.`); setPreview(null); setGeneration(n => n + 1)
      } else {
        const result = await requestJson<Preview>(`${base}/import/preview`, 'POST', input)
        setPreview({ input, result, request_id: crypto.randomUUID() }); setNotice('')
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface">
    <h1 data-type="headline-s">Laboratory records</h1>
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="space-y-3 min-w-0" aria-label="Import laboratory records">
        <h2 data-type="title-m">Import CSV or JSON</h2>
        <p>Columns: analyte, observed_at (with offset), value, unit. Optional: reference_low, reference_high, notes, external_id. JSON uses an array of these objects.</p>
        <label className="block">Choose source file<input aria-label="Choose source file" type="file" accept=".csv,.json" className="block w-full" onChange={async e => { const file = e.target.files?.[0]; if (!file) return; if (file.size > 500000) { setError('Source file exceeds 500000 bytes'); return } try { setContent(await file.text()); setFilename(file.name); setFormat(file.name.endsWith('.json') ? 'json' : 'csv'); setPreview(null) } catch (err) { setError(String(err)) } }} /></label>
        <Field label="Filename"><TextInput value={filename} onChange={setFilename} required /></Field>
        <Field label="Laboratory source"><TextInput value={source} onChange={setSource} required /></Field>
        <label className="block">Format<select aria-label="Import format" value={format} onChange={e => setFormat(e.target.value)} className="block rounded-md bg-surface-container p-2"><option value="csv">CSV</option><option value="json">JSON</option></select></label>
        <label className="block">Source content<textarea aria-label="Source content" value={content} onChange={e => setContent(e.target.value)} rows={6} className="block w-full rounded-md bg-surface-container p-3 font-mono" /></label>
        <Button loading={busy} onClick={() => importAction(false)}>Preview import</Button>
        {preview && previewCurrent && <div className="space-y-2"><p>{preview.result.rows.length} rows; {preview.result.duplicates} duplicates.</p><ul>{preview.result.rows.map((row, index) => <li key={index}>{row.analyte}: {row.value} {row.unit} · {row.observed_at}</li>)}</ul><Button loading={busy} onClick={() => importAction(true)}>Commit import</Button></div>}
        {notice && <p role="status">{notice}</p>}
      </section>
      <section className="space-y-3 min-w-0" aria-label="Laboratory measurements">
        <form onSubmit={e => { e.preventDefault(); const next = new URLSearchParams(); if (analyte) next.set('analyte', analyte); if (from) next.set('from', from); setQuery(next.toString()) }} className="space-y-3"><Field label="Filter analyte"><TextInput value={analyte} onChange={setAnalyte} /></Field><Field label="From timestamp"><TextInput value={from} onChange={setFrom} /></Field><Button type="submit">Filter laboratory records</Button></form>
        {loading && <p role="status">Loading laboratory records…</p>}
        {!loading && !error && !rows.length && <p>No laboratory records.</p>}
        {rows.map(row => <button key={row.id} onClick={() => setParams({ id: row.id })} className="block w-full text-left rounded-lg bg-surface-container p-3 break-words">{row.analyte}: {row.value} {row.unit}<p>{row.observed_at} · {row.source}</p></button>)}
        {rows.length === 100 && <Button onClick={() => { const next = new URLSearchParams(query); next.set('offset', String(Number(next.get('offset') ?? 0) + 100)); setQuery(next.toString()) }}>Next laboratory page</Button>}
      </section>
    </div>
    {error && <div role="alert" className="text-danger">{error} <Button onClick={() => setGeneration(n => n + 1)}>Reload laboratory records</Button></div>}
    {selected && <div className="grid gap-6 lg:grid-cols-2"><Correction key={`${selected.id}:${selected.revision}`} row={selected} saved={() => setGeneration(n => n + 1)} /><section className="space-y-3"><h2 data-type="title-m">Laboratory correction history</h2>{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.value} {row.unit} · {row.notes}</p>)}<h2 data-type="title-m">Recorded trend · {selected.unit}</h2><p>Latest 500 observations with this exact analyte and unit, in chronological order.</p><ol>{trend.map(row => <li key={row.id}>{row.observed_at}: {row.value} {row.unit}</li>)}</ol></section></div>}
  </main>
}
