import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'

type Row = { external_id: string; body: string; direction: string | null; person_id: string | null; eligible: boolean; limitations: string[] }
type Preview = { source_digest: string; review_token: string; rows: Row[]; limits: string[] }
const base = '/api/capabilities/communications/desktop'
export function DesktopPanel() {
  const params = () => new URLSearchParams(location.hash.split('?')[1] || '')
  const [source, setSource] = useState(() => params().get('desktop_source') || 'imessage')
  const [account, setAccount] = useState(() => params().get('desktop_account') || '')
  const select = (nextSource: string, nextAccount: string) => { const query = params(); query.set('desktop_source', nextSource); query.set('desktop_account', nextAccount); location.hash = '#/capabilities/communications?' + query.toString(); setSource(nextSource); setAccount(nextAccount) }
  useEffect(() => { const update = () => { setSource(params().get('desktop_source') || 'imessage'); setAccount(params().get('desktop_account') || '') }; addEventListener('hashchange', update); return () => removeEventListener('hashchange', update) }, [])
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [receipt, setReceipt] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<Row[]>([])
  const data = { source, source_account_id: account, content_base64: content }
  const reset = () => { setPreview(null); setReceipt(''); setHistory([]) }
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  return <section aria-label="Desktop message imports" className="space-y-3 rounded border p-4">
    <h2>Desktop message imports</h2>
    <p>Upload a plain SQLite snapshot from iMessage or Signal Desktop. Encrypted Signal databases require a local export or decryption first. Imported snapshots do not prove complete conversation coverage.</p>
    {error && <p role="alert">{error}</p>}
    {receipt && <p role="status">{receipt}</p>}
    <label>Desktop source<select disabled={busy} value={source} onChange={e => { select(e.target.value, account); reset() }}><option value="imessage">iMessage</option><option value="signal">Signal Desktop</option></select></label>
    <label>Desktop source account<input disabled={busy} value={account} onChange={e => { select(source, e.target.value); reset() }} /></label>
    <label>SQLite snapshot file<input disabled={busy} type="file" accept=".db,.sqlite,.sqlite3" onChange={e => { const file = e.target.files?.[0]; reset(); setContent(''); if (!file) return; if (file.size > 8388608) { setError('Snapshot exceeds 8 MiB'); return } void run(async () => { const encoded = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error('Could not read snapshot')); reader.onload = () => resolve(String(reader.result).split(',')[1] || ''); reader.readAsDataURL(file) }); setContent(encoded) }) }} /></label>
    <button disabled={busy || !content || !account} onClick={() => void run(async () => { setPreview(await requestJson<Preview>(base + '/preview', 'POST', data)); setReceipt('') })}>Preview desktop snapshot</button>
    {preview && <div><p>{preview.rows.length} source messages; {preview.rows.filter(row => row.eligible).length} match people.</p>{preview.limits.map(limit => <p key={limit}>{limit}</p>)}{preview.rows.map(row => <article key={row.external_id}><p>{row.external_id}: {row.direction || 'unsupported event'}; {row.eligible ? 'linked to person' : 'history only'}</p><pre className="whitespace-pre-wrap">{row.body || '(Text unavailable)'}</pre>{row.limitations.map(limit => <p key={limit}>{limit}</p>)}</article>)}<button disabled={busy} onClick={() => void run(async () => { const result = await requestJson<{ receipt: { inserted: number; linked: number }; created: boolean }>(base + '/commit', 'POST', { ...data, source_digest: preview.source_digest, review_token: preview.review_token }); setReceipt(`${result.created ? 'Imported' : 'Already imported'}: ${result.receipt.inserted} messages; ${result.receipt.linked} relationship observations. Coverage remains unknown.`) })}>Commit desktop import</button></div>}
    <button disabled={busy || !account} onClick={() => void run(async () => { setHistory((await requestJson<{ messages: Row[] }>(`${base}/history?source=${encodeURIComponent(source)}&source_account_id=${encodeURIComponent(account)}`)).messages) })}>Read desktop history</button>
    {history.map(row => <article key={row.external_id}><p>Stored: {row.external_id}</p><pre className="whitespace-pre-wrap">{row.body || '(Text unavailable)'}</pre></article>)}
  </section>
}
