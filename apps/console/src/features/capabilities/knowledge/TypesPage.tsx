import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import { Field, Select, TextArea } from '../../../shared/ui/forms'

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
  return <main className="h-full"><ListScaffold title="Classify captures" right={<a data-type="label-m" className="inline-flex min-h-10 items-center rounded-pill bg-surface-high px-xl text-on-surface hover:bg-surface-highest" href="#/capabilities/knowledge/capture">Open capture inbox</a>} bodyClassName="mx-auto px-l py-l"><div className="flex flex-col gap-xl">
    <p data-type="body-m" className="max-w-[42rem] text-on-surface-var">Review original fields and import into the existing destination. Edit saved records in their destination.</p>
    {error && <p role="alert" className="border-l-2 border-danger/40 pl-s text-danger">{error}</p>}
    <div className="grid gap-l lg:grid-cols-2"><section className="flex flex-col gap-m rounded-lg bg-surface-container p-l"><Field label="Original capture"><Select ariaLabel="Original capture" value={captureId} surface="high" disabled={busy} onChange={value => { setCaptureId(value); reset() }} options={[{value:'',label:'Choose a capture'},...captures.map(item=>({value:item.id,label:(item.transcript || item.text || 'Voice capture').slice(0,100)}))]} /></Field>
    {selected && <blockquote className="whitespace-pre-wrap rounded-lg bg-surface-high p-m">{selected.text || selected.transcript}</blockquote>}
    <Field label="Record type"><Select ariaLabel="Record type" value={kind} surface="high" disabled={busy} onChange={value => { setKind(value); setFields(JSON.stringify(samples[value], null, 2)); reset() }} options={Object.keys(samples).map(name=>({value:name,label:name}))} /></Field>
    <Field label="Original fields (JSON)"><TextArea ariaLabel="Original fields (JSON)" rows={10} mono surface="high" value={fields} disabled={busy} onChange={value => { setFields(value); reset() }} /></Field>
    <Button disabled={!captureId || busy} onClick={() => void review()}>Review mapping</Button>
    {preview && <section aria-label="Mapping review" className="flex flex-col gap-m rounded-lg bg-surface-high p-m"><h2 data-type="title-m">Mapping review</h2><p>Destination: {preview.destination}</p><pre className="whitespace-pre-wrap break-words">{JSON.stringify(preview.mapped_fields, null, 2)}</pre><p>Unmapped original fields: {preview.unsupported_fields.join(', ') || 'None'}. Original fields remain in the import receipt.</p>{!preview.available && <p role="status">{preview.unavailable_reason}</p>}<Button disabled={busy || !preview.available} onClick={() => void commit()}>Import reviewed record</Button></section>}</section>
    <section aria-label="Import history" className="rounded-lg bg-surface-container p-l"><h2 data-type="title-m" className="mb-m">Import history</h2>{receipts.length === 0 && <p>No imports yet.</p>}{receipts.map(item => <p className="border-b border-outline-variant/20 py-s last:border-0" key={item.request_id}><a className="text-primary underline" href={item.source_link}>Open {item.kind}</a> · <a className="text-primary underline" href={`#/capabilities/knowledge/capture?capture=${item.capture_id}`}>Original capture</a></p>)}<div className="flex gap-s pt-m"><Button variant="secondary" disabled={busy || offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous imports</Button><Button variant="secondary" disabled={busy || next === null} onClick={() => setOffset(next!)}>Next imports</Button></div></section></div>
  </div></ListScaffold></main>
}
