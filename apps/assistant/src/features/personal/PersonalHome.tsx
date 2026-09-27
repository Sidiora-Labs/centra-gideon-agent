import React, { useEffect, useState } from 'react'
import { GatewayError } from '../../shared/transport.web'
import type { OwnerScope } from '../../shared/auth.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute, type ShellReturnContext, type ShellRoute } from '../../shared/shell/shellRoutes'
import { createPersonalClient, type PersonalAvailability } from './client'
import { PERSONAL_SPACES, personalSpaceRoute, type PersonalSpace } from './routes'

export type PersonalModuleProps = Readonly<{
  route: ShellRoute
  scope: OwnerScope
  navigate: (route: ShellRoute) => void
  onReturn: () => void
  returnTo?: ShellReturnContext
}>

type Draft = { idea: string; goal: string }
const drafts = new Map<string, Draft>()
const activeDraftScopeByOrigin = new Map<string, string>()
const emptyDraft = (): Draft => ({ idea: '', goal: '' })

function spaceForRoute(route: ShellRoute): PersonalSpace {
  const placement = route.placement?.id
  if (placement?.startsWith('capabilities/identity/')) return 'identity'
  if (placement?.startsWith('capabilities/wellbeing/')) return placement === 'capabilities/wellbeing/memory' ? 'memory' : 'health'
  if (placement === 'capabilities/knowledge/journals') return 'journal'
  if (placement === 'capabilities/knowledge/ideas') return 'ideas'
  return PERSONAL_SPACES.find(item => item.placement === placement)?.id
    ?? (route.destination === 'goals' ? 'goals' : 'ideas')
}

function NativeStatus({ label, load }: { label: string; load: () => Promise<PersonalAvailability<unknown>> }) {
  const [status, setStatus] = useState<'loading' | 'ready' | 'empty' | 'stale' | 'denied' | 'unavailable' | 'error'>('loading')
  const [message, setMessage] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    setStatus('loading')
    void load().then(result => {
      if (!active) return
      if (result.state === 'unavailable') { setStatus('unavailable'); setMessage(result.reason); return }
      const value = result.value
      const rows = Array.isArray(value) ? value : value && typeof value === 'object' ? Object.values(value) : []
      const stale = Array.isArray(value) && value.some(item => item && typeof item === 'object' && 'freshness' in item && item.freshness === 'stale')
      setStatus(stale ? 'stale' : rows.length ? 'ready' : 'empty')
      setMessage(stale ? `Showing saved ${label.toLowerCase()} records. Refresh is needed to confirm they are current.` : rows.length ? `${rows.length} native ${label.toLowerCase()} record${rows.length === 1 ? '' : 's'} are available.` : `No ${label.toLowerCase()} records yet.`)
    }).catch(error => {
      if (!active) return
      setStatus(error instanceof GatewayError && error.status === 403 ? 'denied' : 'error')
      setMessage(error instanceof GatewayError && error.status === 403 ? `Access to ${label.toLowerCase()} was denied for this session.` : error instanceof Error ? error.message : `${label} could not be loaded.`)
    })
    return () => { active = false }
  }, [attempt, label, load])
  return <section aria-live="polite" className="gideon-personal-status" data-state={status}>
    <p role={status === 'error' ? 'alert' : 'status'}>{status === 'loading' ? `Loading ${label.toLowerCase()}…` : message}</p>
    {(status === 'error' || status === 'denied' || status === 'stale' || status === 'unavailable') && <button type="button" onClick={() => setAttempt(value => value + 1)}>Retry {label.toLowerCase()}</button>}
  </section>
}

export function PersonalHome({ route, scope, navigate, onReturn, returnTo }: PersonalModuleProps) {
  const priorDraftScope = activeDraftScopeByOrigin.get(scope.runtimeOrigin)
  if (priorDraftScope && priorDraftScope !== scope.cacheKey) drafts.delete(priorDraftScope)
  activeDraftScopeByOrigin.set(scope.runtimeOrigin, scope.cacheKey)
  const [draftState, setDraftState] = useState(() => ({ scopeKey: scope.cacheKey, value: { ...(drafts.get(scope.cacheKey) ?? emptyDraft()) } }))
  const draft = draftState.scopeKey === scope.cacheKey ? draftState.value : drafts.get(scope.cacheKey) ?? emptyDraft()
  const space = spaceForRoute(route)
  const client = React.useMemo(() => createPersonalClient(scope), [scope.cacheKey])
  useEffect(() => () => client.dispose(), [client])
  useEffect(() => { if (draftState.scopeKey === scope.cacheKey) drafts.set(scope.cacheKey, draftState.value) }, [scope.cacheKey, draftState])
  const updateDraft = (key: keyof Draft, value: string) => setDraftState(previous => ({
    scopeKey: scope.cacheKey,
    value: { ...(previous.scopeKey === scope.cacheKey ? previous.value : draft), [key]: value },
  }))
  const ideas = space === 'ideas'
  const goals = space === 'goals'
  const title = PERSONAL_SPACES.find(item => item.id === space)?.label ?? 'Personal'
  const back = returnTo ? () => navigate(createShellRoute(returnTo.destination, {
    view: returnTo.record ? 'detail' : 'list', record: returnTo.record, placement: returnTo.placement, sessionId: returnTo.sessionId,
  })) : onReturn
  const loadIdeas = React.useCallback(() => client.readIdeas().then(value => ({ state: 'available' as const, value })), [client])
  const loadGoals = React.useCallback(() => client.readGoals().then(value => ({ state: 'available' as const, value })), [client])

  return <div className="gideon-personal-home">
    <WorkspaceFrame route={route} mode="full" title={title} onBack={back}>
      <p className="gideon-personal-intro">Keep personal decisions and progress connected to their canonical Gideon records.</p>
      {(ideas || goals) && <div className="gideon-personal-entry-grid">
        <section aria-labelledby="personal-ideas-title" className="gideon-personal-entry">
          <div><h2 id="personal-ideas-title">Ideas</h2><p>Review evidence-backed suggestions and keep a note for later.</p></div>
          <label htmlFor="personal-idea-draft">Quick note</label>
          <textarea id="personal-idea-draft" value={draft.idea} onChange={event => updateDraft('idea', event.currentTarget.value)} rows={3} />
          <NativeStatus label="Ideas" load={loadIdeas} />
        </section>
        <section aria-labelledby="personal-goals-title" className="gideon-personal-entry">
          <div><h2 id="personal-goals-title">Compass Goals</h2><p>Track human goals separately from tasks and automation.</p></div>
          <label htmlFor="personal-goal-draft">Goal draft</label>
          <textarea id="personal-goal-draft" value={draft.goal} onChange={event => updateDraft('goal', event.currentTarget.value)} rows={3} />
          <NativeStatus label="Goals" load={loadGoals} />
        </section>
      </div>}
      {!ideas && !goals && <NativeStatus label={title} load={() => {
        switch (space) {
          case 'identity': return client.readIdentityProfile().then(value => ({ state: 'available' as const, value })).catch(error => Promise.reject(error))
          case 'learning': return client.readLearningCaptures()
          case 'companion': return client.readCompanion()
          case 'memory': return client.readMemory().then(value => ({ state: 'available' as const, value }))
          case 'health': return client.readHealthMeasurements().then(value => ({ state: 'available' as const, value }))
          case 'journal': return client.readJournal(new Date().toISOString().slice(0, 10), Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
          default: return Promise.resolve({ state: 'unavailable' as const, reason: 'This personal space is unavailable.' })
        }
      }} />}
      <nav aria-label="Personal spaces" className="gideon-personal-spaces">
        {PERSONAL_SPACES.map(item => <button key={item.id} type="button" aria-current={item.id === space ? 'page' : undefined}
          onClick={() => navigate(personalSpaceRoute(item.id, { returnTo: {
            destination: route.destination, record: route.record, placement: route.placement, sessionId: route.sessionId,
            selectionId: route.record?.id,
          } }))}>{item.label}</button>)}
      </nav>
    </WorkspaceFrame>
    <style>{`
      .gideon-personal-home{box-sizing:border-box;width:100%;min-width:0;height:100%;color:var(--shell-text,#f5f2f0)}
      .gideon-personal-intro{margin:0;padding:20px 24px 4px;color:var(--shell-text-muted,#aaa5a2);max-width:70ch}
      .gideon-personal-entry-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;padding:16px 24px}
      .gideon-personal-entry{display:flex;min-width:0;flex-direction:column;gap:10px;padding:18px;border:1px solid var(--shell-border,#383432);border-radius:14px;background:var(--shell-surface,#201e1d)}
      .gideon-personal-entry h2{margin:0 0 4px;font-size:1.15rem}.gideon-personal-entry p{margin:0;color:var(--shell-text-muted,#aaa5a2)}
      .gideon-personal-entry textarea{box-sizing:border-box;width:100%;resize:vertical;border:1px solid var(--shell-border,#514b48);border-radius:8px;padding:10px;background:var(--shell-canvas,#171615);color:inherit;font:inherit}
      .gideon-personal-status{padding:4px 0}.gideon-personal-status p{margin:0}.gideon-personal-status button{margin-top:8px}
      .gideon-personal-spaces{display:flex;flex-wrap:wrap;gap:8px;padding:8px 24px 20px}
      .gideon-personal-spaces button{border:1px solid var(--shell-border,#514b48);border-radius:999px;padding:8px 12px;background:transparent;color:inherit;font:inherit;cursor:pointer}
      .gideon-personal-spaces button[aria-current="page"]{border-color:var(--shell-accent,#ff806e);background:color-mix(in srgb,var(--shell-accent,#ff806e) 14%,transparent)}
      .gideon-personal-spaces button:focus-visible,.gideon-personal-entry textarea:focus-visible{outline:2px solid var(--shell-accent,#ff806e);outline-offset:2px}
      @media(max-width:650px){.gideon-personal-entry-grid{grid-template-columns:minmax(0,1fr);padding:14px 16px}.gideon-personal-intro{padding:16px 16px 4px}.gideon-personal-spaces{padding-inline:16px}}
    `}</style>
  </div>
}

export default PersonalHome
