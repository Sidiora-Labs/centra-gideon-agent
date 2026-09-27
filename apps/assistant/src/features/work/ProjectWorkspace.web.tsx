import React, { useEffect, useMemo, useRef, useState } from 'react'
import type { ProjectItem, ProjectLinkedItem, TaskItem, TaskListItem, WorkBoard } from '../../../../console/src/shared/data/api'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { createShellRoute, serializeShellRoute, type ShellReturnContext } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { WorkspaceFrame, type WorkspaceFrameState } from '../../shared/shell/WorkspaceFrame.web'
import { gatewayJson, GatewayError } from '../../shared/transport.web'
import { WorkClient, type WorkEntry, type WorkRead } from './workClient'

type ProjectDraft = { name: string; brief: string; agent_instructions_template: string; status: 'active' | 'archived' }
type Detail = WorkRead<WorkEntry<'project'>> | { state: 'loading' }
type Linked = { loops: ProjectLinkedItem[]; code: ProjectLinkedItem[]; artifacts: { slug: string; name: string; kind: string }[];
  chats: { key: string; title: string; running: boolean }[]; knowledge: { id?: string; title?: string; name?: string }[] }
const draftOf = (row: ProjectItem): ProjectDraft => ({ name: row.name, brief: row.brief ?? '',
  agent_instructions_template: row.agent_instructions_template ?? '', status: row.status ?? 'active' })
const errorText = (error: unknown) => error instanceof Error ? error.message : 'The action failed.'
const button: React.CSSProperties = { minHeight: 44, borderRadius: 10, padding: '8px 14px', font: 'inherit', cursor: 'pointer' }
const input: React.CSSProperties = { width: '100%', boxSizing: 'border-box', minHeight: 44, borderRadius: 8,
  padding: '8px 10px', font: 'inherit' }

export default function ProjectWorkspace(props: ModuleProps) {
  const key = JSON.stringify([props.scope.cacheKey, serializeShellRoute(props.route)])
  return <ProjectWorkspaceInstance key={key} {...props} />
}

function ProjectWorkspaceInstance({ route, scope, navigate, onReturn }: ModuleProps) {
  const { palette } = useShellTheme()
  const id = route.record?.kind === 'project' ? route.record.id : ''
  const client = useMemo(() => new WorkClient(scope), [scope.cacheKey])
  const [detail, setDetail] = useState<Detail>({ state: 'loading' })
  const [draft, setDraft] = useState<ProjectDraft | null>(null)
  const [linked, setLinked] = useState<Linked | null>(null)
  const [board, setBoard] = useState<WorkBoard | null>(null)
  const [lists, setLists] = useState<TaskListItem[]>([])
  const [tasks, setTasks] = useState<TaskItem[]>([])
  const [newTask, setNewTask] = useState('')
  const [problem, setProblem] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const epoch = useRef(0)
  const source: ShellReturnContext = route.returnTo ?? { destination: route.destination, record: route.record,
    placement: route.placement, sessionId: route.sessionId }
  const entry = 'value' in detail ? detail.value : null
  const project = entry?.record
  const path = `/api/projects/${encodeURIComponent(id)}`

  useEffect(() => {
    const current = ++epoch.current
    const abort = new AbortController()
    setDetail({ state: 'loading' }); setDraft(null); setLinked(null); setBoard(null); setLists([]); setTasks([])
    setProblem(''); setNotice('')
    if (id) {
      client.detail('project', id, abort.signal).then(result => {
        if (current !== epoch.current || abort.signal.aborted) return
        setDetail(result)
        if ('value' in result) setDraft(draftOf(result.value.record))
      })
      Promise.all([
        gatewayJson<Linked>(`${path}/linked`, { signal: abort.signal }),
        gatewayJson<WorkBoard>(`${path}/work`, { signal: abort.signal }),
        gatewayJson<{ task_lists: TaskListItem[] }>(`/api/task-lists?project_id=${encodeURIComponent(id)}`, { signal: abort.signal }),
      ]).then(async ([links, work, membership]) => {
        const taskPages = await Promise.all(membership.task_lists.map(list =>
          gatewayJson<{ tasks: TaskItem[] }>(`/api/tasks?task_list_id=${encodeURIComponent(list.id)}`, { signal: abort.signal })))
        if (current !== epoch.current) return
        setLinked(links); setBoard(work); setLists(membership.task_lists); setTasks(taskPages.flatMap(page => page.tasks))
      }).catch(error => { if (current === epoch.current && !abort.signal.aborted) setProblem(`Linked work: ${errorText(error)}`) })
    }
    return () => { abort.abort(); epoch.current++ }
  }, [client, id, refresh])

  const back = () => {
    if (onReturn) onReturn()
    else if (route.returnTo) navigate(createShellRoute(route.returnTo.destination, {
      view: route.returnTo.record ? 'detail' : route.returnTo.placement ? 'workspace' : 'list',
      record: route.returnTo.record, placement: route.returnTo.placement, sessionId: route.returnTo.sessionId,
    }))
    else navigate(createShellRoute('apps', { view: 'workspace', placement: { id: 'projects' } }))
  }
  const openTask = (taskId: string) => navigate(createShellRoute('activity', { view: 'detail',
    placement: { id: 'tasks' }, record: { kind: 'task', id: taskId }, returnTo: source }))
  const openCode = () => navigate(createShellRoute('apps', { view: 'detail', placement: { id: 'projects/detail' },
    record: { kind: 'project', id }, returnTo: source }))
  const save = async () => {
    if (!id || !entry || !draft || busy) return
    const current = epoch.current
    setBusy(true); setProblem(''); setNotice('')
    try {
      const latest = await gatewayJson<ProjectItem>(path)
      if (latest.id !== id || (entry.identity.revision && latest.updated_at !== entry.identity.revision) ||
        JSON.stringify(draftOf(latest)) !== JSON.stringify(draftOf(entry.record))) {
        throw new Error('This project changed since you opened it. Your draft is preserved; review the latest record before saving.')
      }
      if (current !== epoch.current) return
      const updated = await gatewayJson<ProjectItem>(path, { method: 'PUT',
        body: { ...draft, ...(entry.identity.revision ? { expected_revision: entry.identity.revision } : {}) } })
      if (updated.id !== id) throw new Error('Gideon returned a different project. Your draft is preserved.')
      if (current !== epoch.current) return
      setDetail({ state: 'ready', value: { ...entry, record: updated, title: updated.name,
        status: updated.status ?? null, identity: { ...entry.identity, revision: updated.updated_at ?? null } }, checkedAt: Date.now() })
      setDraft(draftOf(updated)); setNotice('Project context saved.')
    } catch (error) {
      if (current === epoch.current) setProblem(error instanceof GatewayError && error.status === 409 && error.code === 'version_conflict'
        ? `This project changed while saving. Your draft for ${id} is preserved; refresh the source before retrying.`
        : error instanceof GatewayError && (error.status === 401 || error.status === 403)
          ? `Access denied. Your draft for ${id} is preserved. ${error.message}` : errorText(error))
    } finally { if (current === epoch.current) setBusy(false) }
  }
  const addTask = async () => {
    if (!id || !newTask.trim() || busy) return
    const current = epoch.current
    setBusy(true); setProblem(''); setNotice('')
    try {
      if (current !== epoch.current) return
      const created = await gatewayJson<TaskItem>('/api/tasks', { method: 'POST',
        body: { title: newTask.trim(), project_id: id } })
      if (!created.id) throw new Error('Gideon did not return a task ID.')
      if (current !== epoch.current) return
      setNewTask(''); setNotice(`Task created: ${created.id}`); setRefresh(n => n + 1)
    } catch (error) { if (current === epoch.current) setProblem(`Task was not created; your title is preserved. ${errorText(error)}`) }
    finally { if (current === epoch.current) setBusy(false) }
  }
  let frameState: WorkspaceFrameState = { kind: 'ready' }
  if (!id) frameState = { kind: 'error', message: 'A native project ID is required.' }
  else if (detail.state === 'loading') frameState = { kind: 'loading', message: 'Loading project…' }
  else if (!('value' in detail)) frameState = detail.state === 'denied' ? { kind: 'denied', message: detail.reason }
    : { kind: 'error', message: detail.reason, onRetry: () => setRefresh(n => n + 1) }
  const card: React.CSSProperties = { background: palette.card, border: `1px solid ${palette.line}`, borderRadius: 14,
    padding: 16, minWidth: 0 }
  const label: React.CSSProperties = { display: 'grid', gap: 6, color: palette.text, fontWeight: 600 }
  const action: React.CSSProperties = { ...button, background: palette.blueDark, color: palette.card, border: 0 }
  const secondary: React.CSSProperties = { ...button, background: palette.secondary, color: palette.text,
    border: `1px solid ${palette.line}` }
  return <WorkspaceFrame route={route} mode="full" title={project?.name ?? 'Project planning'} onBack={back}
    actions={<div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}><button type="button" style={secondary} onClick={openCode}>Open code workspace</button>
      <button type="button" style={secondary} onClick={() => setRefresh(n => n + 1)}>Refresh</button></div>} state={frameState}>
    <div style={{ display: 'grid', gap: 16, paddingBottom: 24, color: palette.text }}>
      {detail.state === 'stale' && <p role="status">Showing a stale project record. Review current data before saving.</p>}
      {project && draft && <>
        <p style={{ margin: 0, color: palette.muted, overflowWrap: 'anywhere' }}>Project ID: {id} · Owner: {scope.ownerId} · Updated: {project.updated_at ?? 'unknown'}</p>
        {problem && <p role="alert" style={{ ...card, color: palette.danger, background: palette.dangerSurface }}>{problem}</p>}
        {notice && <p role="status" style={card}>{notice}</p>}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 330px), 1fr))', gap: 16 }}>
          <section aria-label="Project context" style={card}>
            <h2 style={{ marginTop: 0 }}>Context and owner</h2>
            <div style={{ display: 'grid', gap: 12 }}>
              <label style={label}>Project name<input style={input} value={draft.name} disabled={project.name_locked || project.is_builtin}
                onChange={event => setDraft({ ...draft, name: event.target.value })} /></label>
              <label style={label}>Brief<textarea style={input} rows={5} value={draft.brief}
                onChange={event => setDraft({ ...draft, brief: event.target.value })} /></label>
              <label style={label}>Agent instructions<textarea style={input} rows={4} value={draft.agent_instructions_template}
                onChange={event => setDraft({ ...draft, agent_instructions_template: event.target.value })} /></label>
              <label style={label}>Status<select style={input} value={draft.status}
                onChange={event => setDraft({ ...draft, status: event.target.value as ProjectDraft['status'] })}>
                <option value="active">Active</option><option value="archived">Archived</option>
              </select></label>
              <button type="button" style={action} disabled={busy || !draft.name.trim() || detail.state === 'stale'} onClick={save}>Save project</button>
            </div>
          </section>
          <section aria-label="Project task planning" style={card}>
            <h2 style={{ marginTop: 0 }}>Task planning</h2>
            <p>{lists.length} task list{lists.length === 1 ? '' : 's'} · {tasks.length} task{tasks.length === 1 ? '' : 's'}</p>
            <ul>{lists.map(list => <li key={list.id}>{list.name}</li>)}</ul>
            <ul>{tasks.map(task => <li key={task.id}><button type="button" style={{ ...secondary, width: '100%', textAlign: 'left' }}
              onClick={() => openTask(task.id)}>{task.title} · {task.status}</button></li>)}</ul>
            {!tasks.length && <p>No tasks are planned yet.</p>}
            <label style={label}>New task title<input style={input} value={newTask}
              onChange={event => setNewTask(event.target.value)} /></label>
            <button type="button" style={{ ...action, marginTop: 10 }} disabled={busy || !newTask.trim()} onClick={addTask}>Create task</button>
          </section>
        </div>
        <section aria-label="Linked project work" style={card}>
          <h2 style={{ marginTop: 0 }}>Linked work and evidence</h2>
          <p>Work board: {board?.completeness ?? 'unavailable'} · {board?.attention ?? 0} needing attention</p>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
            {(['loops', 'code'] as const).map(kind => <div key={kind}><h3>{kind === 'code' ? 'Code runs' : 'Loops'}</h3>
              <ul>{linked?.[kind].length ? linked[kind].map(item => <li key={item.id}>{item.name} · {item.status}</li>)
                : <li>None linked.</li>}</ul></div>)}
            <div><h3>Artifacts</h3><ul>{linked?.artifacts.length ? linked.artifacts.map(item => <li key={item.slug}>{item.name} · {item.kind}</li>)
              : <li>No artifacts linked.</li>}</ul></div>
            <div><h3>Conversations and knowledge</h3><ul>{linked?.chats.map(item => <li key={item.key}>{item.title}</li>)}
              {linked?.knowledge.map((item, index) => <li key={item.id ?? index}>{item.title ?? item.name ?? 'Knowledge item'}</li>)}
              {!linked?.chats.length && !linked?.knowledge.length && <li>None linked.</li>}</ul></div>
          </div>
        </section>
      </>}
    </div>
  </WorkspaceFrame>
}
