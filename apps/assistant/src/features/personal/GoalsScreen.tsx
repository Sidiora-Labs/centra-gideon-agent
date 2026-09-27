import React, { useEffect, useMemo, useRef, useState } from 'react'
import type { ModuleProps } from '../../shared/shell/webModules.web'
import { GatewayError } from '../../shared/transport.web'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { createShellRoute } from '../../shared/shell/shellRoutes'
import { useShellTheme } from '../../shared/shell/shellTheme.web'
import { clearPendingGoalWrite, createPersonalClient, pendingGoalWriteKey, preparePendingGoalWrite, readPendingGoalWrite, rejectPendingGoalWrite, type HumanGoal, type PersonalRecord } from './client'

const buttonStyle: React.CSSProperties = { minHeight: 42, borderRadius: 9, padding: '8px 14px', font: 'inherit', cursor: 'pointer' }

export function GoalsScreen({ route, scope, navigate, onReturn }: ModuleProps) {
  const { palette } = useShellTheme()
  const client = useMemo(() => createPersonalClient(scope), [scope.cacheKey])
  const [goalsState, setGoalsState] = useState<{ owner: string; rows: readonly PersonalRecord<HumanGoal>[] } | null>(null)
  const [titleState, setTitleState] = useState({ owner: scope.cacheKey, value: '' })
  const [descriptionState, setDescriptionState] = useState({ owner: scope.cacheKey, value: '' })
  const [targetDateState, setTargetDateState] = useState({ owner: scope.cacheKey, value: '' })
  const [busyOwner, setBusyOwner] = useState<string | null>(null)
  const [errorState, setErrorState] = useState({ owner: scope.cacheKey, value: '' })
  const [noticeState, setNoticeState] = useState({ owner: scope.cacheKey, value: '' })
  const [attempt, setAttempt] = useState(0)
  const scopeRef = useRef(scope.cacheKey)
  scopeRef.current = scope.cacheKey
  const title = titleState.owner === scope.cacheKey ? titleState.value : ''
  const description = descriptionState.owner === scope.cacheKey ? descriptionState.value : ''
  const targetDate = targetDateState.owner === scope.cacheKey ? targetDateState.value : ''
  const goals = goalsState?.owner === scope.cacheKey ? goalsState.rows : []
  const error = errorState.owner === scope.cacheKey ? errorState.value : ''
  const notice = noticeState.owner === scope.cacheKey ? noticeState.value : ''
  const createKey = pendingGoalWriteKey(scope.cacheKey, 'new-goal', 'create')
  const pendingCreate = readPendingGoalWrite(createKey)
  const pendingTitle = typeof pendingCreate?.body.title === 'string' ? pendingCreate.body.title : ''
  const pendingDescription = typeof pendingCreate?.body.description === 'string' ? pendingCreate.body.description : ''
  const pendingTargetDate = typeof pendingCreate?.body.target_date === 'string' ? pendingCreate.body.target_date : ''
  const setError = (value: string) => setErrorState({ owner: scope.cacheKey, value })
  const setNotice = (value: string) => setNoticeState({ owner: scope.cacheKey, value })

  useEffect(() => () => client.dispose(), [client])
  useEffect(() => {
    let active = true
    setError('')
    void client.readGoals().then(rows => { if (active && scopeRef.current === scope.cacheKey) setGoalsState({ owner: scope.cacheKey, rows }) }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : 'Goals could not be loaded.')
    })
    return () => { active = false }
  }, [client, attempt])

  const open = (record: PersonalRecord<HumanGoal>) => navigate(createShellRoute('goals', {
    view: 'detail', placement: { id: 'goals' }, record: { kind: 'human-goal', id: record.identity.nativeId },
    returnTo: { destination: route.destination, record: route.record, placement: route.placement, sessionId: route.sessionId },
  }))
  const create = async (event: React.FormEvent) => {
    event.preventDefault()
    if (busyOwner === scope.cacheKey || (!title.trim() && !pendingCreate)) return
    const owner = scope.cacheKey
    setBusyOwner(owner); setError(''); setNotice('')
    try {
      const operation = preparePendingGoalWrite(createKey, { title: title.trim() || pendingTitle, description: description.trim() || pendingDescription, status: 'active', target_date: (targetDate || pendingTargetDate) || null, expected_revision: 0 })
      const record = await client.saveGoal(undefined, operation.body as { title: string; description: string; status: 'active'; target_date: string | null }, operation.request_id, Number(operation.body.expected_revision ?? 0))
      if (record.identity.ownerScopeKey !== scope.cacheKey || record.identity.nativeId !== record.value.id) throw new Error('The saved goal belongs to a different account or identity.')
      if (scopeRef.current !== owner) return
      clearPendingGoalWrite(createKey)
      setTitleState({ owner, value: '' }); setDescriptionState({ owner, value: '' }); setTargetDateState({ owner, value: '' }); setNotice('Goal saved. Open it to shape its plan and progress.')
      const rows = await client.readGoals()
      if (scopeRef.current !== owner) return
      setGoalsState({ owner, rows })
    } catch (reason) {
      if (reason instanceof GatewayError && [400, 409].includes(reason.status)) rejectPendingGoalWrite(createKey)
      if (scopeRef.current === owner) setError(reason instanceof Error ? reason.message : 'Goal could not be saved. Your draft is preserved.')
    }
    finally { setBusyOwner(current => current === owner ? null : current) }
  }

  return <div className="gideon-goals" style={{ '--goals-text': palette.text, '--goals-muted': palette.muted, '--goals-line': palette.line, '--goals-card': palette.card, '--goals-canvas': palette.canvas, '--goals-accent': palette.blueDark, '--goals-accent-surface': palette.sky } as React.CSSProperties}>
    <WorkspaceFrame route={route} mode="full" title="Compass Goals" onBack={onReturn}>
      <p className="gideon-goals__intro">Keep human commitments, measurable progress and work links together. Completing a task never completes a milestone for you.</p>
      <div className="gideon-goals__layout">
        <section aria-labelledby="goals-list-heading" className="gideon-goals__panel">
          <div className="gideon-goals__panel-title"><div><h2 id="goals-list-heading">Your goals</h2><p>{goals.length} saved human goal{goals.length === 1 ? '' : 's'}</p></div><button type="button" style={buttonStyle} onClick={() => setAttempt(value => value + 1)}>Refresh</button></div>
          {error && <p role="alert" className="gideon-goals__error">{error}<button type="button" onClick={() => setAttempt(value => value + 1)}>Retry</button></p>}
          {notice && <p role="status">{notice}</p>}
          {!error && !goals.length && <p role="status">No goals yet. Start with a commitment you want to carry forward.</p>}
          <ul className="gideon-goals__list">{goals.map(goal => <li key={goal.identity.nativeId}>
            <button type="button" className="gideon-goals__goal" onClick={() => open(goal)}>
              <span className="gideon-goals__goal-copy"><strong>{goal.value.title}</strong><span>{goal.value.description || 'No description yet'}</span><small>{goal.value.status} · {goal.value.target_date ? `target ${goal.value.target_date}` : 'no target date'}</small></span>
            </button>
          </li>)}</ul>
        </section>
        <section aria-labelledby="goal-create-heading" className="gideon-goals__panel">
          <h2 id="goal-create-heading">Name a goal</h2><p>Save an active human goal, then add milestones, sessions and measurements.</p>
          {pendingCreate && <p role="status">{pendingCreate.rejected ? 'The native service rejected this goal save. Review the fields and submit again.' : 'A goal save needs confirmation. Retrying sends the same saved change.'}</p>}
          <form className="gideon-goals__form" onSubmit={create}>
            <label htmlFor="goal-title">Goal title</label><input id="goal-title" required maxLength={200} disabled={!!pendingCreate && !pendingCreate.rejected} value={title || pendingTitle} onChange={event => setTitleState({ owner: scope.cacheKey, value: event.currentTarget.value })} />
            <label htmlFor="goal-description">What does success mean?</label><textarea id="goal-description" rows={3} maxLength={10000} disabled={!!pendingCreate && !pendingCreate.rejected} value={description || pendingDescription} onChange={event => setDescriptionState({ owner: scope.cacheKey, value: event.currentTarget.value })} />
            <label htmlFor="goal-target-date">Target date <span>Optional</span></label><input id="goal-target-date" type="date" disabled={!!pendingCreate && !pendingCreate.rejected} value={targetDate || pendingTargetDate} onChange={event => setTargetDateState({ owner: scope.cacheKey, value: event.currentTarget.value })} />
            <button type="submit" style={buttonStyle} disabled={busyOwner === scope.cacheKey || (!title.trim() && !pendingCreate)}>{busyOwner === scope.cacheKey ? 'Saving…' : pendingCreate ? pendingCreate.rejected ? 'Save updated goal' : 'Retry goal save' : 'Save goal'}</button>
          </form>
        </section>
      </div>
    </WorkspaceFrame>
    <style>{`
      .gideon-goals{box-sizing:border-box;width:100%;min-width:0;height:100%;color:var(--goals-text)}.gideon-goals__intro{margin:0;padding:18px 24px 4px;color:var(--goals-muted);max-width:75ch}
      .gideon-goals__layout{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:16px;padding:16px 24px 24px}.gideon-goals__panel{min-width:0;padding:18px;border:1px solid var(--goals-line);border-radius:14px;background:var(--goals-card)}
      .gideon-goals__panel h2{margin:0 0 4px;font-size:1.12rem}.gideon-goals__panel p{margin:0 0 12px;color:var(--goals-muted)}.gideon-goals__panel-title{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.gideon-goals__panel-title p{margin:3px 0 12px}
      .gideon-goals__list{list-style:none;margin:0;padding:0;display:grid;gap:10px}.gideon-goals__goal{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;width:100%;text-align:left;border:1px solid var(--goals-line);border-radius:10px;padding:13px;background:var(--goals-canvas);color:inherit;font:inherit;cursor:pointer}.gideon-goals__goal:hover{border-color:var(--goals-accent)}.gideon-goals__goal-copy{display:grid;gap:5px;min-width:0}.gideon-goals__goal-copy span,.gideon-goals__goal-copy small{color:var(--goals-muted)}.gideon-goals__goal-id{display:grid;gap:4px;color:var(--goals-muted);font-size:.75rem}.gideon-goals code{overflow-wrap:anywhere}.gideon-goals__form{display:grid;gap:9px}.gideon-goals__form label{font-weight:600;margin-top:5px}.gideon-goals__form label span{font-size:.8em;font-weight:400;color:var(--goals-muted)}.gideon-goals__form input,.gideon-goals__form textarea{box-sizing:border-box;width:100%;min-height:42px;border:1px solid var(--goals-line);border-radius:8px;padding:9px;background:var(--goals-canvas);color:inherit;font:inherit}.gideon-goals__form textarea{resize:vertical}.gideon-goals button:focus-visible,.gideon-goals input:focus-visible,.gideon-goals textarea:focus-visible{outline:2px solid var(--goals-accent);outline-offset:2px}.gideon-goals__error{color:#a12626!important}
      @media(max-width:760px){.gideon-goals__layout{grid-template-columns:minmax(0,1fr);padding:14px 16px}.gideon-goals__intro{padding:16px 16px 4px}.gideon-goals__goal{display:grid}.gideon-goals__goal-id{grid-template-columns:auto 1fr;align-items:baseline}}
    `}</style>
  </div>
}

export default GoalsScreen
