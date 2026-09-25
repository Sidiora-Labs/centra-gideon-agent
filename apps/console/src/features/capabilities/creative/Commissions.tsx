import { useEffect, useState } from 'react'

type Output = { artifact_id: string; artifact_version: number; content_hash: string; path?: string }
type Run = { id: string; status: string; project_id?: string; outputs: Output[]; attempts: Array<{ number: number; status: string; error: string }> }
type Reaction = { id: string; revision: number; author: string; rating: string; deleted: boolean }
type Commission = { id: string; revision: number; name: string; target_ability: string; enabled: boolean; schedule_error: string; runs?: Run[]; feedback?: Reaction[] }

const api = '/api/capabilities/creative/commissions'

export function Commissions({ apiRoot = api }: { apiRoot?: string }) {
  const [items, setItems] = useState<Commission[]>([])
  const [selected, setSelected] = useState<Commission | null>(null)
  const [name, setName] = useState('Weekly treatment')
  const [intent, setIntent] = useState('Create a focused treatment from the selected manuscript.')
  const [ability, setAbility] = useState('series')
  const [sourceId, setSourceId] = useState('')
  const [sourceRevision, setSourceRevision] = useState(1)
  const [error, setError] = useState('')

  async function list() {
    const data = await fetch(apiRoot).then(response => response.json())
    setItems(data.items || [])
  }
  async function load(id: string) {
    const data = await fetch(`${apiRoot}/${id}`).then(response => response.json())
    setSelected(data); await list()
  }
  async function create() {
    setError('')
    const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      request_id: `commission-${Date.now()}`, name, target_ability: ability,
      brief: { intent, genre: '', category: '', style: '', constraints: {}, seed_refs: [] },
      cadence: { kind: 'interval', seconds: 900, timezone: 'UTC' },
      sources: [{ kind: 'work', id: sourceId, revision: sourceRevision }], enabled: true, max_attempts: 2,
      steps: [
        { id: 'verify', title: 'Verify canonical source', operation: 'source.verify', depends_on: [] },
        { id: 'snapshot', title: 'Snapshot treatment', operation: 'treatment.snapshot', depends_on: ['verify'] },
      ],
    }) })
    const data = await response.json()
    if (!response.ok) return setError(data.error || 'Unable to create commission')
    await load(data.id)
  }
  async function update(enabled: boolean) {
    if (!selected) return
    const response = await fetch(`${apiRoot}/${selected.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ revision: selected.revision, enabled }) })
    const data = await response.json()
    if (!response.ok) return setError(data.error || 'Unable to update commission')
    await load(data.id)
  }
  async function runNow() {
    if (!selected) return
    const data = await fetch(`${apiRoot}/${selected.id}/run`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: `manual-${Date.now()}` }) }).then(response => response.json())
    if (data.error) return setError(data.error)
    await load(selected.id)
  }
  async function react(run: Run, output: Output, rating: string) {
    if (!selected) return
    const response = await fetch(`${apiRoot}/${selected.id}/feedback`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      run_id: run.id, author: 'dashboard-owner', output: { artifact_id: output.artifact_id, artifact_version: output.artifact_version, content_hash: output.content_hash },
      rating, note: rating === 'liked' ? 'Keep this direction.' : 'Change this direction.', tags: ['dashboard'],
    }) })
    const data = await response.json()
    if (!response.ok) return setError(data.error || 'Unable to save feedback')
    await load(selected.id)
  }

  useEffect(() => { void list() }, [])
  return <main>
    <h1>Recurring creative commissions</h1>
    <p>Schedule a typed standing brief. Each occurrence creates one attributable direction project and retains its attempts, outputs, and bounded feedback context.</p>
    <section aria-label="Create commission">
      <label>Name<input aria-label="Commission name" value={name} onChange={event => setName(event.target.value)} /></label>
      <label>Intent<textarea aria-label="Commission intent" value={intent} onChange={event => setIntent(event.target.value)} /></label>
      <label>Ability<select aria-label="Target ability" value={ability} onChange={event => setAbility(event.target.value)}>
        {['video', 'image', 'music', 'music-video', 'series'].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <label>Source work ID<input aria-label="Source work ID" value={sourceId} onChange={event => setSourceId(event.target.value)} /></label>
      <label>Source revision<input aria-label="Source revision" type="number" value={sourceRevision} onChange={event => setSourceRevision(Number(event.target.value))} /></label>
      <button onClick={() => void create()}>Create commission</button>
    </section>
    {error && <p role="alert">{error}</p>}
    <ul aria-label="Commission list">{items.map(item => <li key={item.id}>
      <button onClick={() => void load(item.id)}>{item.name}</button> · {item.enabled ? 'scheduled' : 'disabled'}
    </li>)}</ul>
    {selected && <section aria-label="Commission detail">
      <h2>{selected.name}</h2>
      <p>{selected.target_ability} · {selected.enabled ? 'scheduled' : 'disabled'} {selected.schedule_error && `· ${selected.schedule_error}`}</p>
      <button onClick={() => void update(!selected.enabled)}>{selected.enabled ? 'Disable' : 'Enable'}</button>
      <button onClick={() => void runNow()}>Run now</button>
      <h3>Run history</h3>
      <ul>{(selected.runs || []).map(run => <li key={run.id}>
        <strong>{run.status}</strong> · project {run.project_id || 'not created'} · {run.attempts.length} attempt(s)
        {run.outputs.map(output => <div key={output.artifact_id}>Output {output.artifact_id} v{output.artifact_version}
          <button onClick={() => void react(run, output, 'liked')}>Like</button>
          <button onClick={() => void react(run, output, 'disliked')}>Dislike</button>
        </div>)}
      </li>)}</ul>
      <h3>Feedback</h3><ul>{(selected.feedback || []).map(row => <li key={row.id}>{row.author}: {row.rating}{row.deleted ? ' (removed)' : ''}</li>)}</ul>
    </section>}
  </main>
}

export default Commissions
