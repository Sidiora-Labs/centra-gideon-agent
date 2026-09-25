import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Metric = { id: string; metric: string; unit: string; value: number; observed_at: string; end_at: string | null; stage: string | null; device_source: string; source: string; artifact: { filename: string } }
type Input = { filename: string; format: string; source: string; content_base64: string }
type Preview = { preview_id: string; metric_count: number; lab_count: number; metrics: Metric[]; skipped: Record<string, number> }
const base = '/api/capabilities/wellbeing/apple'

export default function AppleHealth() {
  const [file, setFile] = useState({ filename: '', content_base64: '' })
  const [format, setFormat] = useState('xml')
  const [source, setSource] = useState('')
  const [preview, setPreview] = useState<{ input: Input; result: Preview; request_id: string } | null>(null)
  const [metrics, setMetrics] = useState<Metric[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [filter, setFilter] = useState('')
  const [unit, setUnit] = useState('')
  const [from, setFrom] = useState('')
  const [query, setQuery] = useState('')
  const [generation, setGeneration] = useState(0)
  const readerGeneration = useRef(0)
  const input = { ...file, format, source }
  const current = preview && JSON.stringify(preview.input) === JSON.stringify(input)
  useEffect(() => {
    let active = true; setLoading(true)
    requestJson<{ metrics: Metric[] }>(`${base}/metrics?${query}`).then(data => { if (active) setMetrics(data.metrics) })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [query, generation])
  function choose(selected: File | undefined) {
    const selectedGeneration = ++readerGeneration.current
    setFile({ filename: '', content_base64: '' }); setPreview(null); setError('')
    if (!selected) return
    if (!selected.size || selected.size > 8 * 1024 * 1024) { setError('Choose an export between 1 byte and 8 MiB.'); return }
    const reader = new FileReader()
    reader.onload = () => { if (readerGeneration.current === selectedGeneration) { setFile({ filename: selected.name, content_base64: String(reader.result).split(',')[1] }); setFormat(selected.name.endsWith('.zip') ? 'zip' : selected.name.endsWith('.json') ? 'json' : 'xml') } }
    reader.onerror = () => { if (readerGeneration.current === selectedGeneration) setError('Could not read the selected file.') }
    reader.readAsDataURL(selected)
  }
  async function run(commit: boolean) {
    if (busy || (commit && !current)) return
    setBusy(true); setError('')
    try {
      if (commit && preview) {
        const result = await requestJson<{ metrics_added: number; labs_added: number; duplicates: number }>(`${base}/import/commit`, 'POST', { ...preview.input, preview_id: preview.result.preview_id, request_id: preview.request_id })
        setNotice(`Imported ${result.metrics_added} metrics and ${result.labs_added} lab results; ${result.duplicates} duplicates.`); setPreview(null); setGeneration(n => n + 1)
      } else {
        setPreview({ input, result: await requestJson<Preview>(`${base}/import/preview`, 'POST', input), request_id: crypto.randomUUID() }); setNotice('')
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface">
    <h1 data-type="headline-s">Apple Health imports</h1>
    <p>Import XML, ZIP, Health Auto Export JSON or FHIR laboratory observations. Original files stay attached. Limits: 8 MiB upload, 32 MiB expanded ZIP, 20,000 records. Unsupported record counts are shown in preview.</p>
    <section className="space-y-3" aria-label="Apple export import">
      <label className="block">Choose export<input aria-label="Choose export" type="file" accept=".xml,.zip,.json" className="block w-full" onChange={e => choose(e.target.files?.[0])} /></label>
      {file.filename && <p>Selected: {file.filename}</p>}
      <Field label="Export source"><TextInput value={source} onChange={setSource} required /></Field>
      <label className="block">Format<select aria-label="Export format" value={format} onChange={e => setFormat(e.target.value)} className="block rounded-md bg-surface-container p-2"><option value="xml">Apple XML</option><option value="zip">Apple ZIP</option><option value="json">Health Auto Export JSON</option><option value="fhir">FHIR JSON</option></select></label>
      <Button disabled={!file.content_base64} loading={busy} onClick={() => run(false)}>Preview export</Button>
      {preview && current && <div className="space-y-3"><p>{preview.result.metric_count} metrics; {preview.result.lab_count} laboratory results.</p>{Object.entries(preview.result.skipped).map(([reason, count]) => <p key={reason}>{reason}: {count} skipped</p>)}<p>Preview shows at most 100 metric rows.</p><ul>{preview.result.metrics.map((row, index) => <li key={index}>{row.metric}: {row.value} {row.unit} · {row.stage ?? row.device_source}</li>)}</ul><Button loading={busy} onClick={() => run(true)}>Commit export import</Button></div>}
      {notice && <p role="status">{notice}</p>}
    </section>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" onSubmit={e => { e.preventDefault(); setError(''); const next = new URLSearchParams(); if (filter) next.set('metric', filter); if (unit) next.set('unit', unit); if (from) next.set('from', from); setQuery(next.toString()) }}><Field label="Metric identifier"><TextInput value={filter} onChange={setFilter} /></Field><Field label="Exact unit"><TextInput value={unit} onChange={setUnit} /></Field><Field label="From timestamp with offset"><TextInput value={from} onChange={setFrom} /></Field><Button type="submit">Filter imported metrics</Button></form>
    <section aria-label="Imported metrics" className="space-y-3">
      {loading && <p role="status">Loading imported metrics…</p>}
      {!loading && !error && !metrics.length && <p>No imported metrics.</p>}
      {metrics.map(row => <article key={row.id} className="rounded-lg bg-surface-container p-4 break-words"><h2 data-type="title-s">{row.metric}: {row.value} {row.unit}</h2><p>{row.observed_at}{row.end_at ? ` → ${row.end_at}` : ''}</p><p>{row.source} · {row.device_source}{row.stage ? ` · ${row.stage}` : ''}</p><a href={`${base}/metrics/${row.id}/source`} className="text-primary underline">Download original export</a></article>)}
      {metrics.length === 100 && <Button onClick={() => { const next = new URLSearchParams(query); next.set('offset', String(Number(next.get('offset') ?? 0) + 100)); setQuery(next.toString()) }}>Next metrics page</Button>}
    </section>
    <a href="#/capabilities/wellbeing/labs" className="text-primary underline">Open imported laboratory results</a>
  </main>
}
