import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Finding = { id: string; check_id: string; start: number; end: number; quote: string; problem: string; suggestion: string }
type Run = { id: string; draft_id: string; work_revision: number; stale: boolean; missing: boolean; coverage: { start: number; end: number; total_characters: number }; readiness: string; results: { check_id: string; status: string; reason?: string; truncated?: boolean }[]; findings: Finding[] }
type Catalog = { id: string; label: string; availability: string; kind: string; scope: string; context_family?: string; enabled?: boolean; severity?: string }
type Context = { family: string; status: string; reason?: string; revision?: number; artifact_id?: string; artifact_version?: number }
type Policy = { revision: number; readiness_gate: string; checks: Record<string, { enabled: boolean; severity: string }> }
type Review = { id: string; mode: string; status: string; reason?: string }
type Cut = { id: string; status: string; applied_revision?: number; proposal: { id: string } }
type State = { catalog: Catalog[]; contexts: Context[]; runs: Run[]; controls: { scope_id: string; policy: Policy; custom_checks: Catalog[]; reviews: Review[]; cuts: Cut[] } }
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
  const [contextFamily, setContextFamily] = useState('canon')
  const [contextJson, setContextJson] = useState('{}')
  const [readinessGate, setReadinessGate] = useState('block_any')
  const [overrideId, setOverrideId] = useState('prose.cliches')
  const [overrideEnabled, setOverrideEnabled] = useState(true)
  const [overrideSeverity, setOverrideSeverity] = useState('medium')
  const [customLabel, setCustomLabel] = useState('')
  const [customPrompt, setCustomPrompt] = useState('')
  const [reviewMode, setReviewMode] = useState('judge')
  const [activeCut, setActiveCut] = useState<Cut | null>(null)
  const root = `${apiRoot}/${id}/editorial`
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Editorial request failed')
  useEffect(() => {
    let alive = true; setState(null); setSelected(null); setError(''); setMessage(''); setStart(0); setEnd(Math.min(Array.from(text).length, 20000)); setRequestId(crypto.randomUUID())
    requestJson<State>(root).then(result => { if (alive) { setState(result); setReadinessGate(result.controls.policy.readiness_gate); setChecks(result.catalog.filter(c => c.availability === 'available' && c.enabled !== false).map(c => c.id)) } }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [root, revision])
  function changed(action: () => void) { action(); setRequestId(crypto.randomUUID()) }
  async function runChecks() {
    setBusy(true); setError(''); setMessage('')
    try { await requestJson(`${root}/runs`, 'POST', { request_id: requestId, work_revision: revision, start, end, check_ids: checks }); setState(await requestJson<State>(root)); setRequestId(crypto.randomUUID()) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function bindContext() {
    setBusy(true); setError(''); setMessage('')
    try {
      const data = JSON.parse(contextJson)
      await requestJson(`${root}/context`, 'POST', { request_id: requestId, work_revision: revision, family: contextFamily, schema_version: 1, data })
      setState(await requestJson<State>(root)); setMessage(`${contextFamily} context bound to an immutable JSON artifact.`); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function repair() {
    if (!selected) return
    setBusy(true); setError('')
    try { await requestJson(`${root}/runs/${selected.run.id}/findings/${selected.finding.id}/repair`, 'POST', { request_id: requestId, work_revision: revision, replacement }); setMessage('Repair candidate prepared. Review and promote it in manuscript polishing.'); setRequestId(crypto.randomUUID()); onPrepared() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function configurePolicy() {
    if (!state) return
    setBusy(true); setError(''); setMessage('')
    try {
      const prior = state.controls.policy.checks
      await requestJson(`${root}/policy`, 'PATCH', { request_id: requestId, work_revision: revision, policy_revision: state.controls.policy.revision,
        readiness_gate: readinessGate, checks: { ...prior, [overrideId]: { enabled: overrideEnabled, severity: overrideSeverity } } })
      setState(await requestJson<State>(root)); setMessage('Editorial policy saved for the canonical series scope.'); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function createCustom() {
    setBusy(true); setError(''); setMessage('')
    try {
      await requestJson(`${root}/custom-checks`, 'POST', { request_id: requestId, work_revision: revision, label: customLabel, prompt: customPrompt, scope: 'series', severity: overrideSeverity })
      setState(await requestJson<State>(root)); setCustomLabel(''); setCustomPrompt(''); setMessage('Custom editorial check saved.'); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function review() {
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await requestJson<Review>(`${root}/reviews`, 'POST', { request_id: requestId, work_revision: revision, mode: reviewMode })
      setState(await requestJson<State>(root)); setMessage(`${reviewMode} review ${result.status}.`); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function previewCut() {
    if (!selected) return
    setBusy(true); setError('')
    try { const cut = await requestJson<Cut>(`${root}/runs/${selected.run.id}/findings/${selected.finding.id}/cut`, 'POST', { request_id: requestId, work_revision: revision }); setActiveCut(cut); setMessage('Cut preview prepared as an immutable candidate.'); setRequestId(crypto.randomUUID()); onPrepared() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function applyCut() {
    if (!activeCut) return
    setBusy(true); setError('')
    try { const cut = await requestJson<Cut>(`${root}/cuts/${activeCut.id}/apply`, 'POST', { revision }); setActiveCut(cut); setMessage('Reviewed cut applied.'); onPrepared() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function undoCut() {
    if (!activeCut?.applied_revision) return
    setBusy(true); setError('')
    try { const cut = await requestJson<Cut>(`${root}/cuts/${activeCut.id}/undo`, 'POST', { revision: activeCut.applied_revision }); setActiveCut(cut); setMessage('Applied cut undone through work revision restoration.'); onPrepared() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <section aria-label="Editorial review" className="space-y-3 rounded border border-outline p-3"><h2>Editorial review</h2><p>Editorial checks use exact saved-source evidence. Structured families require a versioned canonical JSON binding; model checks use the configured provider and never invent success when it is unavailable.</p>
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}{!state && !error && <p>Loading editorial checks…</p>}
    <section aria-label="Editorial context"><h3>Canonical context</h3><ul>{state?.contexts.map(context => <li key={context.family}>{context.family}: {context.status}{context.revision && ` · revision ${context.revision}`}{context.reason && ` · ${context.reason}`}</li>)}</ul>
      <label className="block">Context family<select className={control} value={contextFamily} onChange={e => changed(() => setContextFamily(e.target.value))}>{['canon', 'cast', 'scene', 'pov', 'arc', 'world', 'research', 'comic'].map(family => <option key={family}>{family}</option>)}</select></label>
      <label className="block">Canonical JSON<textarea className={control} value={contextJson} onChange={e => changed(() => setContextJson(e.target.value))} /></label>
      <Button disabled={busy || !contextJson.trim()} onClick={() => void bindContext()}>Bind canonical context</Button>
    </section>
    <section aria-label="Editorial controls"><h3>Editorial controls</h3><p>Scope: {state?.controls.scope_id}</p>
      <label className="block">Readiness gate<select className={control} value={readinessGate} onChange={e => changed(() => setReadinessGate(e.target.value))}><option value="block_high">Block high</option><option value="block_medium">Block high and medium</option><option value="block_any">Block any finding</option></select></label>
      <label className="block">Override check<select className={control} value={overrideId} onChange={e => changed(() => setOverrideId(e.target.value))}>{state?.catalog.map(check => <option key={check.id} value={check.id}>{check.label}</option>)}</select></label>
      <label className="block"><input type="checkbox" checked={overrideEnabled} onChange={e => changed(() => setOverrideEnabled(e.target.checked))} /> Check enabled</label>
      <label className="block">Override severity<select className={control} value={overrideSeverity} onChange={e => changed(() => setOverrideSeverity(e.target.value))}><option>high</option><option>medium</option><option>low</option></select></label>
      <Button disabled={busy || !state} onClick={() => void configurePolicy()}>Save editorial policy</Button>
      <label className="block">Custom check label<input className={control} value={customLabel} onChange={e => changed(() => setCustomLabel(e.target.value))} /></label>
      <label className="block">Custom check instruction<textarea className={control} value={customPrompt} onChange={e => changed(() => setCustomPrompt(e.target.value))} /></label>
      <Button disabled={busy || !customLabel.trim() || !customPrompt.trim()} onClick={() => void createCustom()}>Create custom check</Button>
      <label className="block">Review mode<select className={control} value={reviewMode} onChange={e => changed(() => setReviewMode(e.target.value))}><option>judge</option><option>panel</option><option>rank</option></select></label>
      <Button disabled={busy || !state} onClick={() => void review()}>Run explicit review</Button>
      <ul>{state?.controls.reviews.map(item => <li key={item.id}>{item.mode}: {item.status}{item.reason && ` · ${item.reason}`}</li>)}</ul>
    </section>
    <label className="block">Editorial coverage start<input className={control} type="number" value={start} onChange={e => changed(() => setStart(Number(e.target.value)))} /></label><label className="block">Editorial coverage end<input className={control} type="number" value={end} onChange={e => changed(() => setEnd(Number(e.target.value)))} /></label>
    <p>Coverage uses Unicode codepoints; at most 20000 per run.</p><details><summary>Editorial check catalog</summary>{state?.catalog.map(check => <label key={check.id} className="block"><input type="checkbox" disabled={busy || check.availability !== 'available'} checked={checks.includes(check.id)} onChange={e => changed(() => setChecks(e.target.checked ? [...checks, check.id] : checks.filter(c => c !== check.id)))} />{check.label} · {check.kind} · {check.availability}</label>)}</details>
    <Button disabled={busy || !state || !checks.length || end <= start || start < 0 || end > Array.from(text).length || end - start > 20000} onClick={() => void runChecks()}>Run selected editorial checks</Button>
    {state?.runs.map((run, index) => <section key={run.id} aria-label={`Editorial run ${state.runs.length - index}`}><h3>Editorial run {state.runs.length - index}</h3><p>Source draft {run.draft_id} · coverage {run.coverage.start}–{run.coverage.end} of {run.coverage.total_characters} · {run.readiness}</p>{run.stale && <p>Run source changed</p>}{run.missing && <p>Run source missing</p>}<ul>{run.results.map(result => <li key={result.check_id}>{result.check_id}: {result.status}{result.reason && ` · ${result.reason}`}{result.truncated && ' · Findings truncated to 100'}</li>)}</ul>
      {run.findings.map(finding => <Button key={finding.id} disabled={busy} onClick={() => { setSelected({ run, finding }); setReplacement(finding.quote); setMessage(''); setRequestId(crypto.randomUUID()) }}>{finding.problem}</Button>)}
    </section>)}
    {selected && <section aria-label="Editorial finding"><h3>{selected.finding.problem}</h3><p>{selected.finding.suggestion}</p><p>Exact source span {selected.finding.start}–{selected.finding.end}</p><label className="block">Finding quotation<textarea className={control} readOnly value={selected.finding.quote} /></label><label className="block">My editorial replacement<textarea className={control} value={replacement} onChange={e => changed(() => setReplacement(e.target.value))} /></label>
      <Button disabled={busy || selected.run.stale || selected.run.missing || selected.run.work_revision !== revision || replacement === selected.finding.quote || selected.finding.end - selected.finding.start > 4000} onClick={() => void repair()}>Prepare reviewed repair</Button>
      {['prose.kill-your-darlings', 'prose.adversarial-cuts'].includes(selected.finding.check_id) && <Button disabled={busy || selected.run.stale || selected.run.missing} onClick={() => void previewCut()}>Preview reviewed cut</Button>}
      {activeCut?.status === 'previewed' && <Button disabled={busy} onClick={() => void applyCut()}>Apply reviewed cut</Button>}
      {activeCut?.status === 'applied' && <Button disabled={busy} onClick={() => void undoCut()}>Undo applied cut</Button>}
    </section>}
  </section>
}
