import { useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Row = { external_id: string; body: string; direction: string | null; person_id: string | null; eligible: boolean; attachments: { name: string; content_type: string; size: number | null }[] }
type Preview = { source_digest: string; review_token: string; rows: Row[]; coverage: string; qualification: string }
const base = '/api/capabilities/communications/signal-archive'
const style = 'block h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'

export function SignalArchivePanel() {
  const [account, setAccount] = useState('')
  const [content, setContent] = useState('')
  const [key, setKey] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [history, setHistory] = useState<Row[]>([])
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const data = { source_account_id: account, content_base64: content, key }
  async function run(operation: () => Promise<void>) {
    setBusy(true); setError('')
    try { await operation() } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) } finally { setBusy(false) }
  }
  function choose(file?: File) {
    setPreview(null); setStatus(''); setContent('')
    if (!file) return
    if (file.size > 33554432) { setError('Encrypted archive exceeds 32 MiB'); return }
    void run(async () => {
      const encoded = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader()
        reader.onerror = () => reject(new Error('Could not read encrypted Signal archive'))
        reader.onload = () => resolve(String(reader.result).split(',')[1] || '')
        reader.readAsDataURL(file)
      })
      setContent(encoded)
    })
  }
  return <section className="space-y-l" aria-label="Encrypted Signal archive">
    <h2 data-type="title-m">Encrypted Signal archive</h2>
    <p>Import an owned Signal Desktop SQLCipher-4 database with its explicitly supplied 64-character key. Gideon authenticates every encrypted page, never reads Signal Desktop directly, and never stores the key or decrypted database.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {status && <p role="status" className="rounded-lg bg-primary-container p-m text-on-primary-container">{status}</p>}
    <label className="block">Signal account label<input className={style} value={account} maxLength={100} onChange={event => { setAccount(event.target.value); setPreview(null) }} /></label>
    <label className="block">Encrypted Signal SQLite file<input className="block min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" type="file" accept=".db,.sqlite,.sqlite3" disabled={busy} onChange={event => choose(event.target.files?.[0])} /></label>
    <label className="block">Transient SQLCipher key<input className={style} type="password" autoComplete="off" value={key} maxLength={64} onChange={event => { setKey(event.target.value); setPreview(null) }} /></label>
    <Button disabled={busy || !account || !content || key.length !== 64} onClick={() => void run(async () => { setPreview(await requestJson<Preview>(base + '/preview', 'POST', data)); setStatus('') })}>Preview encrypted archive</Button>
    {preview && <div className="space-y-2"><p>{preview.rows.length} authenticated Signal messages; {preview.rows.filter(row => row.eligible).length} match existing people. Coverage: {preview.coverage}.</p>
      {preview.rows.map(row => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={row.external_id}><p>{row.external_id} · {row.direction || 'unsupported event'} · {row.eligible ? 'linked person' : 'history only'}</p><p>{row.body || '(Text unavailable)'}</p>{row.attachments.length > 0 && <p>{row.attachments.length} attachment reference(s)</p>}</article>)}
      <Button disabled={busy} onClick={() => void run(async () => { const result = await requestJson<{ receipt: { inserted: number; linked: number }; created: boolean }>(base + '/commit', 'POST', { ...data, source_digest: preview.source_digest, review_token: preview.review_token }); setKey(''); setPreview(null); setStatus(`${result.created ? 'Imported' : 'Already imported'} ${result.receipt.inserted} Signal messages; ${result.receipt.linked} linked relationship observations.`) })}>Commit Signal import</Button>
    </div>}
    <Button variant="secondary" disabled={busy || !account} onClick={() => void run(async () => setHistory((await requestJson<{ messages: Row[] }>(`${base}/history?source_account_id=${encodeURIComponent(account)}`)).messages))}>Read Signal history</Button>
    {history.map(row => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={row.external_id}><p>Stored: {row.external_id}</p><p>{row.body || '(Text unavailable)'}</p></article>)}
  </section>
}
