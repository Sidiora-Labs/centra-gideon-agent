import React, { useEffect, useMemo, useState } from 'react'
import type { WorkflowContinuation, WorkflowOutboxEntry, WorkflowReviewPayload, WorkflowRunDeliverable, WorkflowRunDetailData, WorkflowRunSummary, WorkflowTriageResult } from '../../../../console/src/shared/data/api'
import type { OwnerScope } from '../../shared/auth.web'
import type { ShellRoute, ShellReturnContext } from '../../shared/shell/shellRoutes'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { gatewayJson } from '../../shared/transport.web'
import { createWorkRoute } from './workRouteModel'
import { readReviewIntent, timelineEvents, unresolvedReviewMessage, writeReviewIntent, type ReviewIntent } from './workflowRunState'

type Props = { route: ShellRoute; scope: OwnerScope; runId?: string; onBack: () => void; navigate: (route: ShellRoute) => void }
type Runs = { runs: WorkflowRunSummary[]; total: number }
const button: React.CSSProperties = { minHeight: 44, borderRadius: 9, padding: '8px 12px', font: 'inherit', cursor: 'pointer' }
const runPath = (id: string) => `/api/workflows/runs/${encodeURIComponent(id)}`

export default function WorkflowRunWorkspace({ route, scope, runId, onBack, navigate }: Props) {
  const [rows, setRows] = useState<WorkflowRunSummary[]>([])
  const [detail, setDetail] = useState<WorkflowRunDetailData | null>(null)
  const [continuations, setContinuations] = useState<WorkflowContinuation[]>([])
  const [review, setReview] = useState<WorkflowReviewPayload | null>(null)
  const [outbox, setOutbox] = useState<WorkflowOutboxEntry[]>([])
  const [deliverable, setDeliverable] = useState<WorkflowRunDeliverable | null>(null)
  const [intent, setIntent] = useState<ReviewIntent | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const stable = useMemo(() => `${scope.cacheKey}:${runId ?? 'catalogue'}`, [scope.cacheKey, runId])

  useEffect(() => {
    let live = true
    const controller = new AbortController()
    setError(''); setNotice(''); setDetail(null); setReview(null); setContinuations([])
    if (!runId) {
      gatewayJson<Runs>('/api/workflows/runs', { signal: controller.signal }).then(value => {
        if (live) setRows(value.runs)
      }).catch(reason => { if (live) setError(message(reason)) })
    } else {
      setIntent(readReviewIntent(scope.cacheKey, runId))
      Promise.all([
        gatewayJson<WorkflowRunDetailData>(runPath(runId), { signal: controller.signal }),
        gatewayJson<{ continuations: WorkflowContinuation[] }>(`${runPath(runId)}/continuations`, { signal: controller.signal }).catch(() => ({ continuations: [] })),
        gatewayJson<WorkflowReviewPayload>(`${runPath(runId)}/review`, { signal: controller.signal }).catch(() => null),
        gatewayJson<{ files: WorkflowOutboxEntry[] }>(`${runPath(runId)}/outbox`, { signal: controller.signal }).catch(() => ({ files: [] })),
        gatewayJson<WorkflowRunDeliverable>(`${runPath(runId)}/deliverable`, { signal: controller.signal }).catch(() => null),
      ]).then(([run, pending, findings, artifacts, handoff]) => {
        if (!live) return
        setDetail(run); setContinuations(pending.continuations); setReview(findings); setOutbox(artifacts.files); setDeliverable(handoff)
      }).catch(reason => { if (live) setError(message(reason)) })
    }
    return () => { live = false; controller.abort() }
  }, [stable, revision])

  const act = async (label: string, path: string, body: unknown = {}) => {
    if (!runId || busy) return
    setBusy(true); setError(''); setNotice('')
    try {
      await gatewayJson(path, { method: 'POST', body })
      setNotice(`${label} request accepted by Gideon for run ${runId}.`)
      setRevision(value => value + 1)
    } catch (reason) { setError(`${label} outcome was not received for run ${runId}; check native run status before retrying. ${message(reason)}`) }
    finally { setBusy(false) }
  }

  const triage = async (key: string, outcome: 'accept' | 'reject') => {
    if (!runId || !review || busy || intent?.state === 'unknown' || intent?.state === 'pending') return
    const next: ReviewIntent = { runId, decisions: [{ key, outcome }], state: 'pending', updatedAt: Date.now() }
    writeReviewIntent(scope.cacheKey, next); setIntent(next); setBusy(true); setError(''); setNotice('')
    try {
      const receipt = await gatewayJson<WorkflowTriageResult>(`${runPath(runId)}/review/triage`, {
        method: 'POST', body: { decisions: next.decisions },
      })
      const complete: ReviewIntent = { ...next, state: 'complete', receipt, updatedAt: Date.now() }
      writeReviewIntent(scope.cacheKey, complete); setIntent(complete)
      setNotice(unresolvedReviewMessage(complete, review))
      setRevision(value => value + 1)
    } catch (reason) {
      const unknown: ReviewIntent = { ...next, state: 'unknown', updatedAt: Date.now() }
      writeReviewIntent(scope.cacheKey, unknown); setIntent(unknown)
      setError(`${unresolvedReviewMessage(unknown, review)} ${message(reason)}`)
    } finally { setBusy(false) }
  }

  const title = runId ? detail?.workflow ?? 'Workflow run' : 'Workflow runs'
  const frameState = error && !detail && runId ? { kind: 'error' as const, message: error, onRetry: () => setRevision(value => value + 1) }
    : !runId && error ? { kind: 'error' as const, message: error, onRetry: () => setRevision(value => value + 1) }
      : runId && !detail ? { kind: 'loading' as const, message: 'Recovering native workflow run…' } : { kind: 'ready' as const }

  return <WorkspaceFrame route={route} mode="full" title={title} onBack={onBack}
    actions={<button style={button} type="button" onClick={() => setRevision(value => value + 1)}>Refresh</button>} state={frameState}>
    {!runId && <section aria-label="Workflow run catalogue"><p>Native run history</p>
      {rows.length ? <ul>{rows.map(row => <li key={row.id}><button type="button" style={button}
        onClick={() => navigate(createWorkRoute('workflows/run', row.id, route.returnTo ?? {
          destination: route.destination, record: route.record, placement: route.placement, sessionId: route.sessionId,
        } satisfies ShellReturnContext))}>{row.workflow_name} · {row.id} · {row.status}</button></li>)}</ul> : <p>No workflow runs are recorded.</p>}
    </section>}
    {detail && runId && <div style={{ display: 'grid', gap: 16, minWidth: 0 }}>
      {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      <p>Run ID: <code>{runId}</code> · Workflow: <code>{detail.workflow}</code> · Status: {detail.status} · Revision: {detail.spec_version}</p>
      {detail.project_id && <p>Project ID: <code>{detail.project_id}</code></p>}
      <section aria-label="Run timeline"><h2>Timeline</h2><ol>{timelineEvents(runId, detail).map(event => <li key={event.id}>
        {event.href ? <a href={event.href}>{event.label}</a> : <strong>{event.label}</strong>} · {event.status} · <code>{event.id}</code>
      </li>)}</ol></section>
      <section aria-label="Run controls"><h2>Run controls</h2>
        <button style={button} disabled={busy || detail.status === 'complete' || detail.status === 'cancelled'} onClick={() => void act('Cancel', `${runPath(runId)}/cancel`)}>Cancel run</button>
        {detail.status === 'paused' && <button style={button} disabled={busy} onClick={() => void act('Resume', `${runPath(runId)}/resume`)}>Resume run</button>}
      </section>
      <section aria-label="Pending continuations"><h2>Continuations</h2>{continuations.length ? <ul>{continuations.map(item => <li key={item.resume_token}>
        <p>{item.ask.prompt ?? item.handoff.scope ?? 'Input requested'} · node <code>{item.node_id}</code> · path <code>{item.instance_path}</code></p>
        <p>Continuation ID: <code>{item.resume_token}</code>{item.expired ? ' · expired' : ' · pending'}</p>
        {!item.expired && <button style={button} disabled={busy} onClick={() => void act('Resume continuation', `${runPath(runId)}/resume`, { resume_token: item.resume_token })}>Resume continuation</button>}
      </li>)}</ul> : <p>No pending continuation.</p>}</section>
      <section aria-label="Exact run review"><h2>Review</h2>
        {intent && <p role={intent.state === 'unknown' ? 'alert' : 'status'}>{unresolvedReviewMessage(intent, review)}</p>}
        {review?.findings.length ? <ul>{review.findings.map(finding => <li key={finding.key}>
          <p>{finding.severity}: {finding.problem} · {finding.anchor_state} · finding <code>{finding.key}</code></p>
          <p>Source: <code>{finding.origin_run_id}</code> / node <code>{finding.origin_node_id}</code> · {finding.resolved_path}:{finding.resolved_line}</p>
          <button style={button} disabled={busy || intent?.state === 'unknown' || intent?.state === 'pending'} onClick={() => void triage(finding.key, 'accept')}>Accept finding</button>
          <button style={button} disabled={busy || intent?.state === 'unknown' || intent?.state === 'pending'} onClick={() => void triage(finding.key, 'reject')}>Reject finding</button>
        </li>)}</ul> : <p>No review findings for this run.</p>}
      </section>
      <section aria-label="Run outputs"><h2>Outputs and artifacts</h2><ul>{detail.nodes.map(node => <li key={node.instance_path + node.node_id}>
        <a href={`/api/workflows/runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(node.node_id)}`}>Output for node {node.node_id}</a> · run <code>{runId}</code> · path <code>{node.instance_path}</code>
      </li>)}</ul>
        <ul>{outbox.map(file => <li key={file.slug}><a href={`/api/artifacts/${encodeURIComponent(file.slug)}`}>{file.artifact}</a> · artifact <code>{file.slug}</code> · node <code>{file.node_id}</code> · {file.kind}</li>)}</ul>
        {deliverable && <p>Deliverable for run <code>{deliverable.run_id}</code>: report {deliverable.report.present ? deliverable.report.name : 'not recorded'}; log {deliverable.log.present ? deliverable.log.name : 'not recorded'}.</p>}
      </section>
    </div>}
  </WorkspaceFrame>
}

function message(error: unknown): string { return error instanceof Error && error.message ? error.message : 'Native workflow data is unavailable.' }
