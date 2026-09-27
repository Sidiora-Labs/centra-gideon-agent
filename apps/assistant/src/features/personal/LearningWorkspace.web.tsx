import React, { useEffect, useMemo, useRef, useState } from 'react'
import { GatewayError, gatewayJson } from '../../shared/transport.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { createPersonalClient, type LearningCapture, type LearningReview, type PersonalRecord } from './client'

type CapturePayload = { items: readonly PersonalRecord<LearningCapture>[]; total?: number; unavailable?: boolean; reason?: string }
type ProposalRow = { id: string; kind: string; title: string; provenance?: string; source_excerpt?: string; evidence_refs?: string[]; evidence_strength?: string; risk_tier?: string; status?: string; manifest_valid?: boolean; manifest_issues?: string[]; gate?: Record<string, unknown>; renderable?: boolean }
type InboxPayload = { rows: ProposalRow[]; total?: number; flagged?: number }
type ReadResult<T> = { value?: T; error?: string; unavailable?: boolean }
type WorkspaceData = { captures: CapturePayload; reviews: readonly PersonalRecord<LearningReview>[]; inbox: InboxPayload; week: unknown; health: unknown; report: unknown; summary: unknown; evaluations: Array<{ label: string; payload?: unknown; unavailable?: string }> }
type Scoped<T> = { key: string; value: T }
type ProposalDetail = { id: string; value: Record<string, unknown> }

function isObject(value: unknown): value is Record<string, unknown> { return !!value && typeof value === 'object' && !Array.isArray(value) }
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not reported'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return summarize(value)
}
function humanize(key: string): string {
  return key.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').replace(/\b\w/g, character => character.toUpperCase())
}
function identifierKey(key: string): boolean { return /(^id$|_id$|hash|fingerprint|token|request.?id)/i.test(key) }
function summarize(value: unknown): string {
  if (Array.isArray(value)) return `${value.length} ${value.length === 1 ? 'item' : 'items'}`
  if (isObject(value)) {
    const summary = ['title', 'summary', 'status', 'state', 'health', 'period', 'date', 'total', 'count']
      .map(key => value[key]).find(item => ['string', 'number', 'boolean'].includes(typeof item))
    return summary === undefined ? `${Object.keys(value).filter(key => !identifierKey(key)).length} recorded fields` : display(summary)
  }
  return display(value)
}
function text(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) if (typeof record[key] === 'string' && (record[key] as string).trim()) return (record[key] as string).trim()
  return ''
}
async function readOptional<T>(path: string, signal: AbortSignal): Promise<ReadResult<T>> {
  try { return { value: await gatewayJson<T>(path, { signal }) } }
  catch (error) {
    if (error instanceof GatewayError && [404, 501].includes(error.status)) return { unavailable: true, error: error.message }
    if (error instanceof GatewayError && [401, 403].includes(error.status)) throw error
    if (signal.aborted) throw error
    return { error: error instanceof Error ? error.message : 'The service returned an unreadable response.' }
  }
}

export function LearningWorkspace({ route, scope, onReturn }: ModuleProps) {
  const { palette } = useShellTheme()
  const client = useMemo(() => createPersonalClient(scope), [scope.cacheKey])
  const ownerRoute = `${scope.cacheKey}:${route.destination}:${route.placement?.id ?? ''}`
  const currentSelection = useRef(ownerRoute)
  currentSelection.current = ownerRoute
  const proposalRequest = useRef({ generation: 0, routeKey: ownerRoute, id: '' })
  const [dataState, setDataState] = useState<Scoped<WorkspaceData> | null>(null)
  const [errorState, setErrorState] = useState<Scoped<string> | null>(null)
  const [busyState, setBusyState] = useState<Scoped<boolean> | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [selectedState, setSelectedState] = useState<Scoped<ProposalRow> | null>(null)
  const [proposalState, setProposalState] = useState<Scoped<ProposalDetail> | null>(null)
  const [reviewedState, setReviewedState] = useState<Scoped<boolean> | null>(null)
  const [decisionMessageState, setDecisionMessageState] = useState<Scoped<string> | null>(null)
  const [decisionErrorState, setDecisionErrorState] = useState<Scoped<string> | null>(null)
  const data = dataState?.key === ownerRoute ? dataState.value : null
  const error = errorState?.key === ownerRoute ? errorState.value : ''
  const busy = busyState?.key === ownerRoute ? busyState.value : false
  const selected = selectedState?.key === ownerRoute ? selectedState.value : null
  const proposalDetail = proposalState?.key === ownerRoute ? proposalState.value : null
  const proposal = selected && proposalDetail?.id === selected.id ? proposalDetail.value : null
  const reviewed = reviewedState?.key === ownerRoute ? reviewedState.value : false
  const decisionMessage = decisionMessageState?.key === ownerRoute ? decisionMessageState.value : ''
  const decisionError = decisionErrorState?.key === ownerRoute ? decisionErrorState.value : ''

  useEffect(() => {
    const controller = new AbortController()
    let current = true
    const requestKey = ownerRoute
    proposalRequest.current = { generation: proposalRequest.current.generation + 1, routeKey: requestKey, id: '' }
    setBusyState({ key: requestKey, value: true }); setErrorState(null); setSelectedState(null); setProposalState(null); setReviewedState(null); setDecisionErrorState(null)
    const captures = client.readLearningCaptures(controller.signal)
    const reviews = client.readLearningReviews(controller.signal)
    const tasks = Promise.all([
      captures,
      reviews,
      readOptional<InboxPayload>('/api/learning/proposals', controller.signal),
      readOptional<unknown>('/api/learning/staging/week', controller.signal),
      readOptional<unknown>('/api/learning/health', controller.signal),
      readOptional<unknown>('/api/learning/identity-report', controller.signal),
      readOptional<unknown>('/api/learning/summary', controller.signal),
      readOptional<unknown>('/api/evals/judge-bench', controller.signal),
      readOptional<unknown>('/api/evals/studies', controller.signal),
      readOptional<unknown>('/api/evals/learning-benchmark', controller.signal),
    ])
    void tasks.then(([captureResult, reviewResult, inbox, week, health, report, summary, bench, studies, benchmark]) => {
      if (!current || currentSelection.current !== requestKey) return
      const capturesValue: CapturePayload = captureResult.state === 'available'
        ? { items: captureResult.value, total: captureResult.value.length }
        : { items: [], total: 0, unavailable: true, reason: captureResult.reason }
      const optionalValue = <T,>(result: ReadResult<T>, label: string): T | { unavailable: true; reason: string } | { failed: true; reason: string } => {
        if (result.value !== undefined) return result.value
        if (result.unavailable) return { unavailable: true, reason: result.error || `${label} is unavailable.` }
        return { failed: true, reason: result.error || `${label} could not be loaded.` }
      }
      const inboxValue = optionalValue(inbox, 'Proposal inbox') as InboxPayload
      setDataState({ key: requestKey, value: { captures: capturesValue as unknown as CapturePayload,
        reviews: reviewResult.state === 'available' ? reviewResult.value : [],
        inbox: inboxValue,
        week: optionalValue(week, 'Capture history'), health: optionalValue(health, 'Learning health'), report: optionalValue(report, 'Identity report'), summary: optionalValue(summary, 'Learning summary'),
        evaluations: [{ label: 'Judge benchmark', payload: optionalValue(bench, 'Judge benchmark') }, { label: 'Registered studies', payload: optionalValue(studies, 'Registered studies') }, { label: 'Learning benchmark', payload: optionalValue(benchmark, 'Learning benchmark') }] } })
    }).catch((failure: unknown) => {
      if (!current || currentSelection.current !== requestKey) return
      if (failure instanceof GatewayError && [401, 403].includes(failure.status)) {
        setDataState(previous => previous?.key === requestKey ? null : previous)
      }
      const message = failure instanceof GatewayError && (failure.status === 401 || failure.authRequired) ? 'Your session expired. Sign in again to view Learning.'
        : failure instanceof GatewayError && failure.status === 403 ? 'Access to Learning was denied for this session.'
          : failure instanceof Error ? failure.message : 'Learning could not be loaded.'
      setErrorState({ key: requestKey, value: message })
    }).finally(() => { if (current && currentSelection.current === requestKey) setBusyState({ key: requestKey, value: false }) })
    return () => { current = false; controller.abort() }
  }, [client, ownerRoute, attempt])

  useEffect(() => () => client.dispose(), [client])

  function refresh() {
    proposalRequest.current = { generation: proposalRequest.current.generation + 1, routeKey: ownerRoute, id: '' }
    setSelectedState(null); setProposalState(null); setReviewedState(null); setDecisionErrorState(null); setDecisionMessageState(null)
    setAttempt(value => value + 1)
  }

  async function openProposal(row: ProposalRow) {
    const routeKey = ownerRoute
    if (busy) return
    const generation = proposalRequest.current.generation + 1
    proposalRequest.current = { generation, routeKey, id: row.id }
    const controller = new AbortController()
    setSelectedState({ key: routeKey, value: row }); setProposalState(null); setReviewedState({ key: routeKey, value: false }); setDecisionErrorState({ key: routeKey, value: '' }); setDecisionMessageState({ key: routeKey, value: 'Loading proposal details…' })
    try {
      const result = await gatewayJson<Record<string, unknown>>(`/api/learning/proposals/${encodeURIComponent(row.id)}`, { signal: controller.signal })
      if (routeKey !== currentSelection.current || proposalRequest.current.generation !== generation || proposalRequest.current.routeKey !== routeKey || proposalRequest.current.id !== row.id) return
      setProposalState({ key: routeKey, value: { id: row.id, value: result } }); setDecisionMessageState({ key: routeKey, value: '' })
    } catch (failure) {
      if (routeKey !== currentSelection.current || proposalRequest.current.generation !== generation || proposalRequest.current.routeKey !== routeKey || proposalRequest.current.id !== row.id) return
      setDecisionErrorState({ key: routeKey, value: failure instanceof Error ? failure.message : 'Proposal details could not be loaded.' })
      setDecisionMessageState({ key: routeKey, value: '' })
    }
  }

  async function decide(action: 'accept' | 'reject') {
    const target = selected
    const routeKey = ownerRoute
    const generation = proposalRequest.current.generation
    if (!target || !reviewed || !proposal || proposalRequest.current.routeKey !== routeKey || proposalRequest.current.id !== target.id || busy) return
    const controller = new AbortController()
    setBusyState({ key: routeKey, value: true }); setDecisionErrorState({ key: routeKey, value: '' }); setDecisionMessageState({ key: routeKey, value: 'Saving your review…' })
    try {
      const result = await gatewayJson<Record<string, unknown>>(`/api/learning/proposals/${encodeURIComponent(target.id)}${action === 'accept' ? '/accept' : ''}`,
        { method: action === 'accept' ? 'POST' : 'DELETE', ...(action === 'accept' ? { body: {} } : {}), signal: controller.signal })
      if (routeKey !== currentSelection.current || proposalRequest.current.generation !== generation || proposalRequest.current.id !== target.id) return
      setDecisionMessageState({ key: routeKey, value: action === 'accept' ? 'You accepted this proposal.' : 'You dismissed this proposal.' })
      setSelectedState(null); setProposalState(null); setReviewedState(null); setAttempt(value => value + 1)
      if (action === 'accept' && result.applied && isObject(result.applied) && result.applied.applied === false) setDecisionMessageState({ key: routeKey, value: `Accepted, but installation reported: ${String(result.applied.reason ?? 'not applied')}` })
    } catch (failure) {
      if (routeKey !== currentSelection.current || proposalRequest.current.generation !== generation || proposalRequest.current.id !== target.id) return
      setDecisionErrorState({ key: routeKey, value: failure instanceof Error ? failure.message : 'The review was not saved. The proposal remains available for retry.' })
      setDecisionMessageState({ key: routeKey, value: '' })
    } finally { if (routeKey === currentSelection.current && proposalRequest.current.generation === generation) setBusyState({ key: routeKey, value: false }) }
  }

  const style = { '--learning-text': palette.text, '--learning-muted': palette.muted, '--learning-line': palette.line, '--learning-card': palette.card, '--learning-canvas': palette.canvas, '--learning-accent': palette.blueDark, '--learning-accent-surface': palette.sky } as React.CSSProperties
  const title = 'Learning'
  const workspaceState = !data && (busy || !error) ? { kind: 'loading' as const, message: 'Loading Learning records and reports…' }
    : error && !data ? { kind: 'error' as const, message: error, onRetry: refresh }
      : data ? { kind: 'ready' as const }
        : { kind: 'ready' as const }

  return <WorkspaceFrame route={route} mode="full" title={title} state={workspaceState} onBack={onReturn}
    actions={<button type="button" onClick={refresh} disabled={busy}>Refresh</button>}>
    {data && <div className="gideon-learning" style={style}>
      <p className="gideon-learning__intro">Capture keeps its original source and timeline. Pending proposals require an authenticated human review.</p>
      <section aria-labelledby="learning-captures"><header><div><h2 id="learning-captures">Capture history</h2><p>{data.captures.total ?? data.captures.items.length} capture receipts</p></div></header>
        {(data.captures as unknown as Record<string, unknown>).unavailable === true && <p role="status">{String((data.captures as unknown as Record<string, unknown>).reason ?? 'Capture history is unavailable.')}</p>}
        {data.captures.items.length === 0 ? <p>No captures are recorded yet.</p> : <ol className="gideon-learning__capture-list">{data.captures.items.map(capture => <CaptureCard key={capture.identity.nativeId} capture={capture} />)}</ol>}
      </section>
      <section aria-labelledby="learning-reviews"><h2 id="learning-reviews">Knowledge reviews</h2>
        {data.reviews.length ? <ul>{data.reviews.map(review => <li key={review.identity.nativeId}><strong>{text(review.value, 'title') || 'Review'}</strong> · {text(review.value, 'period', 'date') || 'Date not supplied'}
          <details className="gideon-learning__record-details"><summary>Record details</summary><p><strong>Record ID:</strong> <code>{review.identity.nativeId}</code><br /><strong>Source kind:</strong> {review.identity.sourceKind}</p></details></li>)}</ul> : <p>No saved knowledge reviews are available.</p>}
      </section>
      <section aria-labelledby="learning-proposals"><h2 id="learning-proposals">Proposals for human review</h2>
        <p>{data.inbox.total ?? data.inbox.rows?.length ?? 0} pending · {data.inbox.flagged ?? 0} flagged for manifest review</p>
        {!Array.isArray(data.inbox.rows) ? <Unavailable value={data.inbox} label="Proposal inbox" onRetry={refresh} /> : data.inbox.rows.length === 0 ? <p>No pending proposals.</p> : <ul className="gideon-learning__proposal-list">{data.inbox.rows.map(row => <li key={row.id}>
          <button type="button" className="gideon-learning__proposal" onClick={() => void openProposal(row)}><strong>{row.title || 'Untitled proposal'}</strong><span>{row.kind} · {row.provenance || 'Source not supplied'} · {row.risk_tier || 'Review tier'}</span><span>{row.manifest_valid === false ? `Needs manifest attention: ${(row.manifest_issues ?? []).join(', ')}` : row.evidence_strength || 'Evidence strength not supplied'}</span><span>{row.source_excerpt || row.evidence_refs?.join(' · ') || 'Evidence details require review'}</span></button>
        </li>)}</ul>}
        {selected && <article className="gideon-learning__review" aria-labelledby="proposal-detail-title"><header><div><p className="gideon-learning__eyebrow">Proposal details</p><h3 id="proposal-detail-title">{selected.title}</h3></div><button type="button" disabled={busy} onClick={() => { proposalRequest.current = { generation: proposalRequest.current.generation + 1, routeKey: ownerRoute, id: '' }; setSelectedState(null); setProposalState(null); setReviewedState(null); setDecisionErrorState({ key: ownerRoute, value: '' }) }}>Close</button></header>
          {decisionMessage && <p role="status">{decisionMessage}</p>}{decisionError && <p role="alert">{decisionError}</p>}
          {proposal && <><p><strong>Provenance:</strong> {text(proposal, 'provenance') || selected.provenance || 'Not supplied'}</p><p><strong>Source evidence:</strong> {text(proposal, 'source_excerpt') || selected.source_excerpt || 'No excerpt supplied.'}</p><p><strong>Proposal:</strong> {text(proposal, 'body') || 'No proposal body supplied.'}</p>
            <DataPanel label="Evidence references" value={proposal.evidence_refs ?? selected.evidence_refs ?? []} onRetry={refresh} />
            <DataPanel label="Change manifest" value={proposal.change_manifest ?? { unavailable: true, reason: 'No change manifest was supplied with this proposal.' }} onRetry={refresh} />
            <p><strong>Review gate:</strong> Only the authenticated human reviewer can decide a pending proposal; reviewer identity comes from the session.</p>
            <DataPanel label="Additional review details" value={proposal.gate ?? selected.gate ?? { unavailable: true, reason: 'No proposal-specific review details were supplied.' }} onRetry={refresh} />
            <label className="gideon-learning__ack"><input type="checkbox" checked={reviewed} onChange={event => setReviewedState({ key: ownerRoute, value: event.currentTarget.checked })} /> I reviewed the proposal, provenance, evidence and review gate.</label>
            <div className="gideon-learning__decision"><button type="button" onClick={() => void decide('accept')} disabled={!reviewed || !proposal || proposalDetail?.id !== selected.id || busy || selected.status !== 'pending'}>Accept proposal</button><button type="button" onClick={() => void decide('reject')} disabled={!reviewed || !proposal || proposalDetail?.id !== selected.id || busy || selected.status !== 'pending'}>Dismiss proposal</button></div>
          </>}
        </article>}
      </section>
      <section aria-labelledby="learning-reports"><h2 id="learning-reports">Capture and learning health</h2><DataPanel label="Capture history by day" value={data.week} onRetry={refresh} /><DataPanel label="Learning health" value={data.health} onRetry={refresh} /><DataPanel label="Learning summary" value={data.summary} onRetry={refresh} /></section>
      <section aria-labelledby="learning-identity-report"><h2 id="learning-identity-report">Identity report</h2><DataPanel label="Latest identity report" value={data.report} onRetry={refresh} /></section>
      <section aria-labelledby="learning-evaluations"><h2 id="learning-evaluations">Evaluation results</h2><div className="gideon-learning__evals">{data.evaluations.map(item => <DataPanel key={item.label} label={item.label} value={item.payload ?? { unavailable: true, reason: item.unavailable }} onRetry={refresh} />)}</div></section>
      <style>{`
        .gideon-learning{box-sizing:border-box;min-height:100%;padding:clamp(18px,4vw,36px);color:var(--learning-text)}.gideon-learning__intro{max-width:76ch;color:var(--learning-muted)}.gideon-learning section{margin:0 0 22px;padding:18px;border:1px solid var(--learning-line);border-radius:14px;background:var(--learning-card)}.gideon-learning section h2{margin:0 0 8px;font-size:1.18rem}.gideon-learning section>header{display:flex;justify-content:space-between;gap:12px}.gideon-learning section>header p{margin:4px 0;color:var(--learning-muted)}.gideon-learning__capture-list,.gideon-learning__proposal-list{display:grid;gap:10px;margin:14px 0;padding:0;list-style:none}.gideon-learning__capture-list article,.gideon-learning__review,.gideon-learning__data{padding:14px;border:1px solid var(--learning-line);border-radius:10px;background:var(--learning-canvas)}.gideon-learning__capture-list p,.gideon-learning__review p{overflow-wrap:anywhere}.gideon-learning__proposal{display:grid;width:100%;gap:5px;text-align:left;border:1px solid var(--learning-line);border-radius:10px;padding:13px;background:var(--learning-canvas);color:inherit;font:inherit;cursor:pointer}.gideon-learning__proposal span,.gideon-learning__eyebrow{color:var(--learning-muted);font-size:.9rem}.gideon-learning button{border:1px solid var(--learning-line);border-radius:8px;padding:8px 12px;background:var(--learning-canvas);color:inherit;font:inherit;cursor:pointer}.gideon-learning button:focus-visible,.gideon-learning input:focus-visible{outline:2px solid var(--learning-accent);outline-offset:2px}.gideon-learning button:disabled{opacity:.55;cursor:not-allowed}.gideon-learning__review{margin-top:14px}.gideon-learning__review header{display:flex;align-items:start;justify-content:space-between;gap:10px}.gideon-learning__review h3{margin:0}.gideon-learning__ack{display:flex;gap:9px;align-items:start;margin:14px 0}.gideon-learning__decision{display:flex;flex-wrap:wrap;gap:9px}.gideon-learning__decision button:first-child{border-color:var(--learning-accent);background:var(--learning-accent-surface)}.gideon-learning__data{margin:10px 0;overflow:auto}.gideon-learning__data summary{cursor:pointer;font-weight:600}.gideon-learning__data dl{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}.gideon-learning__data dt,.gideon-learning__record-details summary{color:var(--learning-muted);font-size:.86rem}.gideon-learning__data dd{margin:3px 0;overflow-wrap:anywhere}.gideon-learning__data details{margin:8px 0;padding:8px;border-left:2px solid var(--learning-line)}.gideon-learning__data details summary,.gideon-learning__record-details summary{cursor:pointer}.gideon-learning__data ul{padding-left:20px}.gideon-learning__record-details{margin-top:6px}.gideon-learning__evals{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.gideon-learning code{overflow-wrap:anywhere}@media(max-width:600px){.gideon-learning{padding:16px}.gideon-learning section{padding:14px}}
      `}</style>
    </div>}
  </WorkspaceFrame>
}

function CaptureCard({ capture }: { capture: PersonalRecord<LearningCapture> }) {
  const value = capture.value
  const id = capture.identity.nativeId
  return <li><article><header><strong>{text(value, 'input_origin') || 'Capture'}</strong><span>{text(value, 'status') || 'Status not reported'}</span></header>
    <p>{text(value, 'text', 'original_text') || 'Original text not available.'}</p>
    <p><strong>Captured:</strong> {text(value, 'captured_at') || 'Timestamp not supplied'} · <strong>Revision:</strong> {display(capture.revision)} · {capture.freshness === 'stale' ? 'Earlier result' : 'Current source'}</p>
    {text(value, 'transcript') && <p><strong>Transcript:</strong> {text(value, 'transcript')}</p>}
    {text(value, 'error') && <p role="alert"><strong>Capture error:</strong> {text(value, 'error')}</p>}
    {Array.isArray(value.events) && <details><summary>History ({value.events.length} events)</summary><ol>{value.events.map((event, index) => <li key={index}>{isObject(event) ? <><strong>{humanize(text(event, 'event') || 'Update')}</strong> · {text(event, 'happened_at') || 'Time not supplied'}{event.payload !== undefined && <StructuredValue value={event.payload} />}</> : display(event)}</li>)}</ol></details>}
    <details className="gideon-learning__record-details"><summary>Source and record details</summary><p><strong>Source kind:</strong> {capture.identity.sourceKind} · <strong>Source ID:</strong> <code>{id || 'Not supplied'}</code>{text(value, 'source_link') && <> · <a href={text(value, 'source_link')}>Open source record</a></>}</p></details>
  </article></li>
}

function Unavailable({ value, label, onRetry }: { value: unknown; label: string; onRetry: () => void }) {
  const failed = isObject(value) && value.failed === true
  const detail = isObject(value) && (value.unavailable || failed) ? text(value, 'reason') || `${label} ${failed ? 'could not be loaded' : 'is unavailable on this Gideon instance'}.` : `${label} returned no record.`
  return <p role={failed ? 'alert' : 'status'}>{detail} <button type="button" onClick={onRetry}>Retry</button></p>
}

function DataPanel({ label, value, onRetry }: { label: string; value: unknown; onRetry: () => void }) {
  if (isObject(value) && (value.unavailable || value.failed)) return <section className="gideon-learning__data"><h3>{label}</h3><Unavailable value={value} label={label} onRetry={onRetry} /></section>
  return <details className="gideon-learning__data"><summary>{label} · {summarize(value)}</summary><StructuredValue value={value} /></details>
}

function StructuredValue({ value, label }: { value: unknown; label?: string }) {
  if (Array.isArray(value)) return value.length ? <ul>{value.map((item, index) => <li key={index}>{isObject(item) || Array.isArray(item)
    ? <details><summary>{label ? `${label} ${index + 1}` : `Item ${index + 1}`} · {summarize(item)}</summary><StructuredValue value={item} /></details>
    : display(item)}</li>)}</ul> : <p>No entries were reported.</p>
  if (!isObject(value)) return <p>{display(value)}</p>
  const entries = Object.entries(value)
  const ordinary = entries.filter(([key]) => !identifierKey(key))
  const identifiers = entries.filter(([key]) => identifierKey(key))
  return <>
    {ordinary.length ? <dl>{ordinary.map(([key, item]) => <div key={key}><dt>{humanize(key)}</dt><dd>{isObject(item) || Array.isArray(item)
      ? <details><summary>{summarize(item)}</summary><StructuredValue value={item} label={humanize(key)} /></details>
      : display(item)}</dd></div>)}</dl> : <p>No report fields were supplied.</p>}
    {identifiers.length > 0 && <details><summary>Record identifiers</summary><dl>{identifiers.map(([key, item]) => <div key={key}><dt>{humanize(key)}</dt><dd><code>{display(item)}</code></dd></div>)}</dl></details>}
  </>
}

export default LearningWorkspace
