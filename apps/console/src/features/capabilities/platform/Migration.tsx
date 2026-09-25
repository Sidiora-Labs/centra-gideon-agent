import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

const MAX_ARCHIVE_BYTES = 16 * 1024 * 1024
interface Preview { format: string; archive_digest: string; review_token: string; generated_at: string; coverage: { supported: string[]; unsupported: string }; records: { source_id: string; domain: string; name: string }[]; commit_groups?: { id: string; domains: string[] }[]; completion_policy?: string }
interface Receipt { archive_digest: string; committed_at: string; domains: Record<string, number>; status?: string }

export default function Migration({ baseUrl = '' }: { baseUrl?: string }) {
  const url = `${baseUrl}/api/capabilities/platform/migration`
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<Preview>()
  const [receipts, setReceipts] = useState<Receipt[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async () => { try { setReceipts((await requestJson<{ receipts: Receipt[] }>(url)).receipts) } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void load() }, [baseUrl])
  const choose = async (file?: File) => {
    setPreview(undefined); setContent(''); setError('')
    if (!file) return
    if (file.size === 0 || file.size > MAX_ARCHIVE_BYTES) { setError('Snapshot archive must be between 1 byte and 16 MiB.'); return }
    const bytes = new Uint8Array(await file.arrayBuffer())
    let binary = ''
    for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768))
    setContent(btoa(binary))
  }
  const act = async (action: 'preview' | 'commit') => {
    if (busy || !content || (action === 'commit' && !preview)) return
    setBusy(true); setError('')
    try {
      const body = { action, format: 'legacy_snapshot_v1', content, ...(action === 'commit' ? { archive_digest: preview!.archive_digest, review_token: preview!.review_token } : {}) }
      const result = await requestJson<{ preview?: Preview; receipt?: Receipt }>(url, 'POST', body)
      if (result.preview) setPreview(result.preview)
      if (result.receipt) { setPreview(undefined); setContent(''); await load() }
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Archive migration" className="grid gap-l">
    <h2 data-type="title-m">Legacy archive migration</h2>
    <p>Verified imports currently cover version-1 people, projects, admin actions, tracked threads, ideas, journals, memories, links, buckets, inbox captures, and song sheets with verified attachments. Mixed song archives commit as independent canonical-record and song groups after full-plan validation; when inbox is present, they commit as independent canonical-record, immutable inbox, and song groups. Completed groups are retained, unfinished groups resume only from the exact reviewed archive, and partial completion is reported without claiming archive-wide rollback or deleting immutable inbox events. Settings, logs, relational dumps, unrelated media, and reference files remain refused.</p>
    {error && <p role="alert">{error}</p>}
    <label className="grid gap-xs text-sm">Snapshot archive<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Snapshot archive" type="file" accept=".tar.gz,.tgz,application/gzip" disabled={busy} onChange={event => void choose(event.target.files?.[0])} /></label>
    <Button disabled={busy || !content} onClick={() => void act('preview')}>Preview verified archive</Button>
    {preview && <div><p>{preview.records.length} records verified from {preview.generated_at}</p><p>Domains: {preview.coverage.supported.join(', ')}</p><p>Unsupported: {preview.coverage.unsupported}.</p>{preview.commit_groups && <div><p>{preview.completion_policy}</p><ul>{preview.commit_groups.map(group => <li key={group.id}>{group.id}: {group.domains.join(', ')}</li>)}</ul></div>}<ul>{preview.records.map(row => <li key={`${row.domain}:${row.source_id}`}>{row.domain}: {row.name}</li>)}</ul><Button disabled={busy} onClick={() => void act('commit')}>Import reviewed records</Button></div>}
    <h3 data-type="headline-s">Import receipts</h3>
    {receipts.length ? <ul>{receipts.map(row => <li key={row.archive_digest}>{row.status ? `${row.status} · ` : ''}{Object.entries(row.domains).map(([domain, count]) => `${count} ${domain}`).join(', ')} · {row.committed_at}</li>)}</ul> : <p>No archive imports recorded.</p>}
  </section>
}
