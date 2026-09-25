import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
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
  return <section className="p-4 max-w-5xl mx-auto space-y-4 text-on-surface">
    <h1 className="text-2xl">Life goals and planned sessions</h1><p>Plan human activities. Calendar export contains recorded plans; it does not send invitations or synchronize an external calendar.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!loaded && <p role="status">Loading plans…</p>}
    <Button disabled={busy} onClick={() => void perform(load)}>Reload plans</Button>
    <div className="grid md:grid-cols-2 gap-4"><section className="space-y-2"><h2>Goals</h2>
      {loaded && !goals.length && <p>No life goals yet.</p>}{goals.map(row => <button key={row.id} className="block underline" onClick={() => { choose(row); freshSession() }}>{row.title} · {row.status}</button>)}
      <Button variant="secondary" onClick={() => { setGoal({ title: '', description: '', status: 'active', target_date: null }); setRequest(crypto.randomUUID()); freshSession(); window.location.hash = '#/capabilities/identity/goals' }}>New goal</Button>
      <form className="space-y-2" onSubmit={e => { e.preventDefault(); void perform(async () => {
        const saved = await call<Goal>('/goals', { title: goal.title, description: goal.description, status: goal.status, target_date: goal.target_date || null, ...(goal.id ? { id: goal.id } : {}), expected_revision: goal.revision || 0, request_id: request })
        setRequest(crypto.randomUUID()); choose(saved); await load()
      }) }}>
        <label className="block" htmlFor="goal-title">Goal title</label><input className="w-full bg-surface-high p-2" id="goal-title" required maxLength={200} value={goal.title} onChange={e => setGoal({ ...goal, title: e.target.value })} />
        <label className="block" htmlFor="goal-description">Why this matters</label><textarea className="w-full bg-surface-high p-2" id="goal-description" value={goal.description} onChange={e => setGoal({ ...goal, description: e.target.value })} />
        <label className="block" htmlFor="goal-date">Target date</label><input id="goal-date" type="date" value={goal.target_date || ''} onChange={e => setGoal({ ...goal, target_date: e.target.value || null })} />
        <label className="block" htmlFor="goal-status">Goal status</label><select id="goal-status" value={goal.status} onChange={e => setGoal({ ...goal, status: e.target.value })}>{['active', 'completed', 'archived'].map(value => <option key={value}>{value}</option>)}</select>
        <Button type="submit" disabled={busy}>Save goal</Button>
      </form></section>
      <section className="space-y-2"><h2>Sessions for selected goal</h2>{!goal.id && <p>Save or select a goal to plan a session.</p>}
        {sessions.filter(row => row.goal_id === goal.id).map(row => <button key={row.id} className="block underline" onClick={() => { setSession(row); setSessionRequest(crypto.randomUUID()) }}>{row.title} · {row.status} · {new Date(row.start_at).toLocaleString()}</button>)}
        <Button variant="secondary" disabled={!goal.id} onClick={freshSession}>New session</Button>
        <form className="space-y-2" onSubmit={e => { e.preventDefault(); void perform(async () => {
          const saved = await call<Session>('/sessions', { goal_id: goal.id, title: session.title, start_at: session.start_at, end_at: session.end_at, notes: session.notes, status: session.status, ...(session.id ? { id: session.id } : {}), expected_revision: session.revision || 0, request_id: sessionRequest })
          setSession(saved); setSessionRequest(crypto.randomUUID()); await load()
        }) }}>
          <label className="block" htmlFor="plan-title">Session title</label><input className="w-full bg-surface-high p-2" id="plan-title" required value={session.title} onChange={e => setSession({ ...session, title: e.target.value })} />
          <p>Enter ISO times with timezone, for example 2026-10-01T10:00:00+02:00.</p>
          <label className="block" htmlFor="plan-start">Start with timezone</label><input className="w-full bg-surface-high p-2" id="plan-start" required value={session.start_at} onChange={e => setSession({ ...session, start_at: e.target.value })} />
          <label className="block" htmlFor="plan-end">End with timezone</label><input className="w-full bg-surface-high p-2" id="plan-end" required value={session.end_at} onChange={e => setSession({ ...session, end_at: e.target.value })} />
          <label className="block" htmlFor="plan-notes">Session notes</label><textarea className="w-full bg-surface-high p-2" id="plan-notes" value={session.notes} onChange={e => setSession({ ...session, notes: e.target.value })} />
          <label className="block" htmlFor="plan-status">Session status</label><select id="plan-status" value={session.status} onChange={e => setSession({ ...session, status: e.target.value })}>{['scheduled', 'completed', 'cancelled'].map(value => <option key={value}>{value}</option>)}</select>
          <Button type="submit" disabled={busy || !goal.id}>Save session</Button>
        </form></section></div>
    <Button disabled={busy} onClick={() => void perform(async () => { const response = await fetch(endpoint + '/calendar', { headers: gatewayHeaders }); if (!response.ok) throw new Error('Calendar export unavailable'); setCalendar(await response.text()) })}>Export calendar</Button>
    {calendar && <section aria-label="Calendar export"><a download="human-plans.ics" href={'data:text/calendar;charset=utf-8,' + encodeURIComponent(calendar)}>Download calendar file</a><pre className="overflow-auto whitespace-pre-wrap">{calendar}</pre></section>}
  </section>
}
