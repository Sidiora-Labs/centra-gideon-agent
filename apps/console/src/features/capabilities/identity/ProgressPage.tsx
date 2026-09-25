import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Profile = { revision: number; birth_date: string | null; timezone: string; tracked_task_ids: string[] }
type Sheet = { profile: Profile; as_of: string; age: number | null; goals: Record<string, number>; sessions_completed: { id: string; title: string; local_date: string; planned_minutes: number; goal_id: string }[]; planned_completed_minutes: number; authored_story_count: number; tasks: { id: string; title: string; status: string }[]; tasks_done: number; missing_task_ids: string[]; invalid_task_ids: string[]; source_policy: string }
export default function ProgressPage({ endpoint = '/api/capabilities/identity/progress' }: { endpoint?: string }) {
  const [sheet, setSheet] = useState<Sheet | null>(null)
  const [profile, setProfile] = useState<Profile>({ revision: 0, birth_date: null, timezone: 'UTC', tracked_task_ids: [] })
  const [taskIds, setTaskIds] = useState('')
  const [asOf, setAsOf] = useState(new URLSearchParams(window.location.hash.split('?')[1] || '').get('as_of') || '')
  const [request, setRequest] = useState(() => crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async () => { const next = await readJson<Sheet>(await fetch(endpoint + (asOf ? '?as_of=' + encodeURIComponent(asOf) : ''), { headers: gatewayHeaders })); setSheet(next); setProfile(next.profile); setTaskIds(next.profile.tracked_task_ids.join('\n')) }
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform(load) }, [endpoint])
  return <section className="p-4 max-w-4xl mx-auto space-y-4 text-on-surface"><h1 className="text-2xl">Human progress</h1>
    <p>Source-backed progress. Planned session time is not measured effort. Health and skill level are unknown.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!sheet && <p role="status">Loading progress…</p>}
    <label htmlFor="progress-asof">As of local date</label><input id="progress-asof" type="date" value={asOf} onChange={e => setAsOf(e.target.value)} />
    <Button disabled={busy} onClick={() => { window.location.hash = '#/capabilities/identity/progress' + (asOf ? '?as_of=' + asOf : ''); void perform(load) }}>Reload progress</Button>
    <form className="space-y-2" onSubmit={e => { e.preventDefault(); void perform(async () => {
      await readJson<Profile>(await fetch(endpoint, { method: 'PUT', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, body: JSON.stringify({ birth_date: profile.birth_date, timezone: profile.timezone, tracked_task_ids: taskIds.split('\n').map(x => x.trim()).filter(Boolean), expected_revision: profile.revision, request_id: request }) }))
      setRequest(crypto.randomUUID()); await load()
    }) }}>
      <label className="block" htmlFor="progress-birth">Birth date (optional)</label><input id="progress-birth" type="date" value={profile.birth_date || ''} onChange={e => setProfile({ ...profile, birth_date: e.target.value || null })} />
      <label className="block" htmlFor="progress-zone">Timezone</label><input className="bg-surface-high p-2" id="progress-zone" value={profile.timezone} onChange={e => setProfile({ ...profile, timezone: e.target.value })} required />
      <label className="block" htmlFor="progress-tasks">Tracked native task IDs (one per line)</label><textarea className="w-full bg-surface-high p-2" id="progress-tasks" value={taskIds} onChange={e => setTaskIds(e.target.value)} />
      <Button type="submit" disabled={busy || !sheet}>Save progress settings</Button>
    </form>
    {sheet && <><section aria-label="Progress summary"><h2>Current source summary</h2><p>Age: {sheet.age === null ? 'Unknown' : sheet.age}</p><p>Completed goals: {sheet.goals.completed} · Active goals: {sheet.goals.active}</p><p>Completed sessions: {sheet.sessions_completed.length}</p><p>Planned minutes in completed sessions: {sheet.planned_completed_minutes}</p><p>Authored stories: {sheet.authored_story_count}</p><p>Tracked tasks currently done: {sheet.tasks_done}</p><p>{sheet.source_policy}</p></section>
      <section><h2>Completed sessions</h2>{!sheet.sessions_completed.length && <p>No completed sessions recorded.</p>}{sheet.sessions_completed.map(row => <p key={row.id}><a className="underline" href={'#/capabilities/identity/goals?goal=' + row.goal_id}>{row.title}</a> · {row.local_date} · {row.planned_minutes} planned minutes</p>)}</section>
      <section><h2>Tracked native task outcomes</h2>{!sheet.tasks.length && <p>No tracked task outcomes available.</p>}{sheet.tasks.map(row => <p key={row.id}>{row.title} · {row.status} · {row.id}</p>)}{sheet.missing_task_ids.map(id => <p key={id}>Missing source: {id}</p>)}{sheet.invalid_task_ids.map(id => <p key={id}>Unreadable source: {id}</p>)}</section></>}
  </section>
}
