import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Capture = { id: string; text: string; transcript: string | null }
type Preview = { preview_id: string; capture_id: string; revision: number; kind: string; fields: Record<string, unknown>; mapped_fields: Record<string, unknown>; unsupported_fields: string[]; destination: string; available: boolean; unavailable_reason: string }
type Receipt = { request_id: string; capture_id: string; kind: string; source_link: string; unsupported_fields: string[] }
const root = '/api/capabilities/knowledge/types'
const samples: Record<string, Record<string, string>> = { person: { name: '', notes: '' }, project: { name: '', brief: '' }, idea: { title: '', content: '' }, admin: { title: '', description: '' }, memory: { text: '' } }

export default function TypesPage() {
  const [captures, setCaptures] = useState<Capture[]>([])
  const [captureId, setCaptureId] = useState('')
  const [kind, setKind] = useState('idea')
  const [fields, setFields] = useState(JSON.stringify(samples.idea, null, 2))
  const [preview, setPreview] = useState<Preview | null>(null)
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [offset, setOffset] = useState(0)
  const [next, setNext] = useState<number | null>(null)
  const requestId = useRef(crypto.randomUUID())
  useEffect(() => {
    let active = true
    Promise.all([requestJson<{ items: Capture[] }>('/api/capabilities/knowledge/captures?limit=100'), requestJson<{ items: Receipt[]; next_offset: number | null }>(`${root}?offset=${offset}`)])
      .then(([inbox, history]) => { if (active) { setCaptures(inbox.items); setReceipts(history.items); setNext(history.next_offset) } })
      .catch(e => { if (active) setError(String(e.message || e)) })
    return () => { active = false }
  }, [offset])
  function reset() { setPreview(null); requestId.current = crypto.randomUUID(); setError('') }
  async function review() {
    setBusy(true); setError(''); setPreview(null)
    try { setPreview(await requestJson<Preview>(root + '/preview', 'POST', { capture_id: captureId, kind, fields: JSON.parse(fields) })) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  async function commit() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      const { preview_id, capture_id, revision, kind, fields } = preview
      const saved = await requestJson<Receipt>(root + '/commit', 'POST', { request_id: requestId.current, preview_id, capture_id, revision, kind, fields })
      setReceipts(current => [saved, ...current.filter(item => item.request_id !== saved.request_id)])
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const selected = captures.find(item => item.id === captureId)
  return <main className="mx-auto max-w-4xl space-y-5 p-6">
    <h1 className="text-2xl font-semibold">Classify captures</h1>
    <p>Review original fields and import into the existing destination. Edit saved records in their destination.</p>
    <a href="#/capabilities/knowledge/capture">Open capture inbox</a>
    {error && <p role="alert">{error}</p>}
    <label className="block">Original capture<select aria-label="Original capture" value={captureId} disabled={busy} onChange={e => { setCaptureId(e.target.value); reset() }}><option value="">Choose a capture</option>{captures.map(item => <option key={item.id} value={item.id}>{(item.transcript || item.text || 'Voice capture').slice(0, 100)}</option>)}</select></label>
    {selected && <blockquote className="whitespace-pre-wrap">{selected.text || selected.transcript}</blockquote>}
    <label className="block">Record type<select aria-label="Record type" value={kind} disabled={busy} onChange={e => { setKind(e.target.value); setFields(JSON.stringify(samples[e.target.value], null, 2)); reset() }}>{Object.keys(samples).map(name => <option key={name} value={name}>{name}</option>)}</select></label>
    <label className="block">Original fields (JSON)<textarea aria-label="Original fields (JSON)" className="block w-full min-h-48 border p-3" value={fields} disabled={busy} onChange={e => { setFields(e.target.value); reset() }} /></label>
    <Button disabled={!captureId || busy} onClick={() => void review()}>Review mapping</Button>
    {preview && <section aria-label="Mapping review" className="space-y-3 border p-4"><p>Destination: {preview.destination}</p><pre className="whitespace-pre-wrap">{JSON.stringify(preview.mapped_fields, null, 2)}</pre><p>Unmapped original fields: {preview.unsupported_fields.join(', ') || 'None'}. Original fields remain in the import receipt.</p>{!preview.available && <p role="status">{preview.unavailable_reason}</p>}<Button disabled={busy || !preview.available} onClick={() => void commit()}>Import reviewed record</Button></section>}
    <section aria-label="Import history"><h2>Import history</h2>{receipts.length === 0 && <p>No imports yet.</p>}{receipts.map(item => <p key={item.request_id}><a href={item.source_link}>Open {item.kind}</a> · <a href={`#/capabilities/knowledge/capture?capture=${item.capture_id}`}>Original capture</a></p>)}<Button disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous imports</Button><Button disabled={busy || next === null} onClick={() => setOffset(next!)}>Next imports</Button></section>
  </main>
}
