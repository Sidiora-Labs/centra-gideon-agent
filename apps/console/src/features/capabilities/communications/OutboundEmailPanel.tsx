import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'

type Account = { id: string; name: string; kind: string; owner_email: string }
type Draft = { id: string; account_id: string; sender: string; to: string[]; subject: string; body: string; content_sha256: string; state: string; revision: number; provider_acceptance: string; delivery: string; verification?: { message_external_id: string; source_digest: string; links: { url: string; opened: boolean }[] } }
const base = '/api/capabilities/communications/outbound-email/drafts'

export function OutboundEmailPanel() {
  const [accounts, setAccounts] = useState<Account[]>([]), [drafts, setDrafts] = useState<Draft[]>([])
  const [accountId, setAccountId] = useState(''), [to, setTo] = useState(''), [subject, setSubject] = useState(''), [body, setBody] = useState('')
  const [selected, setSelected] = useState<Draft | null>(null), [approveExact, setApproveExact] = useState(false), [confirmSend, setConfirmSend] = useState(false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const reload = async (selectedId?: string) => { const result = await requestJson<{ drafts: Draft[] }>(base); setDrafts(result.drafts); if (selectedId) setSelected(result.drafts.find(row => row.id === selectedId) || null) }
  useEffect(() => { let active = true; Promise.all([requestJson<{ accounts: Account[] }>('/api/capabilities/communications/mirror/accounts'), requestJson<{ drafts: Draft[] }>(base)]).then(([a, d]) => { if (active) { const remote = a.accounts.filter(row => row.kind === 'imap'); setAccounts(remote); setAccountId(remote[0]?.id || ''); setDrafts(d.drafts) } }).catch(e => active && setError(String(e))); return () => { active = false } }, [])
  const run = async (work: () => Promise<Draft>) => { setBusy(true); setError(''); try { const row = await work(); setSelected(row); setDrafts(current => current.some(item => item.id === row.id) ? current.map(item => item.id === row.id ? row : item) : [row, ...current]); await reload(row.id) } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  return <section aria-label="Approved outbound email" className="space-y-3 rounded border p-4">
    <h2>Approved outbound email</h2><p>Email uses the selected account’s fixed sender and credential. SMTP acceptance does not prove delivery.</p>
    {error && <p role="alert">{error}</p>}
    <form className="space-y-2" onSubmit={e => { e.preventDefault(); void run(async () => (await requestJson<{draft: Draft}>(base, 'POST', { request_key: crypto.randomUUID(), account_id: accountId, to: to.split(',').map(v => v.trim()).filter(Boolean), subject, body })).draft) }}>
      <label>Sending account<select required value={accountId} onChange={e => setAccountId(e.target.value)}><option value="">Select connected account</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.name} — {a.owner_email}</option>)}</select></label>
      <label>Recipients<input required value={to} onChange={e => setTo(e.target.value)} /></label><label>Subject<input value={subject} onChange={e => setSubject(e.target.value)} /></label><label>Body<textarea required value={body} onChange={e => setBody(e.target.value)} /></label>
      <button disabled={busy || !accountId} type="submit">Create exact draft</button>
    </form>
    <label>Durable draft<select value={selected?.id || ''} onChange={e => setSelected(drafts.find(row => row.id === e.target.value) || null)}><option value="">Select draft</option>{drafts.map(row => <option key={row.id} value={row.id}>{row.subject || '(No subject)'} — {row.state}</option>)}</select></label>
    {selected && <article><h3>Exact content for approval</h3><p>From: {selected.sender}</p><p>To: {selected.to.join(', ')}</p><p>Subject: {selected.subject}</p><pre className="whitespace-pre-wrap">{selected.body}</pre><p>Content SHA-256: {selected.content_sha256}</p><p>Status: {selected.state}; provider acceptance: {selected.provider_acceptance}; delivery: {selected.delivery}</p>
      {selected.state === 'draft' && <><label><input type="checkbox" checked={approveExact} onChange={e => setApproveExact(e.target.checked)} /> I approve these exact recipients and body</label><button disabled={busy || !approveExact} onClick={() => void run(async () => (await requestJson<{draft: Draft}>(`${base}/${selected.id}/approve`, 'POST', { revision: selected.revision, content_sha256: selected.content_sha256, confirm_exact: true })).draft)}>Approve exact email</button></>}
      {selected.state === 'approved' && <><label><input type="checkbox" checked={confirmSend} onChange={e => setConfirmSend(e.target.checked)} /> Dispatch this approved email</label><button disabled={busy || !confirmSend} onClick={() => void run(async () => (await requestJson<{draft: Draft}>(`${base}/${selected.id}/send`, 'POST', { revision: selected.revision, content_sha256: selected.content_sha256, confirm_send: true })).draft)}>Send approved email</button></>}
      {['accepted','uncertain','verified'].includes(selected.state) && <button disabled={busy} onClick={() => void run(async () => (await requestJson<{draft: Draft}>(`${base}/${selected.id}/correlate`, 'POST', {})).draft)}>Check ingested verification replies</button>}
      {selected.verification && <div><p>Verification inbox reference: {selected.verification.message_external_id} / {selected.verification.source_digest}</p>{selected.verification.links.map(link => <p key={link.url}>Unopened link: {link.url} ({link.opened ? 'opened' : 'not opened'})</p>)}</div>}
    </article>}
  </section>
}
