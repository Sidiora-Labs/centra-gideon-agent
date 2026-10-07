import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput, Select, Checkbox } from '../../../shared/ui/forms'

type Account = { id: string; name: string; kind: string; owner_email: string }
type Case = { id: string; revision: number; state: string }
type Draft = { id: string; revision: number; sender: string; to: string[]; subject: string; body: string; content_sha256: string; state: string; provider_acceptance: string; delivery: string; verification?: { message_external_id: string; links: { url: string; opened: boolean }[] } }
type Result = { case: Case; draft: Draft | null; verification?: { reply_correlated: boolean; manual_code_matched: boolean; removal_confirmed: boolean; links_opened: boolean } }

export function WhitepagesBrokerPanel({ brokerCase, onChanged }: { brokerCase: Case; onChanged: (row: Case) => void }) {
  const base = `/api/capabilities/wellbeing/privacy/broker-cases/${brokerCase.id}/providers/whitepages`
  const [accounts, setAccounts] = useState<Account[]>([]), [accountId, setAccountId] = useState('')
  const [fullName, setFullName] = useState(''), [contactEmail, setContactEmail] = useState(''), [profileUrl, setProfileUrl] = useState(''), [jurisdiction, setJurisdiction] = useState('US-OTHER')
  const [draft, setDraft] = useState<Draft | null>(null), [approve, setApprove] = useState(false), [confirmSend, setConfirmSend] = useState(false), [manualCode, setManualCode] = useState('')
  const [verification, setVerification] = useState<Result['verification']>(), [busy, setBusy] = useState(false), [error, setError] = useState('')
  useEffect(() => { let active = true; Promise.all([requestJson<{accounts: Account[]}>('/api/capabilities/communications/mirror/accounts'), requestJson<Result>(base+`?revision=${brokerCase.revision}`)]).then(([value,status]) => { if (!active) return; const remote=value.accounts.filter(row=>row.kind==='imap'); setAccounts(remote); setAccountId(remote[0]?.id || ''); setContactEmail(remote[0]?.owner_email || ''); setDraft(status.draft); setVerification(status.verification) }).catch(reason => active && setError(String(reason))); return () => { active=false } }, [base, brokerCase.revision])
  const run = async (work: () => Promise<Result>) => { setBusy(true); setError(''); try { const value=await work(); setDraft(value.draft); setVerification(value.verification); onChanged(value.case); return value } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); return undefined } finally { setBusy(false) } }
  return <section aria-label="Whitepages approved email" className="space-y-3 rounded-lg bg-surface-container p-l">
    <h4>Whitepages approved email</h4><p>Preparation creates a draft only. Sending requires exact-content approval and separate confirmation. SMTP acceptance still leaves delivery uncertain.</p>
    {error && <p role="alert">{error}</p>}
    {!draft && <div className="grid gap-m sm:grid-cols-2">
      <label>Sending account<Select value={accountId} onChange={next => { const id=next; setAccountId(id); setContactEmail(accounts.find(row=>row.id===id)?.owner_email || '') }} ariaLabel={"Sending account"} options={[{ value: "", label: "Select connected account" }, ...accounts.map(row => ({ value: row.id, label: row.name + " — " + row.owner_email }))]} /></label>
      <Field label="Whitepages full name"><TextInput value={fullName} onChange={setFullName} required /></Field>
      <Field label="Whitepages contact email"><TextInput value={contactEmail} onChange={setContactEmail} required /></Field>
      <Field label="Whitepages listing URL"><TextInput value={profileUrl} onChange={setProfileUrl} required /></Field>
      <label>Jurisdiction<Select value={jurisdiction} onChange={next => setJurisdiction(next)} ariaLabel={"Jurisdiction"} options={[{ value: "US-OTHER", label: "US other" }, { value: "US-CA", label: "California" }]} /></label>
      <Button disabled={busy || !accountId || !fullName || !contactEmail || !profileUrl} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => await requestJson<Result>(base+'/prepare','POST',{request_id:crypto.randomUUID(),revision:brokerCase.revision,account_id:accountId,full_name:fullName,contact_email:contactEmail,profile_url:profileUrl,jurisdiction}))}>Prepare Whitepages email</Button>
    </div>}
    {draft && <article className="space-y-2"><h5>Exact email</h5><p>From: {draft.sender}</p><p>To: {draft.to.join(', ')}</p><p>Subject: {draft.subject}</p><pre className="whitespace-pre-wrap">{draft.body}</pre><p>Content SHA-256: {draft.content_sha256}</p><p>Draft state: {draft.state}; provider acceptance: {draft.provider_acceptance}; delivery: {draft.delivery}</p>
      {draft.state==='draft' && <><label><Checkbox checked={approve} onChange={next => setApprove(next)} ariaLabel={" I approve this exact Whitepages recipient and content"} /> I approve this exact Whitepages recipient and content</label><Button disabled={busy || !approve} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => await requestJson<Result>(base+'/approve','POST',{revision:brokerCase.revision,draft_revision:draft.revision,content_sha256:draft.content_sha256,confirm_exact:true}))}>Approve exact Whitepages email</Button></>}
      {draft.state==='approved' && <><label><Checkbox checked={confirmSend} onChange={next => setConfirmSend(next)} ariaLabel={" Dispatch this approved Whitepages email"} /> Dispatch this approved Whitepages email</label><Button disabled={busy || !confirmSend} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => await requestJson<Result>(base+'/send','POST',{request_id:crypto.randomUUID(),revision:brokerCase.revision,draft_revision:draft.revision,content_sha256:draft.content_sha256,confirm_send:true}))}>Send approved Whitepages email</Button></>}
      {['accepted','uncertain','verified'].includes(draft.state) && <><Field label="Manual verification code (optional)"><TextInput value={manualCode} onChange={setManualCode} /></Field><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void run(async () => await requestJson<Result>(base+'/correlate','POST',{revision:brokerCase.revision,...(manualCode?{manual_code:manualCode}:{})}))}>Correlate ingested reply</Button></>}
      {draft.verification && <div><p>Reply: {draft.verification.message_external_id}</p>{draft.verification.links.map(link=><p key={link.url}>Unopened link: {link.url} ({link.opened?'opened':'not opened'})</p>)}</div>}
      {verification && <p role="status">Reply correlated: {String(verification.reply_correlated)}; manual code matched: {String(verification.manual_code_matched)}; removal confirmed: {String(verification.removal_confirmed)}; links opened: {String(verification.links_opened)}</p>}
    </article>}
  </section>
}
