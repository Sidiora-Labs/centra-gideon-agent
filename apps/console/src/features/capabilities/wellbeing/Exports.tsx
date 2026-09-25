import { useEffect, useRef, useState } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Preview = { schema: string; version: number; counts: Record<string, number>; attachment_count: number; attachment_bytes: number; export_bytes: number; omitted: string[] }
type Archive = { id: string; schema: string; version: number; generated_at: string; sha256: string; bytes: number; counts: Record<string, number>; attachment_count: number; omitted: string[] }
const base = '/api/capabilities/wellbeing/exports'

export default function Exports() {
  const { query, setQuery } = useHashRoute('capabilities')
  const [preview, setPreview] = useState<Preview | null>(null), [archives, setArchives] = useState<Archive[]>([]), [selected, setSelected] = useState<Archive | null>(null)
  const [busy, setBusy] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const request = useRef('')
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setSelected(null)
    Promise.all([requestJson<Preview>(base + '/preview'), requestJson<{ exports: Archive[] }>(base), query.export ? requestJson<Archive>(`${base}/${query.export}`) : Promise.resolve(null)])
      .then(([next, list, archive]) => { if (active) { setPreview(next); setArchives(list.exports); setSelected(archive) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [query.export, generation])
  async function create() {
    if (busy) return
    request.current ||= crypto.randomUUID(); setBusy(true); setError('')
    try { const archive = await requestJson<Archive>(base, 'POST', { request_id: request.current }); request.current = ''; setQuery({ export: archive.id }); setGeneration(n => n + 1) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <main className="h-full overflow-auto p-4 sm:p-6 space-y-6 text-on-surface"><h1 data-type="headline-s">Versioned wellbeing exports</h1><p>Save a point-in-time JSON archive of records, revision history and referenced original attachments. Existing archives remain unchanged when records change. This export does not restore data or include external calendar triggers and inbox items.</p>{error && <p role="alert">{error}</p>}<Button onClick={() => setGeneration(n => n + 1)} disabled={loading}>Refresh export preview</Button>{loading && <p role="status">Loading export data…</p>}
    {preview && <section aria-label="Export preview"><h2 data-type="title-m">Current snapshot preview</h2><p>Schema {preview.schema} · version {preview.version}</p><dl>{Object.entries(preview.counts).map(([domain, count]) => <div key={domain}><dt>{domain}</dt><dd>{count} history and provenance records</dd></div>)}</dl><p>{preview.attachment_count} source attachments · {preview.attachment_bytes} source bytes · {preview.export_bytes} archive bytes</p><p>Excluded: {preview.omitted.join(', ')}</p><Button onClick={create} disabled={loading} loading={busy}>Create immutable export</Button></section>}
    <section aria-label="Saved exports"><h2 data-type="title-m">Saved exports</h2>{archives.map(archive => <button key={archive.id} className="block rounded-lg bg-surface-container p-3 my-2 text-left" onClick={() => setQuery({ export: archive.id })}>Export {archive.generated_at} · {archive.bytes} bytes</button>)}{!loading && !archives.length && <p>No saved exports.</p>}</section>
    {selected && <section aria-label="Selected export"><h2 data-type="title-m">Selected immutable export</h2><p>Created {selected.generated_at} · schema version {selected.version}</p><p className="break-all">SHA-256: {selected.sha256}</p><p>{selected.attachment_count} attachments · {selected.bytes} bytes</p><a className="underline" href={`${base}/${selected.id}/download`} download={`wellbeing-${selected.id}.json`}>Download selected JSON export</a></section>}
  </main>
}
