import React, { useEffect, useMemo, useRef, useState } from 'react'
import type { OwnerScope } from '../../shared/auth.web'
import { createShellRoute, serializeShellRoute, type ShellDestination, type ShellReturnContext, type ShellRoute } from '../../shared/shell/shellRoutes'
import { WorkspaceFrame, type WorkspaceFrameState } from '../../shared/shell/WorkspaceFrame.web'
import { WorkClient, type WorkEntry, type WorkKind, type WorkRead } from './workClient'

type WorkDestination = Readonly<{
  id: string
  label: string
  kind: WorkKind
  destination: ShellDestination
  subview?: string
  intent?: 'edit' | 'invoke' | 'availability' | 'unsupported_execution' | 'unsupported_edit'
}>

export const WORK_DESTINATIONS: readonly WorkDestination[] = Object.freeze([
  { id: 'tasks', label: 'Tasks', kind: 'task', destination: 'activity' },
  { id: 'tasks/new', label: 'Create a task', kind: 'task', destination: 'activity' },
  { id: 'tasks/graph', label: 'Dependencies', kind: 'task', destination: 'activity' },
  { id: 'projects', label: 'Projects', kind: 'project', destination: 'apps' },
  { id: 'projects/detail', label: 'Project workspace', kind: 'project', destination: 'apps' },
  { id: 'workflows', label: 'Workflows', kind: 'workflow', destination: 'activity' },
  { id: 'workflows/definition', label: 'Workflow builder', kind: 'workflow', destination: 'activity' },
  { id: 'workflows/run', label: 'Workflow run', kind: 'workflow_run', destination: 'activity' },
  { id: 'triggers', label: 'Schedules and triggers', kind: 'trigger', destination: 'activity' },
  { id: 'triggers/new', label: 'Create an automation', kind: 'trigger', destination: 'activity' },
  { id: 'loops', label: 'Ongoing work', kind: 'loop', destination: 'activity' },
  { id: 'loops/new', label: 'Start ongoing work', kind: 'loop', destination: 'activity' },
  { id: 'loops/run', label: 'Loop controls', kind: 'loop', destination: 'activity' },
  { id: 'rooms', label: 'Rooms', kind: 'room', destination: 'activity' },
  { id: 'rooms/new', label: 'Create a room', kind: 'room', destination: 'activity' },
  { id: 'rooms/conversation', label: 'Room conversation', kind: 'room', destination: 'activity' },
  { id: 'agents', label: 'Agents', kind: 'agent', destination: 'activity' },
  { id: 'agents/new', label: 'Create an agent', kind: 'agent', destination: 'activity' },
  { id: 'agents/inspect', label: 'Agent controls', kind: 'agent', destination: 'activity' },
  { id: 'experiments', label: 'Experiments', kind: 'experiment', destination: 'activity' },
  { id: 'experiments/replay', label: 'Run replay', kind: 'experiment', destination: 'activity' },
  { id: 'skills', label: 'Skills', kind: 'skill', destination: 'activity' },
  { id: 'skills', subview: '/detail', label: 'Skill details', kind: 'skill', destination: 'activity' },
  { id: 'skills', subview: '/edit', label: 'Edit skill', kind: 'skill', destination: 'activity', intent: 'edit' },
  { id: 'skills', subview: '/execution', label: 'Skill execution', kind: 'skill', destination: 'activity', intent: 'unsupported_execution' },
  { id: 'skills', subview: '/availability', label: 'Skill availability', kind: 'skill', destination: 'activity', intent: 'availability' },
  { id: 'tools', label: 'Tools', kind: 'tool', destination: 'activity' },
  { id: 'tools', subview: '/detail', label: 'Tool details', kind: 'tool', destination: 'activity' },
  { id: 'tools', subview: '/edit', label: 'Edit tool', kind: 'tool', destination: 'activity', intent: 'unsupported_edit' },
  { id: 'tools', subview: '/execution', label: 'Tool execution', kind: 'tool', destination: 'activity', intent: 'invoke' },
  { id: 'tools', subview: '/invoke', label: 'Run tool', kind: 'tool', destination: 'activity', intent: 'invoke' },
  { id: 'tools', subview: '/availability', label: 'Tool availability', kind: 'tool', destination: 'activity', intent: 'availability' },
])

export function findWorkDestination(route: ShellRoute): WorkDestination | null {
  const placement = route.placement
  if (placement) {
    const page = WORK_DESTINATIONS.find(entry => entry.id === placement.id
      && entry.subview === placement.subview && entry.destination === route.destination)
    if (!page || (route.record && route.record.kind !== page.kind)) return null
    return page
  }
  if (!route.record || route.destination !== 'activity') return null
  const base = WORK_DESTINATIONS.find(entry => entry.kind === route.record?.kind && !entry.subview
    && entry.destination === route.destination && (route.record?.kind === 'workflow_run'
      ? entry.id === 'workflows/run' : !entry.id.includes('/')))
  return base ?? null
}

export function createWorkRoute(id: string, recordId?: string,
  returnTo?: ShellReturnContext, subview?: string): ShellRoute {
  const page = WORK_DESTINATIONS.find(entry => entry.id === id && entry.subview === subview)
  if (!page) throw new TypeError('Unknown Work destination')
  if (subview && !recordId) throw new TypeError('This Work destination needs a native ID')
  return createShellRoute(page.destination, {
    view: recordId ? 'detail' : 'workspace',
    placement: { id, ...(subview ? { subview } : {}) },
    ...(recordId ? { record: { kind: page.kind, id: recordId } } : {}),
    ...(returnTo ? { returnTo } : {}),
  })
}

export function workReturnRoute(context: ShellReturnContext): ShellRoute {
  return createShellRoute(context.destination, {
    view: context.record ? 'detail' : context.placement ? 'workspace' : 'list',
    record: context.record,
    placement: context.placement,
    sessionId: context.sessionId,
  })
}

type WorkView = WorkRead<WorkEntry[]> | WorkRead<WorkEntry>
type ViewState = { state: 'loading' } | WorkView

const buttonStyle: React.CSSProperties = {
  minHeight: 44, border: '1px solid color-mix(in srgb, currentColor 24%, transparent)',
  borderRadius: 10, background: 'transparent', color: 'inherit', padding: '8px 12px',
  font: 'inherit', cursor: 'pointer', textAlign: 'left',
}

function sourceContext(route: ShellRoute): ShellReturnContext {
  return route.returnTo ?? {
    destination: route.destination, record: route.record, placement: route.placement,
    sessionId: route.sessionId,
  }
}

function summary(record: unknown): string | null {
  if (!record || typeof record !== 'object') return null
  const values = record as Record<string, unknown>
  for (const key of ['description', 'brief', 'objective']) {
    if (typeof values[key] === 'string' && values[key].trim()) return values[key] as string
  }
  return null
}

function stateFor(view: ViewState, retry: () => void): WorkspaceFrameState {
  switch (view.state) {
    case 'loading': return { kind: 'loading', message: 'Checking Gideon records…' }
    case 'empty': return { kind: 'empty', message: 'There are no records here yet.' }
    case 'denied': return { kind: 'denied', message: view.reason }
    case 'unavailable': return { kind: 'error', message: `This workspace is unavailable. ${view.reason}`, onRetry: retry }
    case 'failed': return { kind: 'error', message: `The read failed. ${view.reason}`, onRetry: retry }
    default: return { kind: 'ready' }
  }
}

export type WorkRoutesProps = Readonly<{
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
  onReturn?: () => void
}>

export default function WorkRoutes(props: WorkRoutesProps) {
  const key = JSON.stringify([props.scope.cacheKey, serializeShellRoute(props.route)])
  return <WorkRouteInstance key={key} {...props} />
}

function WorkRouteInstance({ route, scope, navigate, onReturn }: WorkRoutesProps) {
  const page = findWorkDestination(route)
  const client = useMemo(() => new WorkClient(scope), [scope.cacheKey])
  const [view, setView] = useState<ViewState>({ state: 'loading' })
  const [refresh, setRefresh] = useState(0)
  const [skillDraft, setSkillDraft] = useState('')
  const [skillRead, setSkillRead] = useState<WorkRead<string> | null>(null)
  const [toolArgs, setToolArgs] = useState('{}')
  const [confirmRisk, setConfirmRisk] = useState(false)
  const [toolResult, setToolResult] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const actionEpoch = useRef(0)
  const id = route.record?.id

  useEffect(() => {
    actionEpoch.current += 1
    setToolArgs('{}')
    setToolResult(null)
    setActionError(null)
    setConfirmRisk(false)
    setSaving(false)
    return () => { actionEpoch.current += 1 }
  }, [scope.cacheKey, page?.kind, page?.intent, id])

  useEffect(() => {
    if (!page) return
    const abort = new AbortController()
    setView({ state: 'loading' })
    const read = id ? client.detail(page.kind, id, abort.signal) : client.list(page.kind, abort.signal)
    read.then(result => { if (!abort.signal.aborted) setView(result) })
    return () => abort.abort()
  }, [client, page, id, refresh])

  useEffect(() => {
    if (page?.intent !== 'edit' || !id) return
    const abort = new AbortController()
    setSkillRead(null)
    client.skillContent(id, abort.signal).then(result => {
      if (abort.signal.aborted) return
      setSkillRead(result)
      if ('value' in result) setSkillDraft(result.value)
    })
    return () => abort.abort()
  }, [client, page, id, refresh])

  if (!page) return <WorkspaceFrame route={route} mode="full" title="Work" state={{ kind: 'error',
    message: 'This Work destination is unavailable.' }}><></></WorkspaceFrame>

  const goBack = () => {
    if (onReturn) onReturn()
    else if (route.returnTo) navigate(workReturnRoute(route.returnTo))
    else navigate(createWorkRoute(page.id))
  }
  const open = (destination: string, recordId?: string, subview?: string) =>
    navigate(createWorkRoute(destination, recordId, sourceContext(route), subview))
  const entry = 'value' in view && !Array.isArray(view.value) ? view.value as WorkEntry : null
  const rows = 'value' in view && Array.isArray(view.value) ? view.value as WorkEntry[] : null
  const stale = view.state === 'stale'
  const actions = <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
    {id && <button type="button" style={buttonStyle} onClick={() => open(page.id.split('/')[0])}>Catalogue</button>}
    {page.kind === 'skill' && id && page.intent !== 'edit' &&
      <button type="button" style={buttonStyle} onClick={() => open('skills', id, '/edit')}>Edit</button>}
    {page.kind === 'skill' && id && page.intent !== 'availability' &&
      <button type="button" style={buttonStyle} onClick={() => open('skills', id, '/availability')}>Availability</button>}
    {page.kind === 'skill' && id && page.intent !== 'unsupported_execution' &&
      <button type="button" style={buttonStyle} onClick={() => open('skills', id, '/execution')}>Execution</button>}
    {page.kind === 'tool' && id && page.intent !== 'invoke' &&
      <button type="button" style={buttonStyle} onClick={() => open('tools', id, '/invoke')}>Run</button>}
    {page.kind === 'tool' && id && page.intent !== 'availability' &&
      <button type="button" style={buttonStyle} onClick={() => open('tools', id, '/availability')}>Availability</button>}
    {page.kind === 'tool' && id && page.intent !== 'unsupported_edit' &&
      <button type="button" style={buttonStyle} onClick={() => open('tools', id, '/edit')}>Edit</button>}
    <button type="button" style={buttonStyle} onClick={() => setRefresh(current => current + 1)}>Refresh</button>
  </div>

  const saveSkill = async () => {
    if (!id) return
    const epoch = actionEpoch.current
    setSaving(true)
    setActionError(null)
    try {
      await client.saveSkill(id, skillDraft)
      if (epoch === actionEpoch.current) setRefresh(current => current + 1)
    } catch (error) {
      if (epoch === actionEpoch.current) setActionError(error instanceof Error ? error.message : 'The skill was not saved.')
    } finally { if (epoch === actionEpoch.current) setSaving(false) }
  }

  const invokeTool = async () => {
    if (!id || !entry) return
    setActionError(null)
    setToolResult(null)
    const record = entry.record as unknown as Record<string, unknown>
    if (record.disabled || record.locked || record.providerDisabled) {
      setActionError('This tool is currently unavailable.')
      return
    }
    let args: unknown
    try { args = JSON.parse(toolArgs) }
    catch { setActionError('Enter valid JSON arguments.'); return }
    if (!args || typeof args !== 'object' || Array.isArray(args)) {
      setActionError('Arguments must be a JSON object.'); return
    }
    if (record.risk_level === 'destructive' && !confirmRisk) {
      setActionError('Confirm the destructive tool action before running it.'); return
    }
    const epoch = actionEpoch.current
    setSaving(true)
    try {
      const result = await client.invokeTool(id, String(record.provider ?? ''), args as Record<string, unknown>,
        record.risk_level === 'destructive' && confirmRisk)
      if (epoch !== actionEpoch.current) return
      if (result.ok) setToolResult(result.output ?? 'The tool completed without output.')
      else setActionError(result.error ?? 'The tool did not complete.')
    } catch (error) {
      if (epoch === actionEpoch.current) setActionError(error instanceof Error ? error.message : 'The tool could not run.')
    } finally { if (epoch === actionEpoch.current) setSaving(false) }
  }

  return <WorkspaceFrame route={route} mode={page.kind === 'room' ? 'compact' : 'full'} title={page.label} actions={actions}
    onBack={goBack} state={stateFor(view, () => setRefresh(current => current + 1))}>
    <div style={{ width: '100%', minWidth: 0, display: 'grid', gap: 16 }}>
      {stale && <p role="status">Showing the last checked records from {new Date(view.checkedAt).toLocaleString()}.
        Refresh could not complete: {view.reason}</p>}
      {rows && <ul aria-label={page.label} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))',
        gap: 10, padding: 0, margin: 0, listStyle: 'none' }}>
        {rows.map(row => <li key={`${row.identity.kind}:${row.identity.id}`}>
          <button type="button" style={{ ...buttonStyle, display: 'grid', gap: 5, width: '100%' }}
            onClick={() => open(page.id, row.identity.id, page.subview)}>
            <strong>{row.title}</strong>
            <span>{row.status ?? row.identity.kind}</span>
            {summary(row.record) && <span>{summary(row.record)}</span>}
          </button>
        </li>)}
      </ul>}
      {entry && <section aria-label={`${entry.title} details`} style={{ display: 'grid', gap: 10, minWidth: 0 }}>
        <h2 style={{ margin: 0 }}>{entry.title}</h2>
        <p style={{ margin: 0, overflowWrap: 'anywhere' }}>{entry.identity.kind} · {entry.identity.id}
          {entry.identity.revision !== null ? ` · Revision ${entry.identity.revision}` : ''}</p>
        {entry.status && <p style={{ margin: 0 }}>Status: {entry.status}</p>}
        {summary(entry.record) && <p style={{ margin: 0, maxWidth: '75ch' }}>{summary(entry.record)}</p>}
        {page.kind === 'tool' && <p style={{ margin: 0 }}>Provider: {String((entry.record as unknown as Record<string, unknown>).provider ?? 'Unknown')}</p>}
        {page.intent === 'availability' && <p role="status">{(() => {
          const record = entry.record as unknown as Record<string, unknown>
          if (page.kind === 'skill') return `Integrity: ${String(record.integrity ?? 'unverified')}. Agent assignment controls execution availability.`
          return record.locked ? 'Access is locked.' : record.providerDisabled ? 'Provider is disabled.'
            : record.disabled ? 'Tool is disabled.' : 'Tool is available.'
        })()}</p>}
        {page.intent === 'unsupported_execution' && <p role="status">Skills run through an assigned agent. Direct skill execution is unavailable.</p>}
        {page.intent === 'unsupported_edit' && <p role="status">Tool configuration changes are unavailable in this workspace. Review the provider and availability before invoking.</p>}
        {page.intent === 'edit' && <div style={{ display: 'grid', gap: 8, maxWidth: 900 }}>
          {skillRead?.state === 'stale' && <p role="status">Showing previously checked content. Save only after reviewing it.</p>}
          {skillRead && !('value' in skillRead) && <p role="alert">{skillRead.reason}</p>}
          <label htmlFor="gideon-skill-content">Skill content</label>
          <textarea id="gideon-skill-content" value={skillDraft} onChange={event => setSkillDraft(event.target.value)}
            disabled={!skillRead || !('value' in skillRead) || saving} rows={16}
            style={{ width: '100%', minWidth: 0, boxSizing: 'border-box', font: 'inherit' }} />
          <button type="button" style={buttonStyle} disabled={!skillRead || !('value' in skillRead) || saving}
            onClick={saveSkill}>Save skill</button>
        </div>}
        {page.intent === 'invoke' && <div style={{ display: 'grid', gap: 8, maxWidth: 900 }}>
          <label htmlFor="gideon-tool-arguments">Arguments (JSON object)</label>
          <textarea id="gideon-tool-arguments" value={toolArgs} onChange={event => setToolArgs(event.target.value)}
            rows={8} style={{ width: '100%', minWidth: 0, boxSizing: 'border-box', font: 'inherit' }} />
          {(entry.record as unknown as Record<string, unknown>).risk_level === 'destructive' &&
            <label><input type="checkbox" checked={confirmRisk} onChange={event => setConfirmRisk(event.target.checked)} />
              Confirm this destructive action</label>}
          <button type="button" style={buttonStyle} disabled={saving} onClick={invokeTool}>Run tool</button>
          {toolResult && <pre role="status" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{toolResult}</pre>}
        </div>}
        {actionError && <p role="alert">{actionError}</p>}
      </section>}
    </div>
  </WorkspaceFrame>
}
