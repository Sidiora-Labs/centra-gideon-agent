import { useEffect, useState } from 'react'

type Output = { artifact_id: string; artifact_version: number; content_hash: string; path?: string }
type Receipt = { backend: string; operation: string; request_id: string; resource_id: string; status: string; upstream_status: string; error_code: string }
type Run = { id: string; status: string; project_id?: string; outputs: Output[]; dispatch_receipts?: Receipt[]; attempts: Array<{ number: number; status: string; error: string }> }
type Reaction = { id: string; revision: number; author: string; rating: string; deleted: boolean }
type Commission = { id: string; revision: number; name: string; target_ability: string; mode: string; mode_source?: string; enabled: boolean; schedule_error: string; schedule_state?: string; next_fire_at?: string; runs?: Run[]; feedback?: Reaction[] }

const api = '/api/capabilities/creative/commissions'

export function Commissions({ apiRoot = api }: { apiRoot?: string }) {
  const [items, setItems] = useState<Commission[]>([])
  const [selected, setSelected] = useState<Commission | null>(null)
  const [name, setName] = useState('Weekly treatment')
  const [intent, setIntent] = useState('Create a focused treatment from the selected manuscript.')
  const [ability, setAbility] = useState('series')
  const [mode, setMode] = useState('planning')
  const [sourceKind, setSourceKind] = useState('work')
  const [sourceId, setSourceId] = useState('')
  const [sourceRevision, setSourceRevision] = useState(1)
  const [cadenceKind, setCadenceKind] = useState('interval')
  const [recurrenceStart, setRecurrenceStart] = useState('2026-10-01T09:00:00')
  const [recurrenceRule, setRecurrenceRule] = useState('FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1')
  const [mediaPrompt, setMediaPrompt] = useState('A focused visual interpretation of the approved direction.')
  const [imageSize, setImageSize] = useState('')
  const [videoDuration, setVideoDuration] = useState(5)
  const [videoAspect, setVideoAspect] = useState('')
  const [trackId, setTrackId] = useState('')
  const [trackRevision, setTrackRevision] = useState(1)
  const [musicLength, setMusicLength] = useState(30000)
  const [musicLicense, setMusicLicense] = useState('Use is subject to the configured provider terms.')
  const [projectId, setProjectId] = useState('')
  const [projectRevision, setProjectRevision] = useState(1)
  const [seriesMode, setSeriesMode] = useState('model')
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
    const dispatch = mode === 'planning' ? undefined : ability === 'image'
      ? { input: { prompt: mediaPrompt, size: imageSize, controls: {}, loras: [] } }
      : ability === 'video' ? { input: { prompt: mediaPrompt, duration_seconds: videoDuration, aspect_ratio: videoAspect, controls: {} } }
      : ability === 'music' ? { track_id: trackId, track_revision: trackRevision, prompt: mediaPrompt, music_length_ms: musicLength, force_instrumental: true, license: musicLicense }
      : ability === 'music-video' ? { project_id: projectId, revision: projectRevision }
      : { series_id: sourceId, series_revision: sourceRevision, mode: seriesMode, max_attempts: 2 }
    const response = await fetch(apiRoot, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      request_id: `commission-${Date.now()}`, name, target_ability: ability, mode,
      brief: { intent, genre: '', category: '', style: '', constraints: {}, seed_refs: [] },
      cadence: cadenceKind === 'recurrence'
        ? { kind: 'recurrence', dtstart: recurrenceStart, rrule: recurrenceRule, timezone: 'UTC', exdates: [] }
        : { kind: 'interval', seconds: 900, timezone: 'UTC' },
      ...(dispatch ? { dispatch } : {}),
      sources: [{ kind: sourceKind, id: sourceId, revision: sourceRevision }], enabled: true, max_attempts: 2,
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
      <label>Execution mode<select aria-label="Execution mode" value={mode} onChange={event => setMode(event.target.value)}><option value="planning">Planning only</option><option value="generate">Generate through configured service</option></select></label>
      <label>Source kind<select aria-label="Source kind" value={sourceKind} onChange={event => setSourceKind(event.target.value)}><option value="work">Work</option><option value="series">Series</option></select></label>
      <label>Source ID<input aria-label="Source work ID" value={sourceId} onChange={event => setSourceId(event.target.value)} /></label>
      <label>Source revision<input aria-label="Source revision" type="number" value={sourceRevision} onChange={event => setSourceRevision(Number(event.target.value))} /></label>
      <label>Cadence<select aria-label="Cadence kind" value={cadenceKind} onChange={event => setCadenceKind(event.target.value)}>
        <option value="interval">Every 15 minutes</option><option value="recurrence">Calendar recurrence</option>
      </select></label>
      {cadenceKind === 'recurrence' && <>
        <label>First local occurrence<input aria-label="Recurrence start" value={recurrenceStart} onChange={event => setRecurrenceStart(event.target.value)} /></label>
        <label>Recurrence rule<input aria-label="Recurrence rule" value={recurrenceRule} onChange={event => setRecurrenceRule(event.target.value)} /></label>
      </>}
      {mode === 'generate' && (ability === 'image' || ability === 'video' || ability === 'music') && <label>Generation prompt<textarea aria-label="Generation prompt" value={mediaPrompt} onChange={event => setMediaPrompt(event.target.value)} /></label>}
      {mode === 'generate' && ability === 'image' && <label>Image size<input aria-label="Image size" value={imageSize} onChange={event => setImageSize(event.target.value)} /></label>}
      {mode === 'generate' && ability === 'video' && <><label>Duration seconds<input aria-label="Duration seconds" type="number" value={videoDuration} onChange={event => setVideoDuration(Number(event.target.value))} /></label><label>Aspect ratio<input aria-label="Aspect ratio" value={videoAspect} onChange={event => setVideoAspect(event.target.value)} /></label></>}
      {mode === 'generate' && ability === 'music' && <><label>Track ID<input aria-label="Track ID" value={trackId} onChange={event => setTrackId(event.target.value)} /></label><label>Track revision<input aria-label="Track revision" type="number" value={trackRevision} onChange={event => setTrackRevision(Number(event.target.value))} /></label><label>Length milliseconds<input aria-label="Length milliseconds" type="number" value={musicLength} onChange={event => setMusicLength(Number(event.target.value))} /></label><label>License statement<input aria-label="License statement" value={musicLicense} onChange={event => setMusicLicense(event.target.value)} /></label></>}
      {mode === 'generate' && ability === 'music-video' && <><label>Music video project ID<input aria-label="Music video project ID" value={projectId} onChange={event => setProjectId(event.target.value)} /></label><label>Project revision<input aria-label="Project revision" type="number" value={projectRevision} onChange={event => setProjectRevision(Number(event.target.value))} /></label></>}
      {mode === 'generate' && ability === 'series' && <label>Series production mode<select aria-label="Series production mode" value={seriesMode} onChange={event => setSeriesMode(event.target.value)}><option value="model">Configured model</option><option value="authored">Authored approval</option></select></label>}
      <button onClick={() => void create()}>Create commission</button>
    </section>
    {error && <p role="alert">{error}</p>}
    <ul aria-label="Commission list">{items.map(item => <li key={item.id}>
      <button onClick={() => void load(item.id)}>{item.name}</button> · {item.enabled ? 'scheduled' : 'disabled'}
    </li>)}</ul>
    {selected && <section aria-label="Commission detail">
      <h2>{selected.name}</h2>
      <p>{selected.target_ability} · {selected.mode === 'planning' ? 'planning only' : 'generation'}{selected.mode_source === 'legacy' ? ' (legacy)' : ''} · {selected.schedule_state || (selected.enabled ? 'scheduled' : 'disabled')} {selected.next_fire_at && `· next ${selected.next_fire_at}`} {selected.schedule_error && `· ${selected.schedule_error}`}</p>
      <button onClick={() => void update(!selected.enabled)}>{selected.enabled ? 'Disable' : 'Enable'}</button>
      <button onClick={() => void runNow()}>Run now</button>
      <h3>Run history</h3>
      <ul>{(selected.runs || []).map(run => <li key={run.id}>
        <strong>{run.status}</strong> · project {run.project_id || 'not created'} · {run.attempts.length} attempt(s)
        {run.outputs.map(output => <div key={output.artifact_id}>Output {output.artifact_id} v{output.artifact_version}
          <button onClick={() => void react(run, output, 'liked')}>Like</button>
          <button onClick={() => void react(run, output, 'disliked')}>Dislike</button>
        </div>)}
        {(run.dispatch_receipts || []).map(receipt => <div key={receipt.request_id}>Dispatch {receipt.backend}/{receipt.operation}: {receipt.status} {receipt.resource_id || receipt.error_code}</div>)}
      </li>)}</ul>
      <h3>Feedback</h3><ul>{(selected.feedback || []).map(row => <li key={row.id}>{row.author}: {row.rating}{row.deleted ? ' (removed)' : ''}</li>)}</ul>
    </section>}
  </main>
}

export default Commissions
