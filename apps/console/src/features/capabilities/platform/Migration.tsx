import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

interface Preview { format: string; archive_digest: string; review_token: string; generated_at: string; coverage: { supported: string[]; unsupported: string }; records: { source_id: string; domain: string; name: string }[] }
interface Receipt { archive_digest: string; committed_at: string; domains: Record<string, number> }

export default function Migration({ baseUrl = '' }: { baseUrl?: string }) {
  const url = `${baseUrl}/api/capabilities/platform/migration`
  const [content, setContent] = useState(''); const [preview, setPreview] = useState<Preview>(); const [receipts, setReceipts] = useState<Receipt[]>([]); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const load = async () => { try { setReceipts((await requestJson<{ receipts: Receipt[] }>(url)).receipts) } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void load() }, [baseUrl])
  const choose = async (file?: File) => { setPreview(undefined); setError(''); if (!file) { setContent(''); return } const bytes = new Uint8Array(await file.arrayBuffer()); let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte); setContent(btoa(binary)) }
  const act = async (action: 'preview' | 'commit') => { setBusy(true); setError(''); try { const body = { action, format: 'portos_snapshot_v1', content, ...(action === 'commit' ? { archive_digest: preview?.archive_digest, review_token: preview?.review_token } : {}) }; const result = await requestJson<{ preview?: Preview; receipt?: Receipt }>(url, 'POST', body); if (result.preview) setPreview(result.preview); if (result.receipt) { setPreview(undefined); setContent(''); await load() } } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  return <section aria-label="Archive migration" className="space-y-m"><h2>Legacy archive migration</h2><p>Verified imports currently cover people, memories, and links. Archives containing any other domain are refused.</p>{error && <p role="alert">{error}</p>}<label>Snapshot archive<input aria-label="Snapshot archive" type="file" accept=".tar.gz,.tgz,application/gzip" onChange={event => void choose(event.target.files?.[0])} /></label><Button disabled={busy || !content} onClick={() => void act('preview')}>Preview verified archive</Button>{preview && <div><p>{preview.records.length} records verified from {preview.generated_at}</p><p>Domains: {preview.coverage.supported.join(', ')}</p><ul>{preview.records.map(row => <li key={`${row.domain}:${row.source_id}`}>{row.domain}: {row.name}</li>)}</ul><Button disabled={busy} onClick={() => void act('commit')}>Import reviewed records</Button></div>}<h3>Import receipts</h3>{receipts.length ? <ul>{receipts.map(row => <li key={row.archive_digest}>{Object.entries(row.domains).map(([domain, count]) => `${count} ${domain}`).join(', ')} · {row.committed_at}</li>)}</ul> : <p>No archive imports recorded.</p>}</section>
}
