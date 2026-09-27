import React, { useEffect, useMemo, useRef, useState } from 'react'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { GatewayError } from '../../shared/transport.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { createPersonalClient, type GoalMilestone, type GoalPlanProjection, type GoalSession, type HumanGoal, type PersonalRecord } from './client'

type SourceChoice = Readonly<{ kind: 'task' | 'loop' | 'session'; id: string; title: string; status: string }>
const sourceKey = (choice: Pick<SourceChoice, 'kind' | 'id'>) => `${choice.kind}:${choice.id}`
const buttonStyle: React.CSSProperties = { minHeight: 42, borderRadius: 9, padding: '8px 13px', font: 'inherit', cursor: 'pointer' }
const errorText = (error: unknown) => error instanceof GatewayError && error.status === 409
  ? 'This goal or plan changed elsewhere. Your edits are still here; reload before saving again.'
  : error instanceof Error ? error.message : 'The change could not be saved. Your edits are still here.'

export function GoalPlanWorkspace({ route, scope, navigate, onReturn }: ModuleProps) {
  const { palette } = useShellTheme()
  const client = useMemo(() => createPersonalClient(scope), [scope.cacheKey])
  const id = route.record?.id ?? ''
  const recordKey = JSON.stringify([scope.cacheKey, route.record?.kind ?? '', id])
  const recordKeyRef = useRef(recordKey)
  recordKeyRef.current = recordKey
  const [loadedKey, setLoadedKey] = useState('')
  const [storedData, setData] = useState<GoalPlanProjection | null>(null)
  const [storedGoals, setGoals] = useState<readonly PersonalRecord<HumanGoal>[]>([])
  const [storedSessions, setSessions] = useState<readonly PersonalRecord<GoalSession>[]>([])
  const [storedSources, setSources] = useState<readonly SourceChoice[]>([])
  const [storedMilestones, setMilestones] = useState<GoalMilestone[]>([])
  const data = loadedKey === recordKey ? storedData : null
  const goals = loadedKey === recordKey ? storedGoals : []
  const sessions = loadedKey === recordKey ? storedSessions : []
  const sources = loadedKey === recordKey ? storedSources : []
  const milestones = loadedKey === recordKey ? storedMilestones : []
  const [horizon, setHorizon] = useState<'short_term' | 'long_term' | 'lifetime'>('long_term')
  const [parentId, setParentId] = useState('')
  const [unit, setUnit] = useState('units')
  const [targetValue, setTargetValue] = useState('')
  const [selectedSource, setSelectedSource] = useState('')
  const [errorState, setErrorState] = useState({ key: recordKey, value: '' })
  const [noticeState, setNoticeState] = useState({ key: recordKey, value: '' })
  const [checkinValue, setCheckinValue] = useState('')
  const [checkinNotes, setCheckinNotes] = useState('')
  const [sessionTitle, setSessionTitle] = useState('')
  const [sessionStart, setSessionStart] = useState('')
  const [sessionEnd, setSessionEnd] = useState('')
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [newMilestone, setNewMilestone] = useState('')
  const error = errorState.key === recordKey ? errorState.value : ''
  const notice = noticeState.key === recordKey ? noticeState.value : ''
  const setError = (value: string) => setErrorState({ key: recordKey, value })
  const setNotice = (value: string) => setNoticeState({ key: recordKey, value })

  useEffect(() => () => client.dispose(), [client])
  useEffect(() => {
    let active = true
    setError('')
    if (!id || !route.record || !['human-goal', 'goal-plan'].includes(route.record.kind)) {
      setError('Select a human goal to open its plan.')
      return () => { active = false }
    }
    const load = async () => {
      const [plan, allGoals, goalSessions, sourceRows] = await Promise.all([
        client.readGoalPlan(id), client.readGoals(), client.readGoalSessions(id), client.readGoalSources(),
      ])
      if (plan.identity.ownerScopeKey !== scope.cacheKey || plan.identity.nativeId !== id || plan.value.goal.id !== id || plan.value.plan.goal_id !== id) throw new Error('The loaded plan belongs to a different account or goal.')
      if (!active || recordKeyRef.current !== recordKey) return
      setData(plan.value); setGoals(allGoals); setSessions(goalSessions)
      setSources([...sourceRows, ...goalSessions.map(item => ({ kind: 'session' as const, id: item.identity.nativeId, title: item.value.title, status: item.value.status }))])
      setMilestones(plan.value.plan.milestones.map(item => ({ ...item })))
      setHorizon(plan.value.plan.horizon); setParentId(plan.value.plan.parent_id ?? '')
      setUnit(plan.value.plan.unit); setTargetValue(plan.value.plan.target_value == null ? '' : String(plan.value.plan.target_value))
      setNewMilestone(''); setCheckinValue(''); setCheckinNotes(''); setSessionTitle(''); setSessionStart(''); setSessionEnd('')
      setLoadedKey(recordKey)
    }
    void load().catch(reason => {
      if (!active || recordKeyRef.current !== recordKey) return
      if (reason instanceof GatewayError && [401, 403, 404].includes(reason.status)) {
        setData(null); setGoals([]); setSessions([]); setSources([]); setMilestones([]); setLoadedKey('')
      }
      setError(data && !(reason instanceof GatewayError && [401, 403, 404].includes(reason.status))
        ? `Showing the last saved plan. ${errorText(reason)}`
        : errorText(reason))
    })
    return () => { active = false }
  }, [client, id, attempt, route.record?.kind, scope.cacheKey, recordKey])

  const source = { '--goal-text': palette.text, '--goal-muted': palette.muted, '--goal-line': palette.line, '--goal-card': palette.card, '--goal-canvas': palette.canvas, '--goal-accent': palette.blueDark, '--goal-accent-surface': palette.sky } as React.CSSProperties
  const savePlan = async () => {
    if (!data || busyKey === recordKey) return
    const key = recordKey
    setBusyKey(key); setError(''); setNotice('')
    try {
      await client.configureGoalPlan({ goal_id: id, parent_id: parentId || null, horizon, milestones, links: data.plan.links.map(link => ({ kind: link.kind, id: link.id })), unit: unit.trim(), target_value: targetValue === '' ? null : Number(targetValue), expected_revision: data.plan.revision, request_id: crypto.randomUUID() })
      if (recordKeyRef.current !== key) return
      setNotice('Plan saved at the current revision.'); setAttempt(value => value + 1)
    } catch (reason) { if (recordKeyRef.current === key) setError(errorText(reason)) }
    finally { setBusyKey(current => current === key ? null : current) }
  }
  const toggleMilestone = (milestoneId: string) => setMilestones(current => current.map(item => item.id === milestoneId ? { ...item, done: !item.done } : item))
  const addMilestone = () => {
    if (!newMilestone.trim()) return
    setMilestones(current => [...current, { id: crypto.randomUUID(), title: newMilestone.trim(), done: false, target_date: null }])
    setNewMilestone('')
  }
  const updateMilestone = (milestoneId: string, field: 'title' | 'target_date', value: string) => setMilestones(current => current.map(item => item.id === milestoneId ? { ...item, [field]: field === 'target_date' ? value || null : value } : item))
  const saveCheckin = async (event: React.FormEvent) => {
    event.preventDefault(); if (!data || busyKey === recordKey || checkinValue === '') return
    const key = recordKey
    setBusyKey(key); setError(''); setNotice('')
    try {
      const result = await client.addGoalCheckin({ goal_id: id, value: Number(checkinValue), observed_at: new Date().toISOString(), notes: checkinNotes.trim(), request_id: crypto.randomUUID() })
      if (result.goal_id !== id) throw new Error('Native check-in belongs to a different goal.')
      if (recordKeyRef.current !== key) return
      setCheckinValue(''); setCheckinNotes(''); setNotice('Measurement saved to this goal.'); setAttempt(value => value + 1)
    } catch (reason) { if (recordKeyRef.current === key) setError(errorText(reason)) }
    finally { setBusyKey(current => current === key ? null : current) }
  }
  const saveSession = async (event: React.FormEvent) => {
    event.preventDefault(); if (!data || busyKey === recordKey || !sessionTitle.trim() || !sessionStart || !sessionEnd) return
    const key = recordKey
    setBusyKey(key); setError(''); setNotice('')
    try {
      const result = await client.saveGoalSession({ goal_id: id, title: sessionTitle.trim(), start_at: new Date(sessionStart).toISOString(), end_at: new Date(sessionEnd).toISOString(), request_id: crypto.randomUUID(), status: 'scheduled' })
      if (result.identity.ownerScopeKey !== scope.cacheKey || result.value.goal_id !== id) throw new Error('Native session belongs to a different account or goal.')
      if (recordKeyRef.current !== key) return
      setSessionTitle(''); setNotice('Session saved to this goal.'); setAttempt(value => value + 1)
    } catch (reason) { if (recordKeyRef.current === key) setError(errorText(reason)) }
    finally { setBusyKey(current => current === key ? null : current) }
  }
  const addSource = () => {
    const selected = sources.find(item => sourceKey(item) === selectedSource)
    if (!selected || data?.plan.links.some(link => link.kind === selected.kind && link.id === selected.id)) return
    setData(current => current ? { ...current, plan: { ...current.plan, links: [...current.plan.links, selected] } } : current)
    setSelectedSource('')
  }
  const removeSource = (kind: string, sourceId: string) => setData(current => current ? { ...current, plan: { ...current.plan, links: current.plan.links.filter(link => !(link.kind === kind && link.id === sourceId)) } } : current)

  return <main className="gideon-goal-plan" style={source}>
    <WorkspaceFrame route={route} mode="full" title="Compass Goal" onBack={onReturn}>
      <div className="gideon-goal-plan__header"><div><p className="gideon-goal-plan__eyebrow">Human goal and plan</p><h1>{data?.goal.title ?? 'Goal plan'}</h1><p>{data?.goal.description || 'Build a measurable plan for this commitment.'}</p></div><div className="gideon-goal-plan__identity">Goal ID <code>{id || 'Unavailable'}</code></div></div>
      {error && <p role="alert" className="gideon-goal-plan__error">{error} {data && <button type="button" style={buttonStyle} onClick={() => setAttempt(value => value + 1)}>Reload native state</button>}</p>}
      {notice && <p role="status" className="gideon-goal-plan__notice">{notice}</p>}
      {!data && !error && <p role="status">Loading the human goal and its plan…</p>}
      {data && <div className="gideon-goal-plan__grid">
        <section className="gideon-goal-plan__panel" aria-labelledby="goal-plan-settings"><h2 id="goal-plan-settings">Shape the plan</h2>
          <p>Plan revision {data.plan.revision}. Saves use that revision and a unique request ID.</p>
          <label htmlFor="goal-parent">Parent goal</label><select id="goal-parent" value={parentId} onChange={event => setParentId(event.currentTarget.value)}><option value="">No parent</option>{goals.filter(item => item.identity.nativeId !== id).map(item => <option key={item.identity.nativeId} value={item.identity.nativeId}>{item.value.title}</option>)}</select>
          <label htmlFor="goal-horizon">Horizon</label><select id="goal-horizon" value={horizon} onChange={event => setHorizon(event.currentTarget.value as typeof horizon)}><option value="short_term">Short term</option><option value="long_term">Long term</option><option value="lifetime">Lifetime</option></select>
          <div className="gideon-goal-plan__metric"><div><label htmlFor="goal-unit">Metric unit</label><input id="goal-unit" maxLength={40} value={unit} onChange={event => setUnit(event.currentTarget.value)} /></div><div><label htmlFor="goal-target-value">Target value <span>Optional</span></label><input id="goal-target-value" type="number" step="any" value={targetValue} onChange={event => setTargetValue(event.currentTarget.value)} /></div></div>
          <p>Explicit progress: {data.milestones_complete_ratio == null ? 'No milestones completed' : `${Math.round(data.milestones_complete_ratio * 100)}% of milestones marked complete by you`}.</p>
        </section>
        <section className="gideon-goal-plan__panel" aria-labelledby="goal-milestones"><div className="gideon-goal-plan__section-title"><h2 id="goal-milestones">Milestones</h2><span>{milestones.filter(item => item.done).length} of {milestones.length} complete</span></div>
          {!milestones.length && <p>No milestones yet.</p>}
          <ul className="gideon-goal-plan__milestones">{milestones.map(item => <li key={item.id} className={item.done ? 'is-done' : ''}>
            <label className="gideon-goal-plan__check"><input aria-label={`Mark ${item.title} complete`} type="checkbox" checked={item.done} onChange={() => toggleMilestone(item.id)} /><span>Human confirmation</span></label>
            <input aria-label="Milestone title" value={item.title} onChange={event => updateMilestone(item.id, 'title', event.currentTarget.value)} />
            <label className="gideon-goal-plan__date">Target <input aria-label="Milestone target date" type="date" value={item.target_date ?? ''} onChange={event => updateMilestone(item.id, 'target_date', event.currentTarget.value)} /></label>
            <button type="button" className="gideon-goal-plan__quiet" onClick={() => setMilestones(current => current.filter(row => row.id !== item.id))}>Remove</button>
          </li>)}</ul>
          <div className="gideon-goal-plan__add-row"><label className="sr-only" htmlFor="new-milestone">New milestone</label><input id="new-milestone" placeholder="Name the next milestone" value={newMilestone} onChange={event => setNewMilestone(event.currentTarget.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addMilestone() } }} /><button type="button" style={buttonStyle} disabled={!newMilestone.trim()} onClick={addMilestone}>Add milestone</button></div>
          <p>Task and session outcomes stay as linked evidence. Only this checkbox records that you completed a human milestone.</p>
        </section>
        <section className="gideon-goal-plan__panel" aria-labelledby="goal-sessions"><h2 id="goal-sessions">Sessions</h2><p>Scheduled sessions remain attached to goal <code>{id}</code>.</p>
          <ul className="gideon-goal-plan__rows">{sessions.map(session => <li key={session.identity.nativeId}><strong>{session.value.title}</strong><span>{new Date(session.value.start_at).toLocaleString()} – {new Date(session.value.end_at).toLocaleTimeString()}</span><small>{session.value.status} · session {session.identity.nativeId}</small></li>)}</ul>
          <form className="gideon-goal-plan__form" onSubmit={saveSession}><label htmlFor="goal-session-title">Plan a session</label><input id="goal-session-title" required maxLength={200} placeholder="Practice, appointment or review" value={sessionTitle} onChange={event => setSessionTitle(event.currentTarget.value)} /><div className="gideon-goal-plan__metric"><div><label htmlFor="goal-session-start">Starts</label><input id="goal-session-start" type="datetime-local" required value={sessionStart} onChange={event => setSessionStart(event.currentTarget.value)} /></div><div><label htmlFor="goal-session-end">Ends</label><input id="goal-session-end" type="datetime-local" required value={sessionEnd} onChange={event => setSessionEnd(event.currentTarget.value)} /></div></div><button type="submit" style={buttonStyle} disabled={busyKey === recordKey || !sessionTitle.trim()}>Save session</button></form>
        </section>
        <section className="gideon-goal-plan__panel" aria-labelledby="goal-checkins"><h2 id="goal-checkins">Metric check-ins</h2><p>Observations are human reported and use <strong>{data.plan.unit}</strong>.</p>
          <ul className="gideon-goal-plan__rows">{data.checkins.map(row => <li key={String(row.id)}><strong>{String(row.value)} {String(row.unit)}</strong><span>{new Date(String(row.observed_at)).toLocaleString()}</span><small>{String(row.source)}{row.notes ? ` · ${String(row.notes)}` : ''}</small></li>)}</ul>
          {!data.checkins.length && <p>No measurements recorded yet.</p>}
          {data.velocity && <p className="gideon-goal-plan__velocity">Observed rate: {String(data.velocity.value_per_day)} {String(data.velocity.unit)} per day</p>}
          <form className="gideon-goal-plan__form" onSubmit={saveCheckin}><label htmlFor="goal-checkin-value">Record a measurement</label><input id="goal-checkin-value" required type="number" step="any" value={checkinValue} onChange={event => setCheckinValue(event.currentTarget.value)} /><label htmlFor="goal-checkin-notes">Note</label><textarea id="goal-checkin-notes" rows={2} maxLength={5000} value={checkinNotes} onChange={event => setCheckinNotes(event.currentTarget.value)} /><button type="submit" style={buttonStyle} disabled={busyKey === recordKey || checkinValue === ''}>Save check-in</button></form>
        </section>
        <section className="gideon-goal-plan__panel" aria-labelledby="goal-sources"><h2 id="goal-sources">Source links</h2><p>Link real tasks, sessions or running programs while keeping their native identity.</p>
          <div className="gideon-goal-plan__add-row"><label className="sr-only" htmlFor="goal-source-select">Choose a source record</label><select id="goal-source-select" value={selectedSource} onChange={event => setSelectedSource(event.currentTarget.value)}><option value="">Choose a task, session or program</option>{sources.map(item => <option key={sourceKey(item)} value={sourceKey(item)}>{item.title} · {item.kind} · {item.status}</option>)}</select><button type="button" style={buttonStyle} onClick={addSource} disabled={!selectedSource}>Link source</button></div>
          <ul className="gideon-goal-plan__rows">{data.plan.links.map(link => <li key={sourceKey(link)}><strong>{link.title || 'Unavailable source'}</strong><span>{link.kind} · {link.status ?? link.availability ?? 'status unavailable'}</span><small>Source ID {link.id}</small><button type="button" className="gideon-goal-plan__quiet" onClick={() => removeSource(link.kind, link.id)}>Remove link</button></li>)}</ul>
          {!data.plan.links.length && <p>No source links yet.</p>}
        </section>
        <section className="gideon-goal-plan__panel" aria-labelledby="goal-hierarchy"><h2 id="goal-hierarchy">Hierarchy and provenance</h2><p>Parent: {parentId ? goals.find(item => item.identity.nativeId === parentId)?.value.title ?? parentId : 'Top-level goal'}</p><p>Child goal IDs: {data.children.length ? data.children.join(', ') : 'None'}</p><p>Source record <code>{id}</code> · plan revision {data.plan.revision}</p><button type="button" style={buttonStyle} onClick={savePlan} disabled={busyKey === recordKey || !unit.trim()}>{busyKey === recordKey ? 'Saving…' : 'Save plan'}</button> <button type="button" style={buttonStyle} onClick={() => setAttempt(value => value + 1)}>Reload</button></section>
      </div>}
    </WorkspaceFrame>
    <style>{`
      .gideon-goal-plan{box-sizing:border-box;width:100%;min-width:0;height:100%;color:var(--goal-text)}.gideon-goal-plan__header{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;padding:20px 24px 12px}.gideon-goal-plan__header h1{margin:0 0 6px;font-size:1.55rem}.gideon-goal-plan__header p{margin:0;color:var(--goal-muted);max-width:70ch}.gideon-goal-plan__eyebrow{font-size:.8rem;text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px!important}.gideon-goal-plan__identity{display:grid;gap:5px;max-width:260px;color:var(--goal-muted);font-size:.8rem}.gideon-goal-plan code{overflow-wrap:anywhere}.gideon-goal-plan__grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:14px;padding:8px 24px 24px}.gideon-goal-plan__panel{min-width:0;padding:17px;border:1px solid var(--goal-line);border-radius:13px;background:var(--goal-card)}.gideon-goal-plan__panel h2{margin:0 0 5px;font-size:1.1rem}.gideon-goal-plan__panel p{color:var(--goal-muted);margin:0 0 12px}.gideon-goal-plan label{display:grid;gap:5px;margin:8px 0 5px;font-weight:600}.gideon-goal-plan label span{font-size:.8em;color:var(--goal-muted);font-weight:400}.gideon-goal-plan input:not([type=checkbox]),.gideon-goal-plan select,.gideon-goal-plan textarea{box-sizing:border-box;width:100%;min-width:0;min-height:41px;padding:8px 10px;border:1px solid var(--goal-line);border-radius:8px;background:var(--goal-canvas);color:inherit;font:inherit}.gideon-goal-plan textarea{resize:vertical}.gideon-goal-plan__metric{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.gideon-goal-plan__metric label{margin-top:10px}.gideon-goal-plan__section-title{display:flex;justify-content:space-between;gap:10px;align-items:center}.gideon-goal-plan__section-title span{font-size:.82rem;color:var(--goal-muted)}.gideon-goal-plan__milestones,.gideon-goal-plan__rows{list-style:none;margin:0;padding:0;display:grid;gap:9px}.gideon-goal-plan__milestones li,.gideon-goal-plan__rows li{display:grid;gap:6px;padding:10px;border:1px solid var(--goal-line);border-radius:9px;background:var(--goal-canvas)}.gideon-goal-plan__check{display:flex!important;align-items:center;gap:8px!important;margin:0!important;font-size:.8rem;color:var(--goal-muted)}.gideon-goal-plan__check input{width:18px;height:18px;accent-color:var(--goal-accent)}.gideon-goal-plan__milestones li.is-done>input{opacity:.78;text-decoration:line-through}.gideon-goal-plan__date{display:flex!important;align-items:center;gap:10px!important;font-size:.83rem!important}.gideon-goal-plan__date input{margin-left:auto}.gideon-goal-plan__rows li strong{overflow-wrap:anywhere}.gideon-goal-plan__rows span,.gideon-goal-plan__rows small{color:var(--goal-muted)}.gideon-goal-plan__add-row{display:flex;align-items:end;gap:8px;margin:10px 0}.gideon-goal-plan__add-row select,.gideon-goal-plan__add-row input{flex:1}.gideon-goal-plan__quiet{justify-self:start;border:0;background:none;padding:4px;color:var(--goal-muted);text-decoration:underline;cursor:pointer;font:inherit}.gideon-goal-plan__form{display:grid;gap:7px;margin-top:12px}.gideon-goal-plan__notice{margin:8px 24px;padding:10px;border-radius:8px;background:var(--goal-accent-surface)}.gideon-goal-plan__error{margin:8px 24px;color:#9d2020}.gideon-goal-plan__velocity{padding:9px;border-radius:8px;background:var(--goal-accent-surface)}.gideon-goal-plan button:focus-visible,.gideon-goal-plan input:focus-visible,.gideon-goal-plan select:focus-visible,.gideon-goal-plan textarea:focus-visible{outline:2px solid var(--goal-accent);outline-offset:2px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
      @media(max-width:760px){.gideon-goal-plan__header{display:grid;padding:16px}.gideon-goal-plan__grid{grid-template-columns:minmax(0,1fr);padding:8px 16px 20px}.gideon-goal-plan__metric{grid-template-columns:minmax(0,1fr)}.gideon-goal-plan__add-row{align-items:stretch;flex-direction:column}.gideon-goal-plan__identity{max-width:none}}
    `}</style>
  </main>
}

export default GoalPlanWorkspace
