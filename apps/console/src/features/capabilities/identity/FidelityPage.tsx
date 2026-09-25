import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { TopBar } from '../../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../../shared/ui/WorkbenchLayout'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Case = { id: string; revision: number; prompt: string; source_ids: string[]; rules: { type: string; value: string; case_sensitive?: boolean }[] }
type Run = { id: string; case_id: string; status: string; origin: string; answer: string | null; passed: boolean | null; results: { passed: boolean; rule: { type: string; value: string } }[]; error?: string }
export default function FidelityPage({ endpoint = '/api/capabilities/identity/fidelity' }: { endpoint?: string }) {
  const [documents, setDocuments] = useState<{ id: string; title: string; enabled: boolean; private: boolean }[]>([])
  const [cases, setCases] = useState<Case[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [selected, setSelected] = useState('')
  const [prompt, setPrompt] = useState('')
  const [sources, setSources] = useState('')
  const [rule, setRule] = useState('contains')
  const [value, setValue] = useState('')
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [caseRequestId, setCaseRequestId] = useState(() => crypto.randomUUID())
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const call = async <T,>(path: string, body?: unknown): Promise<T> => readJson<T>(await fetch(endpoint + path,
    { method: body ? 'POST' : 'GET', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }))
  const load = async () => { const [nextCases, nextRuns] = await Promise.all([call<Case[]>('/cases'), call<Run[]>('/runs')]); setCases(nextCases); setRuns(nextRuns); const twin = await readJson<{ documents: typeof documents }>(await fetch(endpoint.replace(/\/fidelity$/, '/twin'), { headers: gatewayHeaders })); setDocuments(twin.documents); setLoaded(true) }
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform(load) }, [endpoint])
  const choose = (item: Case) => { setSelected(item.id); setPrompt(item.prompt); setSources(item.source_ids.join(', ')); setRule(item.rules[0].type); setValue(item.rules[0].value); window.location.hash = '#/capabilities/identity/fidelity?case=' + item.id }
  useEffect(() => { const id = new URLSearchParams(window.location.hash.split('?')[1] || '').get('case'); const item = cases.find(row => row.id === id); if (item) choose(item) }, [cases])
  return <WorkbenchLayout topBar={<TopBar keepCornerPadding left={<PageTitle>Identity fidelity checks</PageTitle>} />}>
  <section className="mx-auto flex w-full max-w-[72rem] flex-col gap-l px-l py-2xl text-on-surface">
    <p>Check explicit words against human identity sources. Literal checks do not measure semantic fidelity. Supplied observations are not verified provider outputs.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!loaded && <p role="status">Loading checks…</p>}
    <Button onClick={() => void perform(load)} disabled={busy}>Reload checks</Button>
    <div className="grid md:grid-cols-2 gap-4">
      <section className="space-y-m rounded-lg bg-surface-container p-l"><h2 data-type="title-l">Cases</h2>{loaded && cases.length === 0 && <p>No fidelity cases yet.</p>}
        {cases.map(item => <button className="block underline" key={item.id} onClick={() => choose(item)}>{item.prompt}</button>)}
        <Button variant="secondary" onClick={() => { setCaseRequestId(crypto.randomUUID()); setSelected(''); setPrompt(''); setSources(''); setValue(''); window.location.hash = '#/capabilities/identity/fidelity' }}>New case</Button>
        <form className="space-y-m rounded-lg bg-surface-container p-l" onSubmit={e => { e.preventDefault(); void perform(async () => {
          const current = cases.find(item => item.id === selected)
          const item = await call<Case>('/cases', { ...(selected ? { id: selected } : { request_id: caseRequestId }), expected_revision: current?.revision || 0,
            prompt, source_ids: sources.split(',').map(x => x.trim()).filter(Boolean), rules: [{ type: rule, value, case_sensitive: false }] })
          choose(item); await load()
        }) }}>
          <label className="block" htmlFor="fidelity-question">Question</label><input className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="fidelity-question" required value={prompt} onChange={e => setPrompt(e.target.value)} />
          <label className="block" htmlFor="fidelity-sources">Identity sources</label><select multiple className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="fidelity-sources" required value={sources.split(',').map(x => x.trim())} onChange={e => setSources(Array.from(e.target.selectedOptions, option => option.value).join(','))}>{documents.map(doc => <option key={doc.id} value={doc.id} disabled={doc.private || !doc.enabled}>{doc.title}{doc.private ? ' (private)' : !doc.enabled ? ' (disabled)' : ''}</option>)}</select>
          <label className="block" htmlFor="fidelity-rule">Expectation</label><select id="fidelity-rule" value={rule} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary appearance-none" onChange={e => setRule(e.target.value)}><option value="contains">Contains</option><option value="not_contains">Does not contain</option><option value="equals">Equals</option></select>
          <label className="block" htmlFor="fidelity-value">Expected text</label><input className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="fidelity-value" required value={value} onChange={e => setValue(e.target.value)} />
          <Button type="submit" disabled={busy}>Save case</Button>
        </form>
      </section>
      <section className="space-y-m rounded-lg bg-surface-container p-l"><h2 data-type="title-l">Observed answer</h2><label className="block" htmlFor="fidelity-answer">Supplied answer</label><textarea className="w-full min-w-0 resize-y rounded-md border border-outline-variant/30 bg-surface-container p-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="fidelity-answer" rows={6} value={answer} onChange={e => setAnswer(e.target.value)} />
        <Button disabled={busy || !selected || !answer.trim()} onClick={() => void perform(async () => { await call('/observations', { case_id: selected, answer, request_id: requestId }); setRequestId(crypto.randomUUID()); await load() })}>Check supplied observation</Button>
        <Button variant="secondary" disabled={busy || !selected} onClick={() => void perform(async () => { await call('/run', { case_id: selected, request_id: requestId }); setRequestId(crypto.randomUUID()); await load() })}>Run configured model</Button>
      </section>
    </div>
    <section aria-label="Evaluation history"><h2 data-type="title-l">Evaluation history</h2>{loaded && runs.length === 0 && <p>No evaluations recorded.</p>}
      {runs.map(run => <article key={run.id} className="rounded-lg bg-surface-container p-m"><p>{run.origin === 'supplied_observation' ? 'Supplied observation' : 'Provider run'} · {run.status} · {run.passed === null ? 'Not scored' : run.passed ? 'Literal rules passed' : 'Literal rules failed'}</p>
        {run.error && <p>{run.error}</p>}<p className="whitespace-pre-wrap">{run.answer}</p>{run.results.map((result, index) => <p key={index}>{result.rule.type}: {result.rule.value} — {result.passed ? 'pass' : 'fail'}</p>)}</article>)}
    </section>
  </section></WorkbenchLayout>
}
