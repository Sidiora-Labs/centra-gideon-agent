import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useSearchParams } from 'react-router-dom'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'

type Source = { id: string; filename: string; source: string; assembly: string; sample: string; variant_count: number }
type Variant = { id: string; chromosome: string; position: number; rsid: string; genotype: string; annotation: string; annotation_source: string; revision: number }
type Preview = { preview_id: string; row_count: number; duplicates: number; variants: Variant[] }
const base = '/api/capabilities/wellbeing/genome'

function Annotation({ row, saved }: { row: Variant; saved: () => void }) {
  const [annotation, setAnnotation] = useState(row.annotation)
  const [source, setSource] = useState(row.annotation_source)
  const [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const receipt = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const payload = { revision: row.revision, annotation, annotation_source: source }
    const fingerprint = JSON.stringify(payload)
    if (fingerprint !== receipt.current.fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(`${base}/variants/${row.id}/annotation`, 'PUT', { ...payload, request_id: receipt.current.id }); saved() }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-3"><h2 data-type="title-m">Annotate {row.rsid}</h2><p>{row.chromosome}:{row.position} · {row.genotype}</p><Field label="Authored annotation"><TextInput value={annotation} onChange={setAnnotation} /></Field><Field label="Annotation source"><TextInput value={source} onChange={setSource} required /></Field>{error && <p role="alert">{error}</p>}<Button type="submit" loading={busy}>Save annotation</Button></form>
}

export default function Genome() {
  const [params, setParams] = useSearchParams()
  const sourceId = params.get('source'), variantId = params.get('variant')
  const [filename, setFilename] = useState(''), [content, setContent] = useState(''), [format, setFormat] = useState('tsv'), [assembly, setAssembly] = useState(''), [source, setSource] = useState(''), [sample, setSample] = useState('')
  const [sources, setSources] = useState<Source[]>([]), [variants, setVariants] = useState<Variant[]>([]), [selected, setSelected] = useState<Variant | null>(null), [history, setHistory] = useState<Variant[]>([])
  const [preview, setPreview] = useState<Preview | null>(null), [message, setMessage] = useState(''), [error, setError] = useState(''), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false)
  const [generation, setGeneration] = useState(0), [chromosome, setChromosome] = useState(''), [rsid, setRsid] = useState(''), [query, setQuery] = useState('')
  const receipt = useRef(''), fileGeneration = useRef(0)
  useEffect(() => { setPreview(null); setMessage(''); receipt.current = '' }, [filename, content, format, assembly, source, sample])
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setSelected(null); setHistory([])
    Promise.all([requestJson<{ sources: Source[] }>(base + '/sources'), sourceId ? requestJson<{ variants: Variant[] }>(`${base}/sources/${encodeURIComponent(sourceId)}/variants?${query}`) : Promise.resolve({ variants: [] }), variantId ? requestJson<Variant>(`${base}/variants/${encodeURIComponent(variantId)}`) : Promise.resolve(null), variantId ? requestJson<{ history: Variant[] }>(`${base}/variants/${encodeURIComponent(variantId)}/history`) : Promise.resolve({ history: [] })])
      .then(([catalog, rows, row, revisions]) => { if (active) { setSources(catalog.sources); setVariants(rows.variants); setSelected(row); setHistory(revisions.history) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [sourceId, variantId, query, generation])
  async function choose(file?: File) {
    const current = ++fileGeneration.current; setPreview(null); setContent(''); setFilename(''); setError('')
    if (!file) return
    if (file.size > 8 * 1024 * 1024) { setError('Genome input exceeds 8 MiB'); return }
    const reader = new FileReader()
    reader.onload = () => { if (current === fileGeneration.current) { try { setContent(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(reader.result as ArrayBuffer)); setFilename(file.name) } catch { setError('Genome file must be UTF-8') } } }
    reader.onerror = () => { if (current === fileGeneration.current) setError('Genome file could not be read') }
    reader.readAsArrayBuffer(file)
  }
  async function importSource(commit: boolean) {
    if (busy) return
    setBusy(true); setError(''); setMessage('')
    const payload = { filename, content, format, assembly, source, sample }
    try {
      if (commit && preview) {
        if (!receipt.current) receipt.current = crypto.randomUUID()
        const result = await requestJson<{ source: Source; added: number; duplicates: number }>(base + '/commit', 'POST', { ...payload, preview_id: preview.preview_id, request_id: receipt.current })
        setMessage(`Indexed ${result.added} variants; ${result.duplicates} duplicates.`); setParams({ source: result.source.id }); setGeneration(n => n + 1)
      } else setPreview(await requestJson<Preview>(base + '/preview', 'POST', payload))
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  const activeSource = sources.find(row => row.id === sourceId)
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface"><h1 data-type="headline-s">Genome sources and annotations</h1><p>Index user supplied genotypes with an explicit assembly. Annotations are authored notes; no clinical interpretation is generated.</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    <section className="space-y-3"><h2 data-type="title-m">Import genome source</h2><label className="block">Choose genome file<input aria-label="Choose genome file" type="file" accept=".tsv,.txt,.vcf" onChange={e => choose(e.target.files?.[0])} /></label>{filename && <p>Selected: {filename}</p>}<label className="block">Genome format<select aria-label="Genome format" value={format} onChange={e => setFormat(e.target.value)}><option value="tsv">TSV: rsid chromosome position genotype</option><option value="vcf">VCF with sample GT</option></select></label><label className="block">Declared assembly<select aria-label="Declared assembly" value={assembly} onChange={e => setAssembly(e.target.value)}><option value="">Select declared assembly</option><option>GRCh37</option><option>GRCh38</option></select></label><Field label="Genome source"><TextInput value={source} onChange={setSource} /></Field><Field label="VCF sample (required with multiple samples)"><TextInput value={sample} onChange={setSample} /></Field><Button onClick={() => importSource(false)} loading={busy} disabled={!content || !assembly || !source}>Preview genome</Button>{preview && <div className="space-y-2"><p>{preview.row_count} variants; {preview.duplicates} existing.</p><p>Preview shows up to 100 variants.</p><ul>{preview.variants.map((row, index) => <li key={index}>{row.rsid} · {row.chromosome}:{row.position} · {row.genotype}</li>)}</ul><Button onClick={() => importSource(true)} loading={busy}>Commit genome import</Button></div>}</section>
    {loading && <p role="status">Loading genome records…</p>}
    <section className="space-y-3"><h2 data-type="title-m">Source catalog</h2>{sources.map(row => <button key={row.id} className="block rounded-lg bg-surface-container p-3 text-left" onClick={() => { setQuery(''); setParams({ source: row.id }) }}>{row.filename} · {row.assembly} · {row.variant_count} variants</button>)}{!loading && !sources.length && <p>No genome sources.</p>}</section>
    {activeSource && <section className="space-y-3"><h2 data-type="title-m">{activeSource.filename}</h2><p>{activeSource.source} · {activeSource.assembly} · {activeSource.sample || 'No named sample'}</p><a href={`${base}/sources/${activeSource.id}/original`} download>Download original genome</a><form onSubmit={e => { e.preventDefault(); setQuery(new URLSearchParams({ ...(chromosome ? { chromosome } : {}), ...(rsid ? { rsid } : {}) }).toString()) }} className="space-y-2"><Field label="Exact chromosome"><TextInput value={chromosome} onChange={setChromosome} /></Field><Field label="Exact variant ID"><TextInput value={rsid} onChange={setRsid} /></Field><Button type="submit">Filter variants</Button></form>{variants.map(row => <button key={row.id} className="block w-full rounded-lg bg-surface-container p-3 text-left" onClick={() => setParams({ source: activeSource.id, variant: row.id })}>{row.rsid}: {row.genotype} · {row.chromosome}:{row.position}</button>)}{!loading && !variants.length && <p>No matching variants.</p>}{variants.length === 100 && <Button onClick={() => { const next = new URLSearchParams(query); next.set('offset', String(Number(next.get('offset') || 0) + 100)); setQuery(next.toString()) }}>Next variants page</Button>}</section>}
    {selected && <Annotation key={`${selected.id}:${selected.revision}`} row={selected} saved={() => setGeneration(n => n + 1)} />}{!!history.length && <section aria-label="Annotation history"><h2 data-type="title-m">Annotation history</h2>{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.annotation || 'No authored annotation'} · {row.annotation_source}</p>)}</section>}
  </main>
}
