import React, { useEffect, useState, type CSSProperties } from 'react'
import { GatewayError } from '../../shared/transport.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute, serializeShellRoute, type ShellRoute } from '../../shared/shell/shellRoutes'
import { createPersonalClient, type PersonalAvailability, type PersonalClient, type IdentityStory, type NativeValue, type PersonalRecord } from './client'
import { PERSONAL_SPACES, personalSpaceRoute, type PersonalSpace } from './routes'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { useShellTheme } from '../../shared/shell/shellTheme.web'

export type PersonalModuleProps = ModuleProps

type Draft = { idea: string }
const drafts = new Map<string, Draft>()
const activeDraftScopeByOrigin = new Map<string, string>()
const emptyDraft = (): Draft => ({ idea: '' })

function spaceForRoute(route: ShellRoute): PersonalSpace {
  const placement = route.placement?.id
  if (placement === 'capabilities/identity/goals' || placement === 'capabilities/identity/goal-plans') return 'goals'
  if (placement?.startsWith('capabilities/identity/')) return 'identity'
  if (placement?.startsWith('capabilities/wellbeing/')) return placement === 'capabilities/wellbeing/memory' ? 'memory' : 'health'
  if (placement === 'capabilities/knowledge/journals') return 'journal'
  if (placement === 'capabilities/knowledge/ideas') return 'ideas'
  return PERSONAL_SPACES.find(item => item.placement === placement)?.id
    ?? (route.destination === 'goals' ? 'goals' : 'ideas')
}

type PersonalStatus = 'loading' | 'ready' | 'empty' | 'stale' | 'denied' | 'unavailable' | 'error'
type KeyedPersonalStatus = Readonly<{ key: string; status: PersonalStatus; message: string; value?: unknown }>

export function statusForSelection(state: KeyedPersonalStatus, selectionKey: string): KeyedPersonalStatus {
  return state.key === selectionKey ? state : { key: selectionKey, status: 'loading', message: '' }
}

export function personalSelectionKey(ownerKey: string, route: ShellRoute): string {
  return JSON.stringify([ownerKey, serializeShellRoute(route)])
}

function NativeStatus({ label, load, selectionKey, recordDetail = false, recordId }: { label: string; load: () => Promise<PersonalAvailability<unknown>>; selectionKey: string; recordDetail?: boolean; recordId?: string }) {
  const [storedStatus, setStoredStatus] = useState<KeyedPersonalStatus>(() => ({ key: selectionKey, status: 'loading', message: '' }))
  const { status, message } = statusForSelection(storedStatus, selectionKey)
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setStoredStatus({ key: selectionKey, status: 'loading', message: '' })
    void load().then(result => {
      if (!active) return
      if (result.state === 'unavailable') { setStoredStatus({ key: selectionKey, status: 'unavailable', message: result.reason }); return }
      let value: unknown = result.value
      for (let depth = 0; depth < 2 && value && typeof value === 'object'; depth++) {
        if ('state' in value && value.state === 'unavailable' && 'reason' in value && typeof value.reason === 'string') {
          setStoredStatus({ key: selectionKey, status: 'unavailable', message: value.reason }); return
        }
        if ('state' in value && value.state === 'available' && 'value' in value) {
          value = value.value
          continue
        }
        break
      }
      const summary = summarizePersonalValue(label, value)
      setStoredStatus({ key: selectionKey, status: summary.state, message: summary.message, value })
    }).catch(error => {
      if (!active) return
      setStoredStatus({ key: selectionKey, status: error instanceof GatewayError && error.status === 403 ? 'denied' : 'error',
        message: error instanceof GatewayError && error.status === 403 ? `Access to ${label.toLowerCase()} was denied for this session.` : error instanceof Error ? error.message : `${label} could not be loaded.` })
    })
    return () => { active = false }
  }, [attempt, label, load, selectionKey])
  return <section aria-live="polite" className="gideon-personal-status" data-state={status}>
    <p role={status === 'error' ? 'alert' : 'status'}>{status === 'loading' ? `Loading ${label.toLowerCase()}…` : message}</p>
    {recordDetail && status === 'ready' && <RecordSummary label={label} value={storedStatus.key === selectionKey ? storedStatus.value : undefined} recordId={recordId} />}
    {(status === 'error' || status === 'denied' || status === 'stale' || status === 'unavailable') && <button type="button" onClick={() => setAttempt(value => value + 1)}>Retry {label.toLowerCase()}</button>}
  </section>
}

function RecordSummary({ label, value, recordId }: { label: string; value: unknown; recordId?: string }) {
  let selected = value
  if (selected && typeof selected === 'object' && 'value' in selected && 'identity' in selected) selected = selected.value
  if (selected && typeof selected === 'object' && 'value' in selected) selected = selected.value
  const title = selected && typeof selected === 'object' && 'title' in selected && typeof selected.title === 'string' ? selected.title : label
  const identity = selected && typeof selected === 'object' && 'identity' in selected ? selected.identity : undefined
  const id = identity && typeof identity === 'object' && 'nativeId' in identity && typeof identity.nativeId === 'string' ? identity.nativeId
    : selected && typeof selected === 'object' && 'id' in selected && typeof selected.id === 'string' ? selected.id : recordId ?? ''
  return <div className="gideon-personal-record"><h2>{title}</h2><details><summary>Source details</summary><p>Record ID: <code>{id || 'Not supplied'}</code></p></details></div>
}

export function summarizePersonalValue(label: string, value: unknown): Readonly<{
  state: 'ready' | 'empty' | 'stale';
  message: string;
}> {
  const name = label.toLowerCase()
  if (Array.isArray(value)) {
    const stale = value.some(item => item && typeof item === 'object' && 'freshness' in item && item.freshness === 'stale')
    if (stale) return { state: 'stale', message: `Showing saved ${name} records. Refresh is needed to confirm they are current.` }
    if (!value.length) return { state: 'empty', message: `No ${name} records yet.` }
    return { state: 'ready', message: `${value.length} ${name} record${value.length === 1 ? '' : 's'} ${value.length === 1 ? 'is' : 'are'} available.` }
  }
  if (!value || typeof value !== 'object') return { state: 'empty', message: `No ${name} data is available yet.` }
  if ('kind' in value && value.kind === 'autobiography' && 'stories' in value && Array.isArray(value.stories)) {
    const stories = value.stories
    const stale = stories.some(item => item && typeof item === 'object' && 'freshness' in item && item.freshness === 'stale')
    if (stale) return { state: 'stale', message: 'Showing saved autobiography stories. Refresh is needed to confirm they are current.' }
    if (!stories.length) return { state: 'empty', message: 'No autobiography stories yet.' }
    return { state: 'ready', message: `${stories.length} autobiography stor${stories.length === 1 ? 'y is' : 'ies are'} available.` }
  }
  if ('kind' in value && value.kind === 'twin' && 'profile' in value && value.profile && typeof value.profile === 'object'
    && 'documents' in value.profile && Array.isArray(value.profile.documents)) {
    const count = value.profile.documents.length
    return count
      ? { state: 'ready', message: `${count} identity twin source document${count === 1 ? ' is' : 's are'} available.` }
      : { state: 'empty', message: 'The identity twin has no source documents yet.' }
  }
  if ('journal' in value && (value.journal === null || typeof value.journal === 'object')) {
    return value.journal
      ? { state: 'ready', message: 'A journal entry is saved for this date.' }
      : { state: 'empty', message: 'No journal entry is saved for this date yet.' }
  }
  if ('advertising' in value && typeof value.advertising === 'boolean' && 'detail' in value && typeof value.detail === 'string') {
    return { state: 'ready', message: value.detail }
  }
  return { state: 'ready', message: `${label} information is available.` }
}

export type IdentityWorkspaceData =
  | Readonly<{ kind: 'autobiography'; stories: readonly PersonalRecord<IdentityStory>[] }>
  | Readonly<{ kind: 'twin'; profile: NativeValue }>

export async function readIdentitySpace(client: PersonalClient, placement: string | undefined): Promise<PersonalAvailability<IdentityWorkspaceData>> {
  if (placement === 'capabilities/identity/autobiography') {
    const stories = await client.readIdentityStories()
    return { state: 'available', value: { kind: 'autobiography', stories } }
  }
  if (placement === 'capabilities/identity/twin') {
    return { state: 'available', value: { kind: 'twin', profile: await client.readIdentityProfile() } }
  }
  return { state: 'unavailable', reason: 'This Identity view is unavailable.' }
}

export function PersonalHome({ route, scope, navigate, onReturn, returnTo }: PersonalModuleProps) {
  const priorDraftScope = activeDraftScopeByOrigin.get(scope.runtimeOrigin)
  if (priorDraftScope && priorDraftScope !== scope.cacheKey) drafts.delete(priorDraftScope)
  activeDraftScopeByOrigin.set(scope.runtimeOrigin, scope.cacheKey)
  const [draftState, setDraftState] = useState(() => ({ scopeKey: scope.cacheKey, value: { ...(drafts.get(scope.cacheKey) ?? emptyDraft()) } }))
  const draft = draftState.scopeKey === scope.cacheKey ? draftState.value : drafts.get(scope.cacheKey) ?? emptyDraft()
  const space = spaceForRoute(route)
  const { palette } = useShellTheme()
  const client = React.useMemo(() => createPersonalClient(scope), [scope.cacheKey])
  const selectionKey = personalSelectionKey(scope.cacheKey, route)
  useEffect(() => () => client.dispose(), [client])
  useEffect(() => { if (draftState.scopeKey === scope.cacheKey) drafts.set(scope.cacheKey, draftState.value) }, [scope.cacheKey, draftState])
  const updateDraft = (key: keyof Draft, value: string) => setDraftState(previous => ({
    scopeKey: scope.cacheKey,
    value: { ...(previous.scopeKey === scope.cacheKey ? previous.value : draft), [key]: value },
  }))
  const ideas = !route.record && space === 'ideas'
  const title = PERSONAL_SPACES.find(item => item.id === space)?.label ?? 'Personal'
  const back = returnTo ? () => navigate(createShellRoute(returnTo.destination, {
    view: returnTo.record ? 'detail' : 'list', record: returnTo.record, placement: returnTo.placement, sessionId: returnTo.sessionId,
  })) : onReturn
  const loadIdeas = React.useCallback(() => client.readIdeas().then(value => ({ state: 'available' as const, value })), [client])
  const loadFocused = React.useCallback(() => {
    switch (space) {
      case 'identity': return readIdentitySpace(client, route.placement?.id)
      case 'learning': return client.readLearningCaptures()
      case 'companion': return client.readCompanion()
      case 'memory': return client.readMemory().then(value => ({ state: 'available' as const, value }))
      case 'health': return client.readHealthMeasurements().then(value => ({ state: 'available' as const, value }))
      case 'journal': return client.readJournal(new Date().toISOString().slice(0, 10), Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
      default: return Promise.resolve({ state: 'unavailable' as const, reason: 'This personal space is unavailable.' })
    }
  }, [client, route.placement?.id, space])
  const loadRecord = React.useCallback(async () => {
    const record = route.record
    if (!record) return { state: 'unavailable' as const, reason: 'No personal record is selected.' }
    if (record.kind === 'idea') return { state: 'available' as const, value: await client.readIdea(record.id) }
    if (record.kind === 'human-goal') return { state: 'available' as const, value: await client.readGoal(record.id) }
    if (record.kind === 'goal-plan') return { state: 'available' as const, value: await client.readGoalPlan(record.id) }
    if (record.kind === 'identity-story') return { state: 'available' as const, value: await client.readIdentityStory(record.id) }
    if (record.kind === 'health-measurement') return { state: 'available' as const, value: await client.readHealthMeasurement(record.id) }
    if (record.kind === 'memory-fact') return { state: 'available' as const, value: await client.readMemoryFact(record.id) }
    if (record.kind === 'learning-capture' || record.kind === 'learning-review') {
      const result = record.kind === 'learning-capture' ? await client.readLearningCaptures() : await client.readLearningReviews()
      if (result.state === 'unavailable') return result
      const found = result.value.find(item => item.identity.nativeId === record.id)
      return found ? { state: 'available' as const, value: found } : { state: 'unavailable' as const, reason: 'The selected learning record is unavailable.' }
    }
    return { state: 'unavailable' as const, reason: 'This personal record detail is unavailable.' }
  }, [client, route.record?.id, route.record?.kind])
  const themeStyle = {
    '--personal-text': palette.text,
    '--personal-muted': palette.muted,
    '--personal-border': palette.line,
    '--personal-card': palette.card,
    '--personal-canvas': palette.canvas,
    '--personal-accent': palette.blueDark,
    '--personal-accent-surface': palette.sky,
  } as CSSProperties

  return <div className="gideon-personal-home" style={themeStyle}>
    <WorkspaceFrame route={route} mode="full" title={title} onBack={back}>
      <p className="gideon-personal-intro">Keep personal decisions and progress connected to their source records.</p>
      {ideas && <div className="gideon-personal-entry-grid">
        <section aria-labelledby="personal-ideas-title" className="gideon-personal-entry">
          <div><h2 id="personal-ideas-title">Ideas</h2><p>Review evidence-backed suggestions and keep a note for later.</p></div>
          <label htmlFor="personal-idea-draft">Quick note</label>
          <textarea id="personal-idea-draft" value={draft.idea} onChange={event => updateDraft('idea', event.currentTarget.value)} rows={3} />
          <NativeStatus label="Ideas" load={loadIdeas} selectionKey={selectionKey} />
        </section>
      </div>}
      {route.record ? <NativeStatus label={title} load={loadRecord} selectionKey={selectionKey} recordDetail recordId={route.record.id} />
        : !ideas && <NativeStatus label={title} load={loadFocused} selectionKey={selectionKey} />}
      <nav aria-label="Personal spaces" className="gideon-personal-spaces">
        {PERSONAL_SPACES.map(item => <button key={item.id} type="button" aria-current={item.id === space ? 'page' : undefined}
          onClick={() => navigate(personalSpaceRoute(item.id, { returnTo: {
            destination: route.destination, record: route.record, placement: route.placement, sessionId: route.sessionId,
            selectionId: route.record?.id,
          } }))}>{item.label}</button>)}
      </nav>
    </WorkspaceFrame>
    <style>{`
      .gideon-personal-home{box-sizing:border-box;width:100%;min-width:0;height:100%;color:var(--personal-text)}
      .gideon-personal-intro{margin:0;padding:20px 24px 4px;color:var(--personal-muted);max-width:70ch}
      .gideon-personal-entry-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:16px 24px}
      .gideon-personal-entry{display:flex;min-width:0;flex-direction:column;gap:10px;padding:18px;border:1px solid var(--personal-border);border-radius:14px;background:var(--personal-card)}
      .gideon-personal-entry h2{margin:0 0 4px;font-size:1.15rem}.gideon-personal-entry p{margin:0;color:var(--personal-muted)}
      .gideon-personal-entry textarea{box-sizing:border-box;width:100%;resize:vertical;border:1px solid var(--personal-border);border-radius:8px;padding:10px;background:var(--personal-canvas);color:inherit;font:inherit}
      .gideon-personal-status{padding:4px 0}.gideon-personal-status p{margin:0}.gideon-personal-status button{margin-top:8px}
      .gideon-personal-record h2{margin:10px 0 4px;font-size:1.05rem}.gideon-personal-record summary{cursor:pointer;color:var(--personal-muted)}.gideon-personal-record code{overflow-wrap:anywhere}
      .gideon-personal-spaces{display:flex;flex-wrap:wrap;gap:8px;padding:8px 24px 20px}
      .gideon-personal-spaces button{border:1px solid var(--personal-border);border-radius:999px;padding:8px 12px;background:transparent;color:inherit;font:inherit;cursor:pointer}
      .gideon-personal-spaces button[aria-current="page"]{border-color:var(--personal-accent);background:var(--personal-accent-surface)}
      .gideon-personal-spaces button:focus-visible,.gideon-personal-entry textarea:focus-visible{outline:2px solid var(--personal-accent);outline-offset:2px}
      @media(max-width:650px){.gideon-personal-entry-grid{grid-template-columns:minmax(0,1fr);padding:14px 16px}.gideon-personal-intro{padding:16px 16px 4px}.gideon-personal-spaces{padding-inline:16px}}
    `}</style>
  </div>
}

export default PersonalHome
