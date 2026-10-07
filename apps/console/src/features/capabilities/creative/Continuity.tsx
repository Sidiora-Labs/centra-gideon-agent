import { Select, TextInput, TextArea } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Entry = { start: number; end: number; quote: string; summary?: string; subject?: string; predicate?: string; value?: string; draft_id?: string; stale?: boolean; missing?: boolean }
type Proposal = { id: string; mode: string; base_work_revision: number; base_draft_id: string; artifact_id: string; artifact_version: number; accepted_revision: number | null; stale: boolean; missing: boolean; coverage: { start: number; end: number; total_characters: number }; outline: Entry[]; facts: Entry[] }
type Ledger = { revision: number; proposals: Proposal[]; outline: Entry[]; facts: Entry[]; conflicts: { subject: string; predicate: string; values: string[] }[] }
const control = 'w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
export default function Continuity({ id, revision, text, apiRoot }: { id: string; revision: number; text: string; apiRoot: string }) {
  const t = (value: string) => value
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
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Continuity request failed'))
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
  function evidence(entry: Entry, index: number) { return <li key={index}><p>{entry.summary || `${entry.subject} · ${entry.predicate} · ${entry.value}`}</p><blockquote className="whitespace-pre-wrap">{entry.quote}</blockquote><p>{t('Source span')} {entry.start}–{entry.end}{entry.stale && ` · ${t('Earlier draft')}`}{entry.missing && ` · ${t('Source missing')}`}</p></li> }
  return <section aria-label={t('Reverse outline and continuity')} className="space-y-5 rounded-lg bg-surface-container p-l"><header><h2 data-type="title-m" className="text-on-surface">{t('Reverse outline and continuity')}</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-var">{t('Review exact evidence from a saved draft before adding it to the ledger. Canon is unchanged. Positions count Unicode characters, including each emoji as one codepoint.')}</p></header>
    {error && <p role="alert">{error}</p>}{!ledger && !error && <p>{t('Loading continuity…')}</p>}
    <div className="grid gap-5 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]"><section aria-label={t('Prepare evidence')} className="space-y-3 rounded-md bg-surface-high p-l"><h3 data-type="label-l" className="text-on-surface">{t('Prepare evidence')}</h3>
    <label className="block">{t('Extraction source')}<Select className={control} value={mode} onChange={nextValue => changed(() => setMode(nextValue))} options={[{value: "authored", label: String(t('My evidence'))}, {value: "model", label: String(t('Configured model'))}]} /></label>
    <label className="block">{t('Evidence start')}<TextInput className={control} type="number" min={0} value={String(start)} onChange={nextValue => changed(() => setStart(Number(nextValue)))} /></label>
    <label className="block">{t('Evidence end')}<TextInput className={control} type="number" min={1} value={String(end)} onChange={nextValue => changed(() => setEnd(Number(nextValue)))} /></label>
    <label className="block">{t('Evidence passage')}<TextArea className={control} readOnly value={quote} onChange={() => {}} /></label>
    <p>{t('Coverage')} {start}–{end} {t('of')} {characters.length} {t('characters. Maximum model coverage: 20000; authored quote: 4000.')}</p>
    {mode === 'authored' ? <><label className="block">{t('Outline summary')}<TextInput className={control} value={summary} onChange={nextValue => changed(() => setSummary(nextValue))} /></label>
      <label className="block">{t('Fact subject')}<TextInput className={control} value={subject} onChange={nextValue => changed(() => setSubject(nextValue))} /></label><label className="block">{t('Fact predicate')}<TextInput className={control} value={predicate} onChange={nextValue => changed(() => setPredicate(nextValue))} /></label><label className="block">{t('Fact value')}<TextInput className={control} value={value} onChange={nextValue => changed(() => setValue(nextValue))} /></label></>
      : <label className="block">{t('Extraction instruction')}<TextArea className={control} value={instruction} onChange={nextValue => changed(() => setInstruction(nextValue))} /></label>}
    <Button disabled={busy || !ledger || start < 0 || end <= start || end > characters.length || end - start > (mode === 'model' ? 20000 : 4000)} onClick={() => void propose()} disabledReason={busy ? BUSY_REASON : undefined}>{t('Prepare evidence for review')}</Button>
    </section><section aria-label={t('Continuity ledger')} className="space-y-3"><h3 data-type="label-l" className="text-on-surface">{t('Continuity ledger')}</h3>{ledger && <><p data-type="body-s" className="text-on-surface-var">{t('Ledger revision')} {ledger.revision}</p>{ledger.proposals.length === 0 && <p>{t('No evidence proposals yet.')}</p>}
      {ledger.proposals.map((proposal, index) => <section key={proposal.id} aria-label={`${t('Evidence proposal')} ${index + 1}`} className="rounded border border-outline p-2"><h3>{t('Evidence proposal')} {index + 1}</h3><p>{t('Provenance:')} {t(proposal.mode)}. {t('Coverage')} {proposal.coverage.start}–{proposal.coverage.end} {t('of')} {proposal.coverage.total_characters}.</p>
        <p>{t('Source draft')} {proposal.base_draft_id} · {t('version')} {proposal.artifact_version}</p><a href={`/api/artifacts/${encodeURIComponent(proposal.artifact_id)}/raw?version=${proposal.artifact_version}`} target="_blank" rel="noreferrer">{t('Open pinned source')}</a><ul>{[...proposal.outline, ...proposal.facts].map(evidence)}</ul>{proposal.missing && <p>{t('Source missing')}</p>}{proposal.stale && <p>{t('Earlier draft; create a new proposal to use current text.')}</p>}
        {proposal.accepted_revision ? <p>{t('Accepted at ledger revision')} {proposal.accepted_revision}</p> : <Button disabled={busy || proposal.missing || proposal.stale || proposal.base_work_revision !== revision} onClick={() => void accept(proposal)} disabledReason={busy ? BUSY_REASON : undefined}>{t('Accept evidence')} {index + 1}</Button>}</section>)}
      <section aria-label={t('Accepted reverse outline')}><h3>{t('Accepted reverse outline')}</h3><ol>{ledger.outline.map(evidence)}</ol></section>
      <section aria-label={t('Accepted continuity facts')}><h3>{t('Accepted continuity facts')}</h3><ul>{ledger.facts.map(evidence)}</ul></section>
      <section aria-label={t('Recorded value conflicts')}><h3>{t('Recorded value conflicts')}</h3><p>{t('Exact subject and predicate comparisons; review differing values in their source context.')}</p>{ledger.conflicts.map((conflict, index) => <p key={index}>{conflict.subject} · {conflict.predicate}: {conflict.values.join(' / ')}</p>)}</section>
      <Button onClick={() => void exportLedger()}>{t('Export continuity ledger')}</Button>{exported && <label className="block">{t('Continuity export')}<TextArea className={control} readOnly value={exported} onChange={() => {}} /></label>}
    </>}</section></div>
  </section>
}
