import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Finding = { id: string; check_id: string; start: number; end: number; quote: string; problem: string; suggestion: string }
type Run = { id: string; draft_id: string; work_revision: number; stale: boolean; missing: boolean; coverage: { start: number; end: number; total_characters: number }; readiness: string; results: { check_id: string; status: string; reason?: string; truncated?: boolean }[]; findings: Finding[] }
type Catalog = { id: string; label: string; availability: string; kind: string; scope: string }
type State = { catalog: Catalog[]; runs: Run[] }
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
export default function Editorial({ id, revision, text, apiRoot, onPrepared }: { id: string; revision: number; text: string; apiRoot: string; onPrepared: () => void }) {
  const [state, setState] = useState<State | null>(null)
  const [checks, setChecks] = useState<string[]>([])
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(Math.min(Array.from(text).length, 20000))
  const [selected, setSelected] = useState<{ run: Run; finding: Finding } | null>(null)
  const [replacement, setReplacement] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const root = `${apiRoot}/${id}/editorial`
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Editorial request failed')
  useEffect(() => {
    let alive = true; setState(null); setSelected(null); setError(''); setMessage(''); setStart(0); setEnd(Math.min(Array.from(text).length, 20000)); setRequestId(crypto.randomUUID())
    requestJson<State>(root).then(result => { if (alive) { setState(result); setChecks(result.catalog.filter(c => c.availability === 'available').map(c => c.id)) } }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [root, revision])
  function changed(action: () => void) { action(); setRequestId(crypto.randomUUID()) }
  async function runChecks() {
    setBusy(true); setError(''); setMessage('')
    try { await requestJson(`${root}/runs`, 'POST', { request_id: requestId, work_revision: revision, start, end, check_ids: checks }); setState(await requestJson<State>(root)); setRequestId(crypto.randomUUID()) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function repair() {
    if (!selected) return
    setBusy(true); setError('')
    try { await requestJson(`${root}/runs/${selected.run.id}/findings/${selected.finding.id}/repair`, 'POST', { request_id: requestId, work_revision: revision, replacement }); setMessage('Repair candidate prepared. Review and promote it in manuscript polishing.'); setRequestId(crypto.randomUUID()); onPrepared() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <section aria-label="Editorial review" className="space-y-3 rounded border border-outline p-3"><h2>Editorial review</h2><p>English prose heuristics produce advisory findings, not quality judgments. Review exact saved-source evidence before preparing a repair. Other editorial families remain pending.</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}{!state && !error && <p>Loading editorial checks…</p>}
    <label className="block">Editorial coverage start<input className={control} type="number" value={start} onChange={e => changed(() => setStart(Number(e.target.value)))} /></label><label className="block">Editorial coverage end<input className={control} type="number" value={end} onChange={e => changed(() => setEnd(Number(e.target.value)))} /></label>
    <p>Coverage uses Unicode codepoints; at most 20000 per run.</p><details><summary>Editorial check catalog</summary>{state?.catalog.map(check => <label key={check.id} className="block"><input type="checkbox" disabled={busy || check.availability !== 'available'} checked={checks.includes(check.id)} onChange={e => changed(() => setChecks(e.target.checked ? [...checks, check.id] : checks.filter(c => c !== check.id)))} />{check.label} · {check.kind} · {check.availability}</label>)}</details>
    <Button disabled={busy || !state || !checks.length || end <= start || start < 0 || end > Array.from(text).length || end - start > 20000} onClick={() => void runChecks()}>Run selected editorial checks</Button>
    {state?.runs.map((run, index) => <section key={run.id} aria-label={`Editorial run ${state.runs.length - index}`}><h3>Editorial run {state.runs.length - index}</h3><p>Source draft {run.draft_id} · coverage {run.coverage.start}–{run.coverage.end} of {run.coverage.total_characters} · {run.readiness}</p>{run.stale && <p>Run source changed</p>}{run.missing && <p>Run source missing</p>}<ul>{run.results.map(result => <li key={result.check_id}>{result.check_id}: {result.status}{result.reason && ` · ${result.reason}`}{result.truncated && ' · Findings truncated to 100'}</li>)}</ul>
      {run.findings.map(finding => <Button key={finding.id} disabled={busy} onClick={() => { setSelected({ run, finding }); setReplacement(finding.quote); setMessage(''); setRequestId(crypto.randomUUID()) }}>{finding.problem}</Button>)}
    </section>)}
    {selected && <section aria-label="Editorial finding"><h3>{selected.finding.problem}</h3><p>{selected.finding.suggestion}</p><p>Exact source span {selected.finding.start}–{selected.finding.end}</p><label className="block">Finding quotation<textarea className={control} readOnly value={selected.finding.quote} /></label><label className="block">My editorial replacement<textarea className={control} value={replacement} onChange={e => changed(() => setReplacement(e.target.value))} /></label>
      <Button disabled={busy || selected.run.stale || selected.run.missing || selected.run.work_revision !== revision || replacement === selected.finding.quote || selected.finding.end - selected.finding.start > 4000} onClick={() => void repair()}>Prepare reviewed repair</Button>
    </section>}
  </section>
}
