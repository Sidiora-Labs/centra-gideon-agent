import { Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { TopBar } from '../../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../../shared/ui/WorkbenchLayout'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Goal = { id: string; revision: number; title: string; description: string; status: string; target_date: string | null }
type Session = { id: string; revision: number; goal_id: string; title: string; start_at: string; end_at: string; notes: string; status: string }
export default function LifeGoalsPage({ endpoint = '/api/capabilities/identity/goals' }: { endpoint?: string }) {
  const [goals, setGoals] = useState<Goal[]>([])
  const [sessions, setSessions] = useState<Session[]>([])
  const [goal, setGoal] = useState<Partial<Goal>>({ title: '', description: '', status: 'active', target_date: null })
  const [session, setSession] = useState<Partial<Session>>({ title: '', start_at: '', end_at: '', notes: '', status: 'scheduled' })
  const [request, setRequest] = useState(() => crypto.randomUUID())
  const [sessionRequest, setSessionRequest] = useState(() => crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [calendar, setCalendar] = useState('')
  const call = async <T,>(path: string, body?: unknown): Promise<T> => readJson<T>(await fetch(endpoint + path, { method: body ? 'POST' : 'GET', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }))
  const choose = (item: Goal) => { setGoal(item); window.location.hash = '#/capabilities/identity/goals?goal=' + item.id }
  const load = async () => {
    const [nextGoals, nextSessions] = await Promise.all([call<Goal[]>('/goals'), call<Session[]>('/sessions')])
    setGoals(nextGoals); setSessions(nextSessions); setLoaded(true)
    const id = new URLSearchParams(window.location.hash.split('?')[1] || '').get('goal')
    const selected = nextGoals.find(row => row.id === id)
    if (selected) choose(selected)
  }
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform(load) }, [endpoint])
  const freshSession = () => { setSession({ title: '', start_at: '', end_at: '', notes: '', status: 'scheduled' }); setSessionRequest(crypto.randomUUID()) }
  return <WorkbenchLayout topBar={<TopBar keepCornerPadding left={<PageTitle>Life goals and planned sessions</PageTitle>} />}>
  <section className="mx-auto flex w-full max-w-[72rem] flex-col gap-l px-l py-2xl text-on-surface"><p>Plan human activities. Calendar export contains recorded plans; it does not send invitations or synchronize an external calendar.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!loaded && <p role="status">Loading plans…</p>}
    <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void perform(load)}>Reload plans</Button>
    <div className="grid md:grid-cols-2 gap-4"><section className="space-y-m rounded-lg bg-surface-container p-l"><h2 data-type="title-l">Goals</h2>
      {loaded && !goals.length && <p>No life goals yet.</p>}{goals.map(row => <button key={row.id} className="block underline" onClick={() => { choose(row); freshSession() }}>{row.title} · {row.status}</button>)}
      <Button variant="secondary" onClick={() => { setGoal({ title: '', description: '', status: 'active', target_date: null }); setRequest(crypto.randomUUID()); freshSession(); window.location.hash = '#/capabilities/identity/goals' }}>New goal</Button>
      <form className="space-y-m rounded-lg bg-surface-container p-l" onSubmit={e => { e.preventDefault(); void perform(async () => {
        const saved = await call<Goal>('/goals', { title: goal.title, description: goal.description, status: goal.status, target_date: goal.target_date || null, ...(goal.id ? { id: goal.id } : {}), expected_revision: goal.revision || 0, request_id: request })
        setRequest(crypto.randomUUID()); choose(saved); await load()
      }) }}>
        <label className="block" htmlFor="goal-title">Goal title</label><TextInput className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="goal-title" required maxLength={200} value={goal.title ?? ''} onChange={nextValue => setGoal({ ...goal, title: nextValue })} />
        <label className="block" htmlFor="goal-description">Why this matters</label><TextArea className="w-full min-w-0 resize-y rounded-md border border-outline-variant/30 bg-surface-container p-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="goal-description" value={goal.description ?? ''} onChange={nextValue => setGoal({ ...goal, description: nextValue })} />
        <label className="block" htmlFor="goal-date">Target date</label><TextInput id="goal-date" type="date" value={String(goal.target_date || '')} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" onChange={nextValue => setGoal({ ...goal, target_date: nextValue || null })} />
        <label className="block" htmlFor="goal-status">Goal status</label><Select id="goal-status" value={goal.status ?? 'active'} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary appearance-none" onChange={nextValue => setGoal({ ...goal, status: nextValue })} options={['active', 'completed', 'archived'].map(value => ({ value, label: value }))} />
        <Button type="submit" disabled={busy} disabledReason={busy ? BUSY_REASON : undefined}>Save goal</Button>
      </form></section>
      <section className="space-y-m rounded-lg bg-surface-container p-l"><h2 data-type="title-l">Sessions for selected goal</h2>{!goal.id && <p>Save or select a goal to plan a session.</p>}
        {sessions.filter(row => row.goal_id === goal.id).map(row => <button key={row.id} className="block underline" onClick={() => { setSession(row); setSessionRequest(crypto.randomUUID()) }}>{row.title} · {row.status} · {new Date(row.start_at).toLocaleString()}</button>)}
        <Button variant="secondary" disabled={!goal.id} disabledReason={!goal.id ? 'Save this goal before starting a new session' : undefined} onClick={freshSession}>New session</Button>
        <form className="space-y-m rounded-lg bg-surface-container p-l" onSubmit={e => { e.preventDefault(); void perform(async () => {
          const saved = await call<Session>('/sessions', { goal_id: goal.id, title: session.title, start_at: session.start_at, end_at: session.end_at, notes: session.notes, status: session.status, ...(session.id ? { id: session.id } : {}), expected_revision: session.revision || 0, request_id: sessionRequest })
          setSession(saved); setSessionRequest(crypto.randomUUID()); await load()
        }) }}>
          <label className="block" htmlFor="plan-title">Session title</label><TextInput className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="plan-title" required value={session.title ?? ''} onChange={nextValue => setSession({ ...session, title: nextValue })} />
          <p>Enter ISO times with timezone, for example 2026-10-01T10:00:00+02:00.</p>
          <label className="block" htmlFor="plan-start">Start with timezone</label><TextInput className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="plan-start" required value={session.start_at ?? ''} onChange={nextValue => setSession({ ...session, start_at: nextValue })} />
          <label className="block" htmlFor="plan-end">End with timezone</label><TextInput className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="plan-end" required value={session.end_at ?? ''} onChange={nextValue => setSession({ ...session, end_at: nextValue })} />
          <label className="block" htmlFor="plan-notes">Session notes</label><TextArea className="w-full min-w-0 resize-y rounded-md border border-outline-variant/30 bg-surface-container p-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" id="plan-notes" value={session.notes ?? ''} onChange={nextValue => setSession({ ...session, notes: nextValue })} />
          <label className="block" htmlFor="plan-status">Session status</label><Select id="plan-status" value={session.status ?? 'scheduled'} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary appearance-none" onChange={nextValue => setSession({ ...session, status: nextValue })} options={['scheduled', 'completed', 'cancelled'].map(value => ({ value, label: value }))} />
          <Button type="submit" disabled={busy || !goal.id} disabledReason={busy ? BUSY_REASON : undefined}>Save session</Button>
        </form></section></div>
    <Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void perform(async () => { const response = await fetch(endpoint + '/calendar', { headers: gatewayHeaders }); if (!response.ok) throw new Error('Calendar export unavailable'); setCalendar(await response.text()) })}>Export calendar</Button>
    {calendar && <section aria-label="Calendar export"><a download="human-plans.ics" href={'data:text/calendar;charset=utf-8,' + encodeURIComponent(calendar)}>Download calendar file</a><pre className="overflow-auto whitespace-pre-wrap">{calendar}</pre></section>}
  </section></WorkbenchLayout>
}
