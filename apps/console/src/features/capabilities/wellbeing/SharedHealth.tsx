import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextArea } from '../../../shared/ui/forms'

type Status = { state: string; sha256?: string; bytes?: number; records?: number; store_id?: string; error?: string }
type Preview = { preview_id: string; store_id: string; counts: { create: number; correct: number; unchanged: number }; records: { id: string; action: string }[] }
const base = '/api/capabilities/wellbeing/shared'

export default function SharedHealth() {
  const [status, setStatus] = useState<Status | null>(null), [content, setContent] = useState(''), [preview, setPreview] = useState<Preview | null>(null), [filePreview, setFilePreview] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [result, setResult] = useState('')
  const request = useRef({ fingerprint: '', id: '' })
  async function refresh() { setStatus(await requestJson<Status>(base)) }
  useEffect(() => { void refresh().catch(err => setError(String(err))) }, [])
  async function act(operation: string) {
    if (busy) return
    setBusy(true); setError(''); setResult('')
    try {
      if (operation === 'refresh') { await refresh(); setPreview(null) }
      else if (operation === 'preview' || operation === 'file/preview') {
        setPreview(null)
        const next = await requestJson<Preview>(base + '/' + operation, 'POST', operation === 'preview' ? { content } : {})
        setPreview(next); setFilePreview(operation === 'file/preview')
      } else {
        const payload = operation === 'publish' ? { expected_sha256: status?.sha256 ?? null } : { preview_id: preview!.preview_id, ...(filePreview ? {} : { content }) }
        const fingerprint = JSON.stringify([operation, payload])
        if (request.current.fingerprint !== fingerprint) request.current = { fingerprint, id: crypto.randomUUID() }
        const response = await requestJson<{ counts?: Preview['counts']; records?: number | unknown[] }>(base + '/' + operation, 'POST', { ...payload, request_id: request.current.id })
        const message = (operation === 'publish' ? `Published ${response.records} canonical measurements.` : `Imported ${response.counts!.create} new and ${response.counts!.correct} corrected records; ${response.counts!.unchanged} unchanged.`)
        setPreview(null); request.current = { fingerprint: '', id: '' }; await refresh(); setResult(message)
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <main style={{ maxWidth: 'var(--content-width)' }} className="mx-auto w-full space-y-2xl px-l py-2xl text-on-surface"><h2 data-type="title-m">Native shared health file</h2><p>Exchange version 1 body weight and blood pressure records with a native client. Gideon keeps canonical records and correction history. The fixed file is shared/wellbeing.json in this runtime home; cloud synchronization is not configured here.</p>{error && <p role="alert">{error}</p>}{result && <p role="status">{result}</p>}
    <section aria-label="Shared file status" className="space-y-l rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Local file</h2><p>State: {status?.state ?? 'loading'}</p>{status?.error && <p>{status.error}</p>}{status?.sha256 && <p className="break-all">SHA-256: {status.sha256}</p>}{status?.state === 'ready' && <p>{status.records} shared records · {status.bytes} bytes</p>}<div className="flex flex-wrap gap-m"><Button disabled={busy} onClick={() => act('refresh')}>Refresh shared file</Button><Button disabled={busy || status?.state !== 'ready'} onClick={() => act('file/preview')}>Preview local shared file</Button><Button disabled={busy || !status || !['missing', 'ready'].includes(status.state)} onClick={() => act('publish')}>Publish canonical measurements</Button>{status?.state === 'ready' && <a className="underline" href={base + '/download'} download="wellbeing-shared.json">Download shared JSON</a>}</div></section>
    <section className="space-y-m"><h2 data-type="title-m">Import native document</h2><label>Native health JSON file<input className="block w-full text-sm text-on-surface-var file:mr-m file:rounded-md file:border-0 file:bg-surface-high file:px-m file:py-s file:text-on-surface" aria-label="Native health JSON file" type="file" accept=".json,application/json" disabled={busy} onChange={async event => { const file = event.target.files?.[0]; if (!file) return; try { setContent(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer())); setPreview(null) } catch (err) { setError(String(err)) } }} /></label><Field label="Native document JSON"><TextArea value={content} onChange={value => { setContent(value); setPreview(null) }} /></Field><Button disabled={busy || !content} onClick={() => act('preview')}>Preview uploaded document</Button></section>
    {preview && <section aria-label="Shared import preview" className="space-y-l rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Import preview</h2><p>{preview.counts.create} new · {preview.counts.correct} corrections · {preview.counts.unchanged} unchanged</p><p>Source store: {preview.store_id}</p><p>Missing rows never delete canonical records. Conflicting source revisions or local changes stop the whole import.</p><Button disabled={busy} onClick={() => act(filePreview ? 'file/commit' : 'commit')}>Commit shared import</Button></section>}
  </main>
}
