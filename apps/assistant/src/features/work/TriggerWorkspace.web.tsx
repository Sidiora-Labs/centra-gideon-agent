import React, { useEffect, useRef, useState } from 'react'
import type { Trigger } from '../../../../console/src/shared/data/api'
import type { OwnerScope } from '../../shared/auth.web'
import type { ShellRoute } from '../../shared/shell/shellRoutes'
import { WorkspaceFrame } from '../../shared/shell/WorkspaceFrame.web'
import { gatewayJson } from '../../shared/transport.web'
import { createWorkRoute } from './workRouteModel'

type Props = { route: ShellRoute; scope: OwnerScope; triggerId?: string; creating?: boolean; onBack: () => void; navigate: (route: ShellRoute) => void }
type History = { runs?: Array<Record<string, unknown>>; entries?: Array<Record<string, unknown>>; supported?: boolean }
const button: React.CSSProperties = { minHeight: 44, borderRadius: 9, padding: '8px 12px', font: 'inherit', cursor: 'pointer' }
const input: React.CSSProperties = { minHeight: 40, boxSizing: 'border-box', width: '100%', maxWidth: 620, padding: '8px 10px', font: 'inherit' }

export default function TriggerWorkspace({ route, scope, triggerId, creating = false, onBack, navigate }: Props) {
  const [rows, setRows] = useState<Trigger[]>([])
  const [rowsLoaded, setRowsLoaded] = useState(false)
  const [history, setHistory] = useState<History | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [type, setType] = useState<'schedule' | 'event' | 'lifecycle'>('schedule')
  const [name, setName] = useState('')
  const [schedule, setSchedule] = useState('0 9 * * *')
  const [event, setEvent] = useState('MemoryUpdate')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const stableOwner = scope.cacheKey
  const actionEpoch = useRef(0)

  useEffect(() => {
    const epoch = ++actionEpoch.current
    return () => { if (actionEpoch.current === epoch) actionEpoch.current += 1 }
  }, [scope.cacheKey, triggerId, creating, route.placement?.id, route.record?.id])

  useEffect(() => {
    let live = true
    const controller = new AbortController()
    setRows([]); setRowsLoaded(false); setHistory(null); setHistoryError(''); setError(''); setNotice('')
    gatewayJson<{ triggers: Trigger[] }>('/api/triggers', { signal: controller.signal }).then(value => {
      if (live && scope.cacheKey === stableOwner) { setRows(value.triggers); setRowsLoaded(true) }
    }).catch(reason => { if (live && !controller.signal.aborted) setError(message(reason)) })
    if (triggerId) gatewayJson<History>(`/api/triggers/${encodeURIComponent(triggerId)}/history`, { signal: controller.signal })
      .then(value => { if (live && scope.cacheKey === stableOwner) { setHistory(value); setHistoryError('') } })
      .catch(reason => { if (live && !controller.signal.aborted) { setHistory(null); setHistoryError(`Native run history could not be loaded. ${message(reason)}`) } })
    return () => { live = false; controller.abort() }
  }, [scope.cacheKey, triggerId, revision])

  const selected = rows.find(row => row.id === triggerId) ?? null
  const submit = async () => {
    if (!name.trim() || busy) { if (!name.trim()) setError('Name this automation before saving.'); return }
    const epoch = actionEpoch.current
    setBusy(true); setError(''); setNotice('')
    try {
      const body = type === 'schedule'
        ? { trigger_type: type, name: name.trim(), cron: schedule.trim(), action: { provider: 'notify', config: { title_template: name.trim(), body_template: `Scheduled automation: ${name.trim()}`, kind: 'success' } } }
        : type === 'event'
          ? { trigger_type: type, name: name.trim(), pattern: event, action: { provider: 'notify', config: {} } }
          : { trigger_type: type, name: name.trim(), event, action: { provider: 'notify', config: {} } }
      const result = await gatewayJson<{ trigger?: Trigger; id?: string }>('/api/triggers', { method: 'POST', body })
      if (epoch !== actionEpoch.current || scope.cacheKey !== stableOwner) return
      const id = result.trigger?.id ?? result.id
      if (!id) { setError('Gideon did not return a native trigger ID; the draft is still available.'); return }
      navigate(createWorkRoute('triggers', id, route.returnTo))
    } catch (reason) { if (epoch === actionEpoch.current && scope.cacheKey === stableOwner) setError(`The native write did not complete. Your draft is preserved. ${message(reason)}`) }
    finally { if (epoch === actionEpoch.current && scope.cacheKey === stableOwner) setBusy(false) }
  }

  const control = async (action: 'toggle' | 'run' | 'test') => {
    if (!selected || busy) return
    const epoch = actionEpoch.current
    setBusy(true); setError(''); setNotice('')
    try {
      const body = action === 'toggle' ? { enabled: !selected.enabled } : {}
      await gatewayJson(`/api/triggers/${encodeURIComponent(selected.id)}/${action}`, { method: 'POST', body })
      if (epoch !== actionEpoch.current || scope.cacheKey !== stableOwner) return
      setNotice(action === 'toggle' ? 'Native trigger state updated.' : `Native ${action} request accepted.`)
      setRevision(value => value + 1)
    } catch (reason) { if (epoch === actionEpoch.current && scope.cacheKey === stableOwner) setError(`The native ${action} outcome was not confirmed. ${message(reason)}`) }
    finally { if (epoch === actionEpoch.current && scope.cacheKey === stableOwner) setBusy(false) }
  }

  const remove = async () => {
    if (!selected || busy || selected.read_only) return
    const epoch = actionEpoch.current
    setBusy(true); setError('')
    try {
      await gatewayJson(`/api/triggers/${encodeURIComponent(selected.id)}`, { method: 'DELETE' })
      if (epoch !== actionEpoch.current || scope.cacheKey !== stableOwner) return
      navigate(createWorkRoute('triggers', undefined, route.returnTo))
    } catch (reason) { if (epoch === actionEpoch.current && scope.cacheKey === stableOwner) setError(`The trigger was not removed. ${message(reason)}`) }
    finally { if (epoch === actionEpoch.current && scope.cacheKey === stableOwner) setBusy(false) }
  }

  const title = creating ? 'Create an automation' : triggerId ? selected?.name ?? 'Automation' : 'Schedules and triggers'
  const errorState = error && !selected && !creating ? { kind: 'error' as const, message: error, onRetry: () => setRevision(value => value + 1) }
    : !creating && triggerId && !rowsLoaded ? { kind: 'loading' as const, message: 'Checking native trigger and source readiness…' }
      : { kind: 'ready' as const }
  const open = (id: string) => navigate(createWorkRoute('triggers', id, route.returnTo))

  return <WorkspaceFrame route={route} mode="full" title={title} onBack={onBack} state={errorState}
    actions={<div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      {triggerId && <button style={button} type="button" onClick={() => navigate(createWorkRoute('triggers', undefined, route.returnTo))}>Catalogue</button>}
      {!creating && <button style={button} type="button" onClick={() => navigate(createWorkRoute('triggers/new', undefined, route.returnTo))}>New automation</button>}
      <button style={button} type="button" onClick={() => setRevision(value => value + 1)}>Refresh</button>
    </div>}>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {creating && <section aria-label="New trigger" style={{ display: 'grid', gap: 12, maxWidth: 720 }}>
      <label>Automation type<select value={type} disabled={busy} onChange={e => setType(e.target.value as typeof type)} style={input}>
        <option value="schedule">Schedule</option><option value="event">Data event</option><option value="lifecycle">Lifecycle hook</option>
      </select></label>
      <p>{type === 'schedule' ? 'Runs on a clock schedule. Gideon will report the next fire time and actual run history.' : type === 'event' ? 'Runs when a supported native memory, inbox, or app event matches.' : 'Runs on a native lifecycle hook. Dormant or unbound hook readiness stays visible.'}</p>
      <label>Name<input style={input} value={name} disabled={busy} onChange={e => setName(e.target.value)} /></label>
      {type === 'schedule' && <label>Cron schedule<input aria-label="Cron schedule" style={input} value={schedule} disabled={busy} onChange={e => setSchedule(e.target.value)} /></label>}
      {type === 'event' && <label>Native event pattern<select style={input} value={event} disabled={busy} onChange={e => setEvent(e.target.value)}>
        <option>MemoryUpdate</option><option>MemoryKeyPattern</option><option>ContentMatch</option><option>InboxMessage</option><option>InboxSender</option><option>InboxAddress</option><option>AppEvent</option>
      </select></label>}
      {type === 'lifecycle' && <label>Lifecycle event<input aria-label="Lifecycle event" style={input} value={event} disabled={busy} onChange={e => setEvent(e.target.value)} /></label>}
      <p role="status">Gideon checks {type} source readiness when you save; the resulting native status and any unavailable-source reason will appear on the record.</p>
      <button style={button} type="button" disabled={busy || !name.trim()} onClick={() => void submit()}>{busy ? 'Saving…' : 'Save automation'}</button>
    </section>}
    {!creating && !triggerId && <section aria-label="Trigger catalogue"><h2>Schedules and triggers</h2>
      {rows.length ? <ul>{rows.map(row => <li key={row.id}><button style={button} type="button" onClick={() => open(row.id)}>
        {row.name} · {row.kind} · {row.enabled ? 'enabled' : row.state ?? 'disabled'}</button></li>)}</ul> : <p>No native triggers are recorded. Create one to see only readiness and next-run data confirmed by Gideon.</p>}
    </section>}
    {!creating && triggerId && rowsLoaded && !selected && <section aria-label="Missing trigger">
      <h2>Automation not found</h2><p>Gideon returned the native trigger catalogue, but it does not contain <code>{triggerId}</code>.</p>
      <button style={button} type="button" onClick={() => setRevision(value => value + 1)}>Retry native read</button>
      <button style={button} type="button" onClick={() => navigate(createWorkRoute('triggers', undefined, route.returnTo))}>Return to catalogue</button>
    </section>}
    {!creating && triggerId && selected && <div style={{ display: 'grid', gap: 14 }}>
      <section aria-label="Trigger readiness"><h2>Readiness</h2><p>Native ID: <code>{selected.id}</code> · Type: {selected.kind}</p>
        <p>Status: {selected.enabled ? selected.state ?? 'enabled' : 'disabled'} · Health: {selected.health ?? 'not reported'}</p>
        {selected.kind === 'schedule' && <p>Schedule: {selected.schedule ?? selected.cron_expr ?? 'not reported'} · Next event: {selected.next_run_ts ? new Date(selected.next_run_ts * 1000).toLocaleString() : 'not scheduled'}</p>}
        {selected.kind === 'event' && <p>Source: {selected.pattern ?? 'not reported'} · Matches: {selected.event ?? selected.event_glob ?? 'native source readiness not reported'}</p>}
        {selected.kind === 'lifecycle' && <p>Hook: {selected.event ?? 'not reported'} · Enforcement: {selected.enforcement ?? (selected.blocking ? 'blocking' : 'non-blocking')}</p>}
        {selected.author && <p>Author: {selected.author}{selected.read_only ? ' · managed read-only' : ''}</p>}
        {selected.broken?.map(issue => <p key={issue} role="alert">Unavailable: {issue}</p>)}
        {selected.warnings?.map(issue => <p key={issue} role="status">Readiness warning: {issue}</p>)}
      </section>
      <section aria-label="Trigger controls"><h2>Controls</h2>
        <button style={button} disabled={busy || Boolean(selected.read_only)} onClick={() => void control('toggle')}>{selected.enabled ? 'Pause' : 'Enable'}</button>
        {selected.kind !== 'event' && <><button style={button} disabled={busy || Boolean(selected.read_only) || Boolean(selected.broken?.length)} onClick={() => void control('test')}>Test once</button>
          <button style={button} disabled={busy || Boolean(selected.read_only) || Boolean(selected.broken?.length)} onClick={() => void control('run')}>Run now</button></>}
        <button style={button} disabled={busy || Boolean(selected.read_only)} onClick={() => void remove()}>Delete</button>
      </section>
      <section aria-label="Trigger run history"><h2>Run history</h2>
        {historyError ? <><p role="alert">{historyError}</p><button style={button} type="button" onClick={() => setRevision(value => value + 1)}>Retry run history</button></>
          : !history ? <p role="status">Loading native run history…</p>
          : history.supported === false ? <p>This native trigger type does not expose run history.</p>
          : history?.runs?.length || history?.entries?.length ? <ol>{(history.runs ?? history.entries ?? []).map((run, index) => <li key={String(run.id ?? run.run_id ?? index)}>{String(run.status ?? run.outcome ?? 'Recorded')} · <code>{String(run.id ?? run.run_id ?? '')}</code></li>)}</ol>
            : <p>No native run history is recorded.</p>}
      </section>
    </div>}
  </WorkspaceFrame>
}

function message(error: unknown): string { return error instanceof Error && error.message ? error.message : 'Native trigger data is unavailable.' }
