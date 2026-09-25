import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
const nativeControl = 'block h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'

type Account = { id: string; name: string; kind: string; owner_email: string; revision: number; sync: { state: string; coverage: string; evidence_status?: string; error?: string } }
type Message = { external_id: string; subject: string; body: string; direction: string; attachments: { filename: string; size: number }[] }
const base = '/api/capabilities/communications/mirror'
const initial = { name: '', kind: 'maildir', owner_email: '', alias: 'custom', host: '', username: '', credential_ref: '', auth_mode: 'password', inbox_folder: 'INBOX', sent_folder: 'Sent' }
export function MirrorPanel() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [form, setForm] = useState(initial)
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('mirror') || '')
  const selectAccount = (id: string) => { const params = new URLSearchParams(location.hash.split('?')[1] || ''); if (id) params.set('mirror', id); else params.delete('mirror'); location.hash = '#/capabilities/communications?' + params.toString(); setSelected(id) }
  useEffect(() => { const update = () => { setSelected(new URLSearchParams(location.hash.split('?')[1] || '').get('mirror') || ''); setMessages([]) }; addEventListener('hashchange', update); return () => removeEventListener('hashchange', update) }, [])
  const [content, setContent] = useState('')
  const [folder, setFolder] = useState('INBOX')
  const [messages, setMessages] = useState<Message[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [adapters, setAdapters] = useState<{ adapter: string; implemented: boolean; qualification: string }[]>([])
  const account = accounts.find(row => row.id === selected)
  const reload = async () => setAccounts((await requestJson<{ accounts: Account[] }>(base + '/accounts')).accounts)
  useEffect(() => { let active = true; Promise.all([requestJson<{ accounts: Account[] }>(base + '/accounts'), requestJson<{ adapters: typeof adapters }>(base + '/capabilities')]).then(([a, c]) => { if (active) { setAccounts(a.accounts); setAdapters(c.adapters) } }).catch(e => { if (active) setError(String(e)) }); return () => { active = false } }, [])
  const run = async (work: () => Promise<void>) => { setBusy(true); setError(''); try { await work() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  const readMessages = async (id: string) => setMessages((await requestJson<{ messages: Message[] }>(`${base}/accounts/${id}/messages`)).messages)
  return <section aria-label="Account mirrors" className="space-y-l">
    <h2 data-type="title-m">Account mirrors</h2>
    <p>Read mail into this runtime. Local archives cover only uploaded data. Remote account access requires an existing credential connection.</p>
    <details><summary>Adapter coverage</summary>{adapters.map(row => <p key={row.adapter}>{row.adapter}: {row.implemented ? 'available' : 'unavailable'} — {row.qualification}</p>)}</details>
    {error && <p role="alert" className="rounded-lg bg-danger-container p-m text-on-danger-container">{error}</p>}
    <form className="space-y-m rounded-lg bg-surface-container px-l py-l" onSubmit={e => { e.preventDefault(); void run(async () => { const data = await requestJson<{ account: Account }>(base + '/accounts', 'POST', form); await reload(); selectAccount(data.account.id); setMessages([]); setForm(initial) }) }}>
      <label className="block">Account name<input className={nativeControl} required value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label>
      <label className="block">Owner email<input className={nativeControl} required type="email" value={form.owner_email} onChange={e => setForm({ ...form, owner_email: e.target.value })} /></label>
      <label className="block">Source type<select className={nativeControl} value={form.kind} onChange={e => setForm({ ...initial, name: form.name, owner_email: form.owner_email, kind: e.target.value })}><option value="maildir">Maildir</option><option value="mbox">Mbox</option><option value="imap">IMAP TLS</option></select></label>
      {form.kind === 'imap' && <fieldset><legend>Existing remote connection</legend>
        <label className="block">Provider<select className={nativeControl} value={form.alias} onChange={e => setForm({ ...form, alias: e.target.value })}><option value="custom">Custom</option><option value="gmail">Gmail</option><option value="outlook">Outlook</option></select></label>
        {(['host', 'username', 'credential_ref', 'inbox_folder', 'sent_folder'] as const).map(key => <label className="block" key={key}>{key}<input className={nativeControl} required value={form[key]} onChange={e => setForm({ ...form, [key]: e.target.value })} /></label>)}
        <label className="block">Authentication<select className={nativeControl} value={form.auth_mode} onChange={e => setForm({ ...form, auth_mode: e.target.value })}><option value="password">Password reference</option><option value="xoauth2">OAuth token reference</option></select></label>
      </fieldset>}
      <Button disabled={busy} type="submit">Create mail account</Button>
    </form>
    <label className="block">Mail account<select className={nativeControl} disabled={busy} value={selected} onChange={e => { const id = e.target.value; selectAccount(id); setMessages([]); if (id) void run(() => readMessages(id)) }}><option value="">Select account</option>{accounts.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}</select></label>
    {account && <div className="space-y-2">
      <p>Sync: {account.sync.state}; coverage: {account.sync.coverage}; evidence: {account.sync.evidence_status || 'none'}</p>
      {account.sync.error && <p>{account.sync.error}</p>}
      {account.kind !== 'imap' && <div><label className="block">Mail source content<textarea className="block min-h-28 w-full resize-y rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" value={content} onChange={e => setContent(e.target.value)} /></label><label className="block">Message folder<select className={nativeControl} value={folder} onChange={e => setFolder(e.target.value)}><option>INBOX</option><option>Sent</option></select></label><Button disabled={busy || !content} onClick={() => void run(async () => { await requestJson(`${base}/accounts/${selected}/upload`, 'POST', { content, folder }); setContent('') })}>Upload mail source</Button></div>}
      <Button disabled={busy} onClick={() => void run(async () => { try { await requestJson(`${base}/accounts/${selected}/sync`, 'POST', {}) } finally { await reload(); await readMessages(selected) } })}>Sync mail account</Button>
      <Button disabled={busy} onClick={() => void run(() => readMessages(selected))}>Read mirrored messages</Button>
      {messages.map(message => <article className="rounded-lg border border-outline-variant/20 bg-surface px-l py-m" key={message.external_id}><h3 data-type="title-s">{message.subject || '(No subject)'}</h3><p>{message.direction}</p><pre className="whitespace-pre-wrap">{message.body}</pre>{message.attachments.map((file, index) => <p key={index}>Attachment: {file.filename} ({file.size} bytes)</p>)}</article>)}
    </div>}
  </section>
}
