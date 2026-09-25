import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Entry = { start: number; end: number; quote: string; summary?: string; subject?: string; predicate?: string; value?: string; draft_id?: string; stale?: boolean; missing?: boolean }
type Proposal = { id: string; mode: string; base_work_revision: number; base_draft_id: string; artifact_id: string; artifact_version: number; accepted_revision: number | null; stale: boolean; missing: boolean; coverage: { start: number; end: number; total_characters: number }; outline: Entry[]; facts: Entry[] }
type Ledger = { revision: number; proposals: Proposal[]; outline: Entry[]; facts: Entry[]; conflicts: { subject: string; predicate: string; values: string[] }[] }
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
export default function Continuity({ id, revision, text, apiRoot }: { id: string; revision: number; text: string; apiRoot: string }) {
  const [ledger, setLedger] = useState<Ledger | null>(null)
  const [mode, setMode] = useState('authored')
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(Math.min(Array.from(text).length, 20000))
  const [summary, setSummary] = useState('')
  const [subject, setSubject] = useState('')
  const [predicate, setPredicate] = useState('')
  const [value, setValue] = useState('')
  const [instruction, setInstruction] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [exported, setExported] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const root = `${apiRoot}/${id}/continuity`
  const characters = Array.from(text)
  const quote = characters.slice(start, end).join('')
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Continuity request failed')
  useEffect(() => {
    let alive = true; setLedger(null); setError(''); setExported(''); setStart(0); setEnd(Math.min(Array.from(text).length, 20000)); setRequestId(crypto.randomUUID())
    requestJson<Ledger>(root).then(result => { if (alive) setLedger(result) }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [root, revision])
  function changed(action: () => void) { action(); setRequestId(crypto.randomUUID()) }
  async function propose() {
    setBusy(true); setError('')
    try {
      const anchor = { start, end, quote }
      await requestJson(`${root}/proposals`, 'POST', { request_id: requestId, work_revision: revision, start, end, mode, instruction,
        ...(mode === 'authored' ? { outline: summary.trim() ? [{ ...anchor, summary }] : [], facts: subject.trim() ? [{ ...anchor, subject, predicate, value }] : [] } : {}) })
      setLedger(await requestJson<Ledger>(root)); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function accept(proposal: Proposal) {
    setBusy(true); setError('')
    try { await requestJson(`${root}/proposals/${proposal.id}/accept`, 'POST', { revision: ledger!.revision, work_revision: revision }); setLedger(await requestJson<Ledger>(root)) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function exportLedger() { try { setExported(JSON.stringify(await requestJson(`${root}/export`), null, 2)) } catch (e) { fail(e) } }
  function evidence(entry: Entry, index: number) { return <li key={index}><p>{entry.summary || `${entry.subject} · ${entry.predicate} · ${entry.value}`}</p><blockquote className="whitespace-pre-wrap">{entry.quote}</blockquote><p>Source span {entry.start}–{entry.end}{entry.stale && ' · Earlier draft'}{entry.missing && ' · Source missing'}</p></li> }
  return <section aria-label="Reverse outline and continuity" className="space-y-3 rounded border border-outline p-3"><h2>Reverse outline and continuity</h2>
    <p>Review exact evidence from a saved draft before adding it to the ledger. Canon is unchanged. Positions count Unicode characters, including each emoji as one codepoint.</p>
    {error && <p role="alert">{error}</p>}{!ledger && !error && <p>Loading continuity…</p>}
    <label className="block">Extraction source<select className={control} value={mode} onChange={e => changed(() => setMode(e.target.value))}><option value="authored">My evidence</option><option value="model">Configured model</option></select></label>
    <label className="block">Evidence start<input className={control} type="number" min={0} value={start} onChange={e => changed(() => setStart(Number(e.target.value)))} /></label>
    <label className="block">Evidence end<input className={control} type="number" min={1} value={end} onChange={e => changed(() => setEnd(Number(e.target.value)))} /></label>
    <label className="block">Evidence passage<textarea className={control} readOnly value={quote} /></label>
    <p>Coverage {start}–{end} of {characters.length} characters. Maximum model coverage: 20000; authored quote: 4000.</p>
    {mode === 'authored' ? <><label className="block">Outline summary<input className={control} value={summary} onChange={e => changed(() => setSummary(e.target.value))} /></label>
      <label className="block">Fact subject<input className={control} value={subject} onChange={e => changed(() => setSubject(e.target.value))} /></label><label className="block">Fact predicate<input className={control} value={predicate} onChange={e => changed(() => setPredicate(e.target.value))} /></label><label className="block">Fact value<input className={control} value={value} onChange={e => changed(() => setValue(e.target.value))} /></label></>
      : <label className="block">Extraction instruction<textarea className={control} value={instruction} onChange={e => changed(() => setInstruction(e.target.value))} /></label>}
    <Button disabled={busy || !ledger || start < 0 || end <= start || end > characters.length || end - start > (mode === 'model' ? 20000 : 4000)} onClick={() => void propose()}>Prepare evidence for review</Button>
    {ledger && <><p>Ledger revision {ledger.revision}</p>{ledger.proposals.length === 0 && <p>No evidence proposals yet.</p>}
      {ledger.proposals.map((proposal, index) => <section key={proposal.id} aria-label={`Evidence proposal ${index + 1}`} className="rounded border border-outline p-2"><h3>Evidence proposal {index + 1}</h3><p>Provenance: {proposal.mode}. Coverage {proposal.coverage.start}–{proposal.coverage.end} of {proposal.coverage.total_characters}.</p>
        <p>Source draft {proposal.base_draft_id} · version {proposal.artifact_version}</p><a href={`/api/artifacts/${encodeURIComponent(proposal.artifact_id)}/raw?version=${proposal.artifact_version}`} target="_blank" rel="noreferrer">Open pinned source</a><ul>{[...proposal.outline, ...proposal.facts].map(evidence)}</ul>{proposal.missing && <p>Source missing</p>}{proposal.stale && <p>Earlier draft; create a new proposal to use current text.</p>}
        {proposal.accepted_revision ? <p>Accepted at ledger revision {proposal.accepted_revision}</p> : <Button disabled={busy || proposal.missing || proposal.stale || proposal.base_work_revision !== revision} onClick={() => void accept(proposal)}>Accept evidence {index + 1}</Button>}</section>)}
      <section aria-label="Accepted reverse outline"><h3>Accepted reverse outline</h3><ol>{ledger.outline.map(evidence)}</ol></section>
      <section aria-label="Accepted continuity facts"><h3>Accepted continuity facts</h3><ul>{ledger.facts.map(evidence)}</ul></section>
      <section aria-label="Recorded value conflicts"><h3>Recorded value conflicts</h3><p>Exact subject and predicate comparisons; review differing values in their source context.</p>{ledger.conflicts.map((conflict, index) => <p key={index}>{conflict.subject} · {conflict.predicate}: {conflict.values.join(' / ')}</p>)}</section>
      <Button onClick={() => void exportLedger()}>Export continuity ledger</Button>{exported && <label className="block">Continuity export<textarea className={control} readOnly value={exported} /></label>}
    </>}
  </section>
}
