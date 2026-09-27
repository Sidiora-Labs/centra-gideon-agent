import React, { useEffect, useMemo, useRef, useState } from 'react'
import type { TaskComment, TaskGraphData, TaskItem } from '../../../../console/src/shared/data/api'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { createShellRoute, serializeShellRoute, type ShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { WorkspaceFrame, type WorkspaceFrameState } from '../../shared/shell/WorkspaceFrame.web'
import { gatewayJson, GatewayError } from '../../shared/transport.web'
import { WorkClient, type WorkEntry, type WorkRead } from './workClient'

type TaskDraft = { title: string; description: string; status: string; assignee: string; priority: string }
type Detail = WorkRead<WorkEntry<'task'>> | { state: 'loading' }

const draftOf = (task: TaskItem): TaskDraft => ({ title: task.title, description: task.description ?? '',
  status: task.status, assignee: task.assignee ?? '', priority: task.priority ?? 'medium' })
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The action failed.'
const path = (id: string) => `/api/tasks/${encodeURIComponent(id)}`
const button: React.CSSProperties = { minHeight: 44, borderRadius: 10, padding: '8px 14px', font: 'inherit', cursor: 'pointer' }
const input: React.CSSProperties = { width: '100%', boxSizing: 'border-box', minHeight: 44, borderRadius: 8,
  padding: '8px 10px', font: 'inherit' }

export default function TaskWorkspace(props: ModuleProps) {
  const key = JSON.stringify([props.scope.cacheKey, serializeShellRoute(props.route)])
  return <TaskWorkspaceInstance key={key} {...props} />
}

function TaskWorkspaceInstance({ route, scope, navigate, onReturn }: ModuleProps) {
  const { palette } = useShellTheme()
  const id = route.record?.kind === 'task' ? route.record.id : ''
  const client = useMemo(() => new WorkClient(scope), [scope.cacheKey])
  const [detail, setDetail] = useState<Detail>({ state: 'loading' })
  const [draft, setDraft] = useState<TaskDraft | null>(null)
  const [comments, setComments] = useState<TaskComment[]>([])
  const [graph, setGraph] = useState<TaskGraphData | null>(null)
  const [commentDraft, setCommentDraft] = useState('')
  const [notice, setNotice] = useState('')
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const epoch = useRef(0)
  const source: ShellReturnContext = route.returnTo ?? { destination: route.destination, record: route.record,
    placement: route.placement, sessionId: route.sessionId }
  const entry = 'value' in detail ? detail.value : null
  const task = entry?.record
  const taskReceipts = task as (TaskItem & { attempts?: Array<Record<string, unknown>>; evidence?: Array<Record<string, unknown>> }) | undefined

  useEffect(() => {
    const current = ++epoch.current
    const abort = new AbortController()
    setDetail({ state: 'loading' }); setDraft(null); setComments([]); setGraph(null); setProblem(''); setNotice('')
    if (id) {
      client.detail('task', id, abort.signal).then(result => {
        if (epoch.current !== current || abort.signal.aborted) return
        setDetail(result)
        if ('value' in result) setDraft(draftOf(result.value.record))
      })
      gatewayJson<{ comments: TaskComment[] }>(`${path(id)}/comments`, { signal: abort.signal })
        .then(result => { if (epoch.current === current) setComments(result.comments) })
        .catch(error => { if (epoch.current === current && !abort.signal.aborted) setProblem(`Comments: ${errorText(error)}`) })
      gatewayJson<TaskGraphData>('/api/tasks/graph', { signal: abort.signal })
        .then(result => { if (epoch.current === current) setGraph(result) })
        .catch(error => { if (epoch.current === current && !abort.signal.aborted) setProblem(`Dependencies: ${errorText(error)}`) })
    }
    return () => { abort.abort(); epoch.current++ }
  }, [client, id, refresh])

  const returnFromTask = () => {
    if (onReturn) onReturn()
    else if (route.returnTo) navigate(createShellRoute(route.returnTo.destination, {
      view: route.returnTo.record ? 'detail' : route.returnTo.placement ? 'workspace' : 'list',
      record: route.returnTo.record, placement: route.returnTo.placement, sessionId: route.returnTo.sessionId,
    }))
    else navigate(createShellRoute('activity', { view: 'list', placement: { id: 'tasks' } }))
  }
  const goTask = (taskId: string) => navigate(createShellRoute('activity', {
    view: 'detail', placement: { id: 'tasks' }, record: { kind: 'task', id: taskId }, returnTo: source,
  }))
  const save = async () => {
    if (!id || !entry || !draft || busy) return
    const current = epoch.current
    setBusy(true); setProblem(''); setNotice('')
    try {
      const latest = await gatewayJson<TaskItem>(path(id))
      if (latest.id !== id || (entry.identity.revision && latest.updated_at !== entry.identity.revision) ||
        JSON.stringify(draftOf(latest)) !== JSON.stringify(draftOf(entry.record))) {
        throw new Error('This task changed since you opened it. Your draft is preserved; review the latest record before saving.')
      }
      const updated = await gatewayJson<TaskItem>(path(id), { method: 'PUT', body: draft })
      if (updated.id !== id) throw new Error('Gideon returned a different task. Your draft is preserved.')
      if (epoch.current !== current) return
      setDetail({ state: 'ready', value: { ...entry, record: updated,
        title: updated.title, status: updated.status, identity: { ...entry.identity, revision: updated.updated_at ?? null } }, checkedAt: Date.now() })
      setDraft(draftOf(updated)); setNotice('Task saved.')
    } catch (error) {
      if (epoch.current === current) setProblem(error instanceof GatewayError && (error.status === 401 || error.status === 403)
        ? `Access denied. Your draft for ${id} is preserved. ${error.message}` : errorText(error))
    } finally { if (epoch.current === current) setBusy(false) }
  }
  const addComment = async () => {
    if (!id || !commentDraft.trim() || busy) return
    const current = epoch.current
    setBusy(true); setProblem(''); setNotice('')
    try {
      const comment = await gatewayJson<TaskComment>(`${path(id)}/comments`, { method: 'POST', body: { body: commentDraft } })
      if (comment.task_id !== id) throw new Error('Gideon returned a comment for a different task.')
      if (epoch.current !== current) return
      setComments(rows => [...rows, comment]); setCommentDraft(''); setNotice('Comment added.')
    } catch (error) { if (epoch.current === current) setProblem(`Comment not added; your text is preserved. ${errorText(error)}`) }
    finally { if (epoch.current === current) setBusy(false) }
  }
  let frameState: WorkspaceFrameState = { kind: 'ready' }
  if (!id) frameState = { kind: 'error', message: 'A native task ID is required.' }
  else if (detail.state === 'loading') frameState = { kind: 'loading', message: 'Loading task…' }
  else if (!('value' in detail)) frameState = detail.state === 'denied' ? { kind: 'denied', message: detail.reason }
    : { kind: 'error', message: detail.reason, onRetry: () => setRefresh(n => n + 1) }
  const card: React.CSSProperties = { background: palette.card, border: `1px solid ${palette.line}`, borderRadius: 14,
    padding: 16, minWidth: 0 }
  const label: React.CSSProperties = { display: 'grid', gap: 6, color: palette.text, fontWeight: 600 }
  const action: React.CSSProperties = { ...button, background: palette.blueDark, color: palette.card, border: 0 }
  const dependencies = task?.dependencies?.map(row => row.depends_on_task_id).filter(Boolean)
    ?? task?.depends_on ?? []
  const graphEdges = graph?.edges.filter(row => row.from === id || row.to === id) ?? []
  return <WorkspaceFrame route={route} mode="full" title={task?.title ?? 'Task workspace'} onBack={returnFromTask}
    actions={<button type="button" style={{ ...button, background: palette.secondary, border: `1px solid ${palette.line}`, color: palette.text }}
      onClick={() => setRefresh(n => n + 1)}>Refresh</button>} state={frameState}>
    <div style={{ display: 'grid', gap: 16, paddingBottom: 24, color: palette.text }}>
      {detail.state === 'stale' && <p role="status">Showing a stale task record. Review current data before saving.</p>}
      {task && draft && <>
        <p style={{ margin: 0, color: palette.muted, overflowWrap: 'anywhere' }}>Task ID: {id} · Owner: {scope.ownerId} · Updated: {task.updated_at ?? 'unknown'}</p>
        {problem && <p role="alert" style={{ ...card, color: palette.danger, background: palette.dangerSurface }}>{problem}</p>}
        {notice && <p role="status" style={card}>{notice}</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 16 }}>
          <section aria-label="Task plan" style={card}>
            <h2 style={{ marginTop: 0 }}>Plan and owner</h2>
            <div style={{ display: 'grid', gap: 12 }}>
              <label style={label}>Title<input style={input} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
              <label style={label}>Description<textarea style={input} rows={4} value={draft.description} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
              <label style={label}>Status<select style={input} value={draft.status} onChange={event => setDraft({ ...draft, status: event.target.value })}>
                {['open', 'in_progress', 'blocked', 'done', 'cancelled', 'skipped'].map(value => <option key={value} value={value}>{value.replace('_', ' ')}</option>)}
              </select></label>
              <label style={label}>Assignee<input style={input} value={draft.assignee} onChange={event => setDraft({ ...draft, assignee: event.target.value })} /></label>
              <label style={label}>Priority<select style={input} value={draft.priority} onChange={event => setDraft({ ...draft, priority: event.target.value })}>
                {['critical', 'high', 'medium', 'low', 'trivial'].map(value => <option key={value}>{value}</option>)}
              </select></label>
              <button type="button" style={action} disabled={busy || !draft.title.trim() || detail.state === 'stale'} onClick={save}>Save task</button>
            </div>
          </section>
          <section aria-label="Task review" style={card}>
            <h2 style={{ marginTop: 0 }}>Review</h2>
            <p>Status: {task.status} · Owner: {task.assignee || 'Unassigned'}</p>
            {task.block_reason?.message && <p role="status">{task.block_reason.message}</p>}
            <h3>Action plan</h3><ol>{task.action_plan?.length ? task.action_plan.map((step, index) =>
              <li key={index}>{step.content || step.description || `Step ${index + 1}`}{step.completed ? ' · complete' : ''}</li>) : <li>No plan steps recorded.</li>}</ol>
            <h3>Exit criteria</h3><ul>{task.exit_criteria?.length ? task.exit_criteria.map((criterion, index) =>
              <li key={index}>{criterion.description} · {criterion.status ?? 'incomplete'}</li>) : <li>No criteria recorded.</li>}</ul>
            <h3>Dependencies</h3><ul>{dependencies.length ? dependencies.map(dep => <li key={dep}>
              <button type="button" style={{ ...button, color: palette.blueDark, background: 'transparent', border: 0 }} onClick={() => goTask(dep!)}>{dep}</button></li>)
              : <li>No dependencies recorded.</li>}</ul>
            <p>{graphEdges.length} related graph edge{graphEdges.length === 1 ? '' : 's'}.</p>
            <h3>Attempts and evidence</h3>
            <p>{taskReceipts?.attempts?.length ?? 0} attempt{taskReceipts?.attempts?.length === 1 ? '' : 's'} recorded.</p>
            {taskReceipts?.attempts?.length ? <ol>{taskReceipts.attempts.map((attempt, index) =>
              <li key={index}>{String(attempt.status ?? attempt.outcome ?? attempt.summary ?? `Attempt ${index + 1}`)}</li>)}</ol> : null}
            <p>{taskReceipts?.evidence?.length ?? 0} evidence item{taskReceipts?.evidence?.length === 1 ? '' : 's'} recorded.</p>
            {taskReceipts?.evidence?.length ? <ul>{taskReceipts.evidence.map((item, index) =>
              <li key={index}>{String(item.title ?? item.summary ?? item.description ?? item.path ?? `Evidence ${index + 1}`)}</li>)}</ul> : null}
            {task.execution_notes?.length ? <ul>{task.execution_notes.map((note, index) => <li key={index}>{note.content}</li>)}</ul> : null}
          </section>
        </div>
        <section aria-label="Task comments" style={card}>
          <h2 style={{ marginTop: 0 }}>Comments</h2>
          <ul>{comments.length ? comments.map(comment => <li key={comment.id}><strong>{comment.author || 'Owner'}</strong> · {comment.body}</li>)
            : <li>No comments yet.</li>}</ul>
          <label style={label}>Add a comment<textarea style={input} rows={3} value={commentDraft}
            onChange={event => setCommentDraft(event.target.value)} /></label>
          <button type="button" style={{ ...action, marginTop: 10 }} disabled={busy || !commentDraft.trim()} onClick={addComment}>Post comment</button>
        </section>
      </>}
    </div>
  </WorkspaceFrame>
}
