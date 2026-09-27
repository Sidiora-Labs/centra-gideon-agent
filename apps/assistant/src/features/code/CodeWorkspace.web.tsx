import * as React from 'react'
import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import type { ShellReturnContext, ShellRoute } from '../../shared/shell/shellRoutes'
import { useShellTheme, type ShellPalette } from '../../shared/shell/shellTheme'
import { WorkspaceFrame, type WorkspaceFrameState } from '../../shared/shell/WorkspaceFrame.web'
import { GatewayError } from '../../shared/transport.web'
import CodeFiles from './CodeFiles.web'
import { captureSavedContext, codeIndexRoute, codeRecord, codeRoute, deleteSavedContext,
  readCodeSelection, readLiveTerminals, readProjectTasks, readSavedContexts, readWorkspaceProjects, reconcileSavedContext,
  type CodeSelection, type LiveComparison, type LiveTask, type LiveTerminal, type SavedContext, type WorkspaceProject } from './codeRoute'

export type CodeWorkspaceProps = {
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
  returnTo?: ShellReturnContext
  onReturn: () => void
}

const panel = (palette: ShellPalette): CSSProperties => ({ border: `1px solid ${palette.line}`, borderRadius: 14,
  padding: 18, minWidth: 0, background: palette.card, color: palette.text })
const controls: CSSProperties = { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }
const stack: CSSProperties = { display: 'grid', gap: 16, padding: 'clamp(12px, 3vw, 32px)', maxWidth: 1280, margin: '0 auto' }
const ProjectPlanning = React.lazy(() => import('../work/ProjectWorkspace.web'))

function errorMessage(error: unknown): string {
  if (error instanceof GatewayError) {
    if (error.status === 409) return 'This saved context changed. Reload it before deleting.'
    if (error.status === 403) return 'Access to this workspace was denied. Choose another project or return to the conversation.'
    if (error.status === 404) return 'This workspace record is missing. Choose another project.'
  }
  return error instanceof Error ? error.message : 'The workspace request failed.'
}

export function SavedContextDetails({ context, comparison, busy, onCompare, onDelete }: {
  context: SavedContext; comparison: LiveComparison | null; busy: boolean;
  onCompare: () => void; onDelete: () => void
}) {
  const { palette } = useShellTheme()
  return <section aria-label="Saved context" style={panel(palette)}>
    <h2>Saved context</h2>
    <p>{context.workspace}</p>
    <p>Saved branch: {context.branch ?? 'Detached'} · {context.dirty ? 'Uncommitted changes' : 'Clean'}</p>
    <p>Captured {context.captured_at}</p>
    <div style={controls}>
      <button type="button" disabled={busy} onClick={onCompare}>Compare with live workspace</button>
      <button type="button" disabled={busy} onClick={onDelete}>Delete saved context</button>
    </div>
    {comparison && <div aria-live="polite">
      <p>Live branch: {comparison.live.branch ?? 'Detached'} · {comparison.branch_matches === null ? 'No saved branch' : comparison.branch_matches ? 'Branch matches' : 'Branch changed'} · {comparison.live.dirty ? 'Uncommitted changes' : 'Clean'}</p>
      <p>Live terminals: {comparison.surviving_terminal_ids.join(', ') || 'None'}</p>
      <p>Missing terminals: {comparison.missing_terminal_ids.join(', ') || 'None'}</p>
      <p>Live tasks: {comparison.surviving_task_ids.join(', ') || 'None'}</p>
      <p>Missing tasks: {comparison.missing_task_ids.join(', ') || 'None'}</p>
    </div>}
  </section>
}

export default function CodeWorkspace({ route, scope, navigate, returnTo, onReturn }: CodeWorkspaceProps) {
  if (route.placement?.id === 'projects/detail' && route.placement.subview === '/planning' &&
    codeRecord(route)?.kind === 'project') {
    return <React.Suspense fallback={<p role="status">Loading project planning…</p>}>
      <ProjectPlanning route={route} scope={scope} navigate={navigate} returnTo={returnTo} onReturn={onReturn} />
    </React.Suspense>
  }
  return <CodeWorkspaceBody route={route} scope={scope} navigate={navigate} returnTo={returnTo} onReturn={onReturn} />
}

function CodeWorkspaceBody({ route, scope, navigate, returnTo, onReturn }: CodeWorkspaceProps) {
  const { palette } = useShellTheme()
  const [projects, setProjects] = useState<WorkspaceProject[]>([])
  const [contexts, setContexts] = useState<SavedContext[]>([])
  const [selection, setSelection] = useState<CodeSelection | null>(null)
  const [comparison, setComparison] = useState<LiveComparison | null>(null)
  const [phase, setPhase] = useState<WorkspaceFrameState>({ kind: 'loading', message: 'Loading Code workspace…' })
  const [error, setError] = useState('')
  const [busyState, setBusyState] = useState<{ key: string; value: boolean } | null>(null)
  const [draft, setDraft] = useState<{ key: string; terminalIds: string[]; taskIds: string[] } | null>(null)
  const [terminals, setTerminals] = useState<LiveTerminal[]>([])
  const [tasks, setTasks] = useState<LiveTask[]>([])
  const [referenceError, setReferenceError] = useState('')
  const [loadedKey, setLoadedKey] = useState('')
  const retry = useRef<{ body: string; id: string } | null>(null)
  const generation = useRef(0)
  const record = codeRecord(route)
  const recordKey = record ? `${record.kind}:${record.id}` : 'index'
  const ownerKey = scope.cacheKey
  const viewKey = JSON.stringify([ownerKey, route.placement?.id, recordKey, route.returnTo])
  const currentView = useRef({ ownerKey, viewKey })
  if (currentView.current.viewKey !== viewKey) {
    generation.current++
    if (currentView.current.ownerKey !== ownerKey) retry.current = null
    currentView.current = { ownerKey, viewKey }
  }
  const active = loadedKey === viewKey
  const visibleSelection = active ? selection : null
  const visibleProjects = active ? projects : []
  const visibleContexts = active ? contexts : []
  const visibleTerminals = active ? terminals : []
  const visibleTasks = active ? tasks : []
  const visibleComparison = active ? comparison : null
  const busy = busyState?.key === viewKey && busyState.value
  const selectedTerminalIds = draft?.key === viewKey ? draft.terminalIds : []
  const selectedTaskIds = draft?.key === viewKey ? draft.taskIds : []

  const load = useCallback(async () => {
    const current = ++generation.current
    setLoadedKey(viewKey)
    setPhase({ kind: 'loading', message: 'Loading Code workspace…' })
    setError(''); setReferenceError(''); setComparison(null); setSelection(null); setProjects([]); setContexts([])
    setTerminals([]); setTasks([])
    const [availableProjects, savedContexts, selected] = await Promise.allSettled([
      readWorkspaceProjects(), readSavedContexts(), record ? readCodeSelection(record) : Promise.resolve(null),
    ])
    if (generation.current !== current || currentView.current.viewKey !== viewKey) return
    if (availableProjects.status === 'fulfilled') setProjects(availableProjects.value)
    if (savedContexts.status === 'fulfilled') setContexts(savedContexts.value)
    const failure = selected.status === 'rejected' ? selected.reason
      : availableProjects.status === 'rejected' ? availableProjects.reason
      : savedContexts.status === 'rejected' ? savedContexts.reason : null
    if (!failure) {
      const nextSelection = selected.status === 'fulfilled' ? selected.value : null
      setSelection(nextSelection)
      setPhase({ kind: 'ready' })
      const project = nextSelection?.project
        ?? (nextSelection?.context && availableProjects.status === 'fulfilled'
          ? availableProjects.value.find(item => item.project.id === nextSelection.context?.project_id) : undefined)
      if (project?.project.workspace_dir) {
        const [terminalResult, taskResult] = await Promise.allSettled([
          readLiveTerminals(project.project.workspace_dir), readProjectTasks(project.project.id),
        ])
        if (generation.current !== current || currentView.current.viewKey !== viewKey) return
        if (terminalResult.status === 'fulfilled') setTerminals(terminalResult.value)
        if (taskResult.status === 'fulfilled') setTasks(taskResult.value)
        if (terminalResult.status === 'rejected' || taskResult.status === 'rejected')
          setReferenceError('Some live references could not be loaded. Refresh the workspace to try again.')
      }
    } else {
      const message = errorMessage(failure)
      const chooseProject = <button type="button" onClick={() => navigate(codeIndexRoute(route.returnTo ?? returnTo))}>Choose another project</button>
      if (failure instanceof GatewayError && failure.status === 404) setPhase({ kind: 'empty', message, action: chooseProject })
      else if (failure instanceof GatewayError && failure.status === 403) setPhase({ kind: 'denied', message, action: chooseProject })
      else setPhase({ kind: 'error', message, onRetry: () => void load() })
    }
  }, [viewKey])

  useEffect(() => { void load(); return () => { generation.current++ } }, [load])

  async function act(work: (stillCurrent: () => boolean) => Promise<void>) {
    if (busy) return
    const current = generation.current
    const stillCurrent = () => generation.current === current && currentView.current.viewKey === viewKey
    setBusyState({ key: viewKey, value: true }); setError('')
    try { await work(stillCurrent) } catch (failure) { if (stillCurrent()) setError(errorMessage(failure)) }
    finally { if (stillCurrent()) setBusyState({ key: viewKey, value: false }) }
  }

  const selectedProject = visibleSelection?.project
    ?? (visibleSelection?.context ? visibleProjects.find(item => item.project.id === visibleSelection.context?.project_id) : undefined)
  const currentProject = selectedProject?.project.id
  const source = route.returnTo ?? returnTo
  const openPlanning = () => currentProject && navigate({ ...codeRoute({ kind: 'project', id: currentProject }, source),
    placement: { id: 'projects/detail', subview: '/planning' } })
  const selectProject = (id: string) => navigate(id
    ? codeRoute({ kind: 'project', id }, source)
    : codeIndexRoute(source))
  const selectContext = (id: string) => navigate(codeRoute({ kind: 'snapshot', id }, source))
  const workspace = selectedProject?.project.workspace_dir ?? visibleSelection?.context?.workspace ?? visibleSelection?.run?.workspace_dir

  async function capture(stillCurrent: () => boolean) {
    if (!selectedProject) return
    const refs = { terminal_ids: selectedTerminalIds, task_ids: selectedTaskIds }
    const body = JSON.stringify({ project: selectedProject.project.id, refs })
    const id = retry.current?.body === body ? retry.current.id : crypto.randomUUID()
    retry.current = { body, id }
    const context = await captureSavedContext(selectedProject, refs, id)
    if (!stillCurrent()) return
    retry.current = null
    setContexts(rows => [context, ...rows.filter(item => item.id !== context.id)])
    selectContext(context.id)
  }

  async function remove(context: SavedContext, stillCurrent: () => boolean) {
    await deleteSavedContext(context)
    if (!stillCurrent()) return
    setContexts(rows => rows.filter(item => item.id !== context.id))
    selectProject(context.project_id)
  }

  const toggleReference = (kind: 'terminalIds' | 'taskIds', id: string) => setDraft(previous => {
    const current = previous?.key === viewKey ? previous : { key: viewKey, terminalIds: [], taskIds: [] }
    const ids = current[kind]
    return { ...current, [kind]: ids.includes(id) ? ids.filter(item => item !== id) : [...ids, id] }
  })

  return <WorkspaceFrame route={route} mode="full" title="Code workspace"
    state={active ? phase : { kind: 'loading', message: 'Loading Code workspace…' }}
    onBack={onReturn} onGoToChat={onReturn} actions={<button type="button" onClick={onReturn}>Return to conversation</button>}>
    <div style={{ ...stack, color: palette.text, background: palette.canvas }}>
      <header><p>Project files, saved workspace context and Code runs</p></header>
      {active && error && <p role="alert">{error}</p>}
      <section aria-label="Project switcher" style={panel(palette)}>
        <h2>Projects</h2>
        <label htmlFor="code-project">Selected project</label>{' '}
        <select id="code-project" value={currentProject ?? ''} onChange={event => selectProject(event.target.value)}>
          <option value="">Choose a project</option>
          {visibleProjects.map(item => <option key={item.project.id} value={item.project.id}>{item.project.name}</option>)}
        </select>
        {!visibleProjects.length && <p>No registered workspace projects are available.</p>}
      </section>
      {visibleSelection?.run && <section aria-label="Code project" style={panel(palette)}>
        <h2>{visibleSelection.run.name}</h2><p>Code run · {visibleSelection.run.status}</p>
        {visibleSelection.run.project_id && <p>Project: {visibleSelection.run.project_id}{!visibleSelection.project && ' · Project no longer available'}</p>}
      </section>}
      {workspace && <section aria-label="Workspace location" style={panel(palette)}><h2>Workspace</h2><p>{workspace}</p>
        {visibleSelection?.context && selectedProject && visibleSelection.context.workspace !== selectedProject.project.workspace_dir &&
          <p role="status">This saved workspace path differs from the project’s current path.</p>}
      </section>}
      {selectedProject && !selectedProject.project.workspace_dir && <section style={panel(palette)} role="status">
        This project has no accessible workspace path. Attach a workspace to save its context.
      </section>}
      {record?.kind === 'project' && selectedProject && <div style={controls}>
        <button type="button" onClick={openPlanning}>Open project planning</button>
      </div>}
      {record?.kind === 'project' && selectedProject?.project.workspace_dir &&
        <CodeFiles project={selectedProject} scope={scope} />}
      {selectedProject?.project.workspace_dir && <section aria-label="Capture context" style={panel(palette)}>
        <h2>Save workspace context</h2><p>Capture the current branch and selected live terminal and task references.</p>
        <div style={{ display: 'grid', gap: 10, maxWidth: 640 }}>
          {active && referenceError && <p role="status">{referenceError}</p>}
          <fieldset><legend>Live terminals in this workspace</legend>
            {visibleTerminals.length ? visibleTerminals.map(item => <label key={item.session_id} style={{ display: 'block' }}>
              <input type="checkbox" checked={selectedTerminalIds.includes(item.session_id)}
                onChange={() => toggleReference('terminalIds', item.session_id)} />
              {item.shell || 'Terminal'} · {item.session_id}
            </label>) : <p>No live terminals in this workspace.</p>}
          </fieldset>
          <fieldset><legend>Project tasks</legend>
            {visibleTasks.length ? visibleTasks.map(item => <label key={item.id} style={{ display: 'block' }}>
              <input type="checkbox" checked={selectedTaskIds.includes(item.id)}
                onChange={() => toggleReference('taskIds', item.id)} />
              {item.title} · {item.status}
            </label>) : <p>No tasks in this project.</p>}
          </fieldset>
          <button type="button" disabled={busy} onClick={() => void act(capture)}>Save context</button>
        </div>
      </section>}
      <section aria-label="Saved contexts" style={panel(palette)}>
        <h2>Saved contexts</h2>
        {!visibleContexts.length && <p>No saved contexts.</p>}
        <ul>{visibleContexts.filter(item => !currentProject || item.project_id === currentProject).map(item =>
          <li key={item.id}><button type="button" aria-current={record?.kind === 'snapshot' && record.id === item.id ? 'page' : undefined}
            onClick={() => selectContext(item.id)}>{item.project_id} · {item.branch ?? 'Detached'} · {item.captured_at}</button></li>)}</ul>
        {record?.kind === 'snapshot' && visibleSelection?.context && <SavedContextDetails context={visibleSelection.context} comparison={visibleComparison}
          busy={busy} onCompare={() => void act(async stillCurrent => {
            const result = await reconcileSavedContext(visibleSelection.context!)
            if (stillCurrent()) setComparison(result)
          })}
          onDelete={() => void act(stillCurrent => remove(visibleSelection.context!, stillCurrent))} />}
      </section>
    </div>
  </WorkspaceFrame>
}
