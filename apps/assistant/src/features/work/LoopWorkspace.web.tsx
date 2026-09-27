import React, { useEffect, useRef, useState } from 'react'
import type { Loop } from '../../../../console/src/shared/data/api'
import type { OwnerScope } from '../../shared/auth.web'
import type { ShellRoute } from '../../shared/shell/shellRoutes'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { gatewayJson, openGatewayEventSource } from '../../shared/transport.web'
import { createWorkRoute } from './workRouteModel'

type Props = { route: ShellRoute; scope: OwnerScope; loopId?: string; creating?: boolean; onBack: () => void; navigate: (route: ShellRoute) => void }
type PlanStep = { id: string; kind: string; title: string; objective?: string; status: string; artifact?: Record<string, unknown>; comments?: Array<{ text: string }> }
type PlanSession = { project_id: string; steps: PlanStep[]; design_error?: string }
type PlanState = { session: PlanSession | null; planner?: { active: boolean; stalled: boolean; retryable: boolean } }
type Report = { report: unknown; log: unknown }
type Validation = { can_start: boolean; errors?: Array<{ message?: string } | string>; warnings?: Array<{ message?: string } | string>; [key: string]: unknown }
const button: React.CSSProperties = { minHeight: 44, borderRadius: 9, padding: '8px 12px', font: 'inherit', cursor: 'pointer' }
const input: React.CSSProperties = { minHeight: 40, boxSizing: 'border-box', width: '100%', maxWidth: 720, padding: '8px 10px', font: 'inherit' }

export default function LoopWorkspace({ route, scope, loopId, creating = false, onBack, navigate }: Props) {
  const [rows, setRows] = useState<Loop[]>([])
  const [loop, setLoop] = useState<Loop | null>(null)
  const [plan, setPlan] = useState<PlanState | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [kind, setKind] = useState<'general' | 'goal' | 'code' | 'design' | 'research'>('general')
  const [name, setName] = useState('')
  const [task, setTask] = useState('')
  const [questionReply, setQuestionReply] = useState('')
  const [commentDrafts, setCommentDrafts] = useState<Record<string, string>>({})
  const [preflight, setPreflight] = useState<Validation | null>(null)
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [streamStatus, setStreamStatus] = useState<'connecting' | 'live' | 'disconnected'>('disconnected')
  const epoch = useRef(0)
  const ownerKey = scope.cacheKey
  const path = loopId ? `/api/loops/${encodeURIComponent(loopId)}` : ''

  useEffect(() => {
    let live = true
    const token = ++epoch.current
    const controller = new AbortController()
    setLoop(null); setPlan(null); setReport(null); setError(''); setNotice('')
    setStreamStatus(loopId ? 'connecting' : 'disconnected')
    const guarded = (fn: () => void) => { if (live && token === epoch.current && scope.cacheKey === ownerKey) fn() }
    if (loopId) {
      Promise.all([
        gatewayJson<Loop>(path, { signal: controller.signal }),
        gatewayJson<PlanState>(`${path}/plan-session`, { signal: controller.signal }).catch(() => null),
        gatewayJson<Report>(`${path}/report`, { signal: controller.signal }).catch(() => null),
      ]).then(([value, planState, reportValue]) => guarded(() => { setLoop(value); setPlan(planState); setReport(reportValue) }))
        .catch(reason => guarded(() => setError(message(reason))))
      let stream: EventSource | undefined
      try {
        stream = openGatewayEventSource(`${path}/stream`)
        stream.onopen = () => { if (live && token === epoch.current && scope.cacheKey === ownerKey) setStreamStatus('live') }
        stream.onmessage = () => {
          if (!live || token !== epoch.current || scope.cacheKey !== ownerKey) return
          void gatewayJson<Loop>(path, { signal: controller.signal }).then(value => guarded(() => setLoop(value))).catch(() => undefined)
        }
        stream.onerror = () => {
          if (live && token === epoch.current && scope.cacheKey === ownerKey) setStreamStatus('disconnected')
          stream?.close()
        }
      } catch { setStreamStatus('disconnected') }
      return () => { live = false; controller.abort(); stream?.close(); if (epoch.current === token) epoch.current += 1 }
    }
    gatewayJson<{ loops: Loop[] }>('/api/loops', { signal: controller.signal })
      .then(value => guarded(() => setRows(value.loops))).catch(reason => guarded(() => setError(message(reason))))
    return () => { live = false; controller.abort(); if (epoch.current === token) epoch.current += 1 }
  }, [ownerKey, loopId, path, revision])

  const create = async () => {
    if (!task.trim() || busy) return
    const token = epoch.current
    const current = () => token === epoch.current && scope.cacheKey === ownerKey
    setBusy(true); setError(''); setNotice('')
    try {
      const result = await gatewayJson<Validation>('/api/loops/validate', { method: 'POST', body: { name: name.trim() || task.trim().slice(0, 48), kind, task: task.trim() } })
      if (!current()) return
      if (!result.can_start) { setPreflight(result); setError('Native preflight found requirements to resolve. Your intake draft is preserved.'); return }
      setPreflight(result)
      if (!current()) return
      const created = await gatewayJson<Loop>('/api/loops', { method: 'POST', body: { name: name.trim() || task.trim().slice(0, 48), kind, task: task.trim() } })
      if (!current()) return
      if (!created.id) { setError('Gideon did not return a native loop ID. Your intake draft is preserved.'); return }
      navigate(createWorkRoute('loops/run', created.id, route.returnTo))
    } catch (reason) { if (current()) setError(`Native preflight or create did not complete. Your intake draft is preserved. ${message(reason)}`) }
    finally { if (current()) setBusy(false) }
  }

  const action = async (name: string, target: string, method: 'POST' | 'PATCH' = 'POST', body: unknown = {}) => {
    if (!loopId || busy) return
    const token = epoch.current
    setBusy(true); setError(''); setNotice('')
    try {
      await gatewayJson(target, { method, body })
      if (token !== epoch.current || scope.cacheKey !== ownerKey) return
      setNotice(`Native ${name} action accepted for loop ${loopId}.`)
      setRevision(value => value + 1)
    } catch (reason) {
      if (token === epoch.current && scope.cacheKey === ownerKey) setError(`Native ${name} outcome was not confirmed. Draft text is preserved. ${message(reason)}`)
    } finally { if (token === epoch.current && scope.cacheKey === ownerKey) setBusy(false) }
  }

  const submitReview = async (stepId: string, decision: 'approve' | 'comment') => {
    if (!loopId || busy) return
    const text = commentDrafts[stepId]?.trim() ?? ''
    if (decision === 'comment' && !text) { setError('Add review feedback before sending it.'); return }
    const token = epoch.current
    setBusy(true); setError(''); setNotice('')
    try {
      await gatewayJson(`${path}/plan/${decision}`, { method: 'POST', body: decision === 'approve' ? { step_id: stepId } : { step_id: stepId, text } })
      if (token !== epoch.current || scope.cacheKey !== ownerKey) return
      if (decision === 'comment') setCommentDrafts(value => ({ ...value, [stepId]: '' }))
      setNotice(`Native plan ${decision} recorded for step ${stepId}.`)
      setRevision(value => value + 1)
    } catch (reason) {
      if (token === epoch.current && scope.cacheKey === ownerKey) setError(`Plan ${decision} was not confirmed. Review text is preserved. ${message(reason)}`)
    } finally { if (token === epoch.current && scope.cacheKey === ownerKey) setBusy(false) }
  }

  const title = creating ? 'Start ongoing work' : loopId ? loop?.name ?? 'Loop controls' : 'Ongoing work'
  const open = (id: string) => navigate(createWorkRoute('loops/run', id, route.returnTo))
  const frameState = error && !loop && !creating && loopId ? { kind: 'error' as const, message: error, onRetry: () => setRevision(value => value + 1) }
    : !creating && loopId && !loop ? { kind: 'loading' as const, message: 'Recovering native loop status, plan, and report…' } : { kind: 'ready' as const }

  return <WorkspaceFrame route={route} mode="full" title={title} onBack={onBack} state={frameState}
    actions={<div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {loopId && <button style={button} type="button" onClick={() => navigate(createWorkRoute('loops', undefined, route.returnTo))}>Catalogue</button>}
      {!creating && <button style={button} type="button" onClick={() => navigate(createWorkRoute('loops/new', undefined, route.returnTo))}>Start ongoing work</button>}
      {!creating && <button style={button} type="button" onClick={() => setRevision(value => value + 1)}>Refresh native state</button>}
    </div>}>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {creating && <section aria-label="New loop intake" style={{ display: 'grid', gap: 12, maxWidth: 760 }}>
      <label>Work type<select style={input} value={kind} disabled={busy} onChange={event => { setKind(event.target.value as typeof kind); setPreflight(null) }}>
        <option value="general">General</option><option value="goal">Goal</option><option value="code">Code</option><option value="design">Design</option><option value="research">Research</option>
      </select></label>
      <label>Name<input style={input} value={name} disabled={busy} onChange={event => setName(event.target.value)} /></label>
      <label>Objective<textarea style={{ ...input, minHeight: 110 }} value={task} disabled={busy} onChange={event => { setTask(event.target.value); setPreflight(null) }} /></label>
      {preflight && <div role={preflight.can_start ? 'status' : 'alert'}><p>Native preflight: {preflight.can_start ? 'ready to create' : 'requirements remain'}</p>
        {preflight.errors?.map((item, index) => <p key={index}>{typeof item === 'string' ? item : item.message}</p>)}
        {preflight.warnings?.map((item, index) => <p key={index}>Readiness warning: {typeof item === 'string' ? item : item.message}</p>)}
      </div>}
      <button style={button} type="button" disabled={busy || task.trim().length < 12} onClick={() => void create()}>{busy ? 'Checking native readiness…' : 'Check readiness and create'}</button>
    </section>}
    {!creating && !loopId && <section aria-label="Loop catalogue"><h2>Native loop records</h2>
      {rows.length ? <ul>{rows.map(row => <li key={row.id}><button style={button} type="button" onClick={() => open(row.id)}>{row.name} · {row.kind} · {row.status} · <code>{row.id}</code></button></li>)}</ul>
        : <p>No native loops are recorded.</p>}
    </section>}
    {!creating && loopId && loop && <div style={{ display: 'grid', gap: 14 }}>
      <section aria-label="Loop status"><h2>Live progress</h2><p>Loop ID: <code>{loop.id}</code> · Kind: {loop.kind} · Status: {loop.status}</p>
        <p role={streamStatus === 'disconnected' ? 'alert' : 'status'}>{streamStatus === 'live'
          ? 'Live updates connected.'
          : streamStatus === 'connecting'
            ? 'Connecting to native live updates…'
            : 'Live updates disconnected. Showing the last native snapshot; reconnect to refresh and resume updates.'}</p>
        {streamStatus === 'disconnected' && <button style={button} type="button" onClick={() => setRevision(value => value + 1)}>Reconnect live updates</button>}
        <p>Cycles: {loop.total_cycles} · Workspace: {loop.workspace_dir || 'not assigned'} · Project: {loop.project_id || 'none'}</p>
        {loop.error_message && <p role="alert">{loop.error_message}</p>}{loop.pending_question && <p role="status">Question: {typeof loop.pending_question === 'string' ? loop.pending_question : loop.pending_question.question}</p>}
      </section>
      <section aria-label="Loop plan and review"><h2>Plan and review</h2>
        {plan?.planner && <p>Planner: {plan.planner.active ? 'working' : plan.planner.stalled ? 'stalled' : 'waiting'}{plan.planner.retryable ? ' · retry available' : ''}</p>}
        {plan?.session?.design_error && <p role="alert">Plan design error: {plan.session.design_error}</p>}
        {plan?.session?.steps.length ? <ol>{plan.session.steps.map(step => <li key={step.id}>
          <h3>{step.title} · {step.status}</h3>{step.objective && <p>{step.objective}</p>}
          {step.artifact && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(step.artifact, null, 2)}</pre>}
          {step.comments?.map((comment, index) => <p key={index}>Review note: {comment.text}</p>)}
          {step.status === 'awaiting_review' && <div style={{ display: 'grid', gap: 8 }}>
            <label>Review feedback<textarea style={{ ...input, minHeight: 80 }} value={commentDrafts[step.id] ?? ''} disabled={busy} onChange={event => setCommentDrafts(value => ({ ...value, [step.id]: event.target.value }))} /></label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><button style={button} disabled={busy} onClick={() => void submitReview(step.id, 'approve')}>Approve step</button>
              <button style={button} disabled={busy || !(commentDrafts[step.id] ?? '').trim()} onClick={() => void submitReview(step.id, 'comment')}>Send feedback</button></div>
          </div>}
        </li>)}</ol> : <p>No native planning review is recorded.</p>}
        {loop.status === 'intake' && <button style={button} disabled={busy} onClick={() => void action('plan start', `${path}/plan/start`)}>Begin plan walkthrough</button>}
      </section>
      <section aria-label="Live loop controls"><h2>Controls</h2>
        {loop.status === 'ready' || loop.status === 'review' ? <button style={button} disabled={busy} onClick={() => void action('start', path, 'PATCH', { action: 'start' })}>Start loop</button> : null}
        {loop.status === 'running' && <button style={button} disabled={busy} onClick={() => void action('pause', path, 'PATCH', { action: 'pause' })}>Pause loop</button>}
        {['paused', 'stagnant', 'blocked', 'needs_input', 'failed'].includes(loop.status) && <button style={button} disabled={busy} onClick={() => void action('resume', path, 'PATCH', { action: 'resume' })}>Resume loop</button>}
        {['intake', 'planning', 'running', 'paused', 'stagnant', 'blocked', 'needs_input', 'failed'].includes(loop.status) && <button style={button} disabled={busy} onClick={() => void action('stop', path, 'PATCH', { action: 'stop' })}>Stop loop</button>}
        {loop.status === 'running' && <label><input type="checkbox" checked={Boolean(loop.autopilot)} disabled={busy} onChange={event => void action('autopilot', `${path}/autopilot`, 'POST', { on: event.target.checked })} /> Automatic progress</label>}
      </section>
      <section aria-label="Loop question and steering"><h2>Questions and steering</h2>
        <label>Answer or instruction<textarea style={{ ...input, minHeight: 90 }} value={questionReply} disabled={busy} onChange={event => setQuestionReply(event.target.value)} /></label>
        <button style={button} disabled={busy || !questionReply.trim() || !loop.status || ['intake', 'planning', 'complete', 'stopped'].includes(loop.status)} onClick={() => void action('steer', `${path}/nudge`, 'POST', { text: questionReply })}>Send answer or instruction</button>
      </section>
      <section aria-label="Loop report"><h2>Report and source records</h2>
        {report ? <><p>Native report: {String(report.report || 'No report has been recorded.')}</p><p>Work log: {String(report.log || 'No findings log has been recorded.')}</p></>
          : <p>The native report is currently unavailable.</p>}
        <p>Source IDs: loop <code>{loop.id}</code>{loop.tasks_project_id && <> · tasks project <code>{loop.tasks_project_id}</code></>}{loop.linked_task_ids?.map(id => <> · task <code key={id}>{id}</code></>)}</p>
      </section>
    </div>}
  </WorkspaceFrame>
}

function message(error: unknown): string { return error instanceof Error && error.message ? error.message : 'Native loop data is unavailable.' }
