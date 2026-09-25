import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListRow, ListScaffold } from '../../../shared/ui/ListScaffold'
import { Clock3, Plus, Sparkles, Zap } from 'lucide-react'
import { Checkbox, Field, NumberField, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

type Output = { artifact_id: string; artifact_version: number; content_hash: string; path?: string }
type Receipt = { backend: string; operation: string; request_id: string; resource_id: string; status: string; upstream_status: string; error_code: string }
type Run = { id: string; status: string; project_id?: string; outputs: Output[]; dispatch_receipts?: Receipt[]; attempts: Array<{ number: number; status: string; error: string }> }
type Reaction = { id: string; revision: number; author: string; rating: string; deleted: boolean }
type Commission = { id: string; revision: number; name: string; target_ability: string; mode: string; mode_source: string; enabled: boolean; schedule_error: string; schedule_state?: string; next_fire_at?: string; runs?: Run[]; feedback?: Reaction[] }
type Peer = { id: string; label: string }

export function Commissions({ apiRoot = '/api/capabilities/creative/commissions' }: { apiRoot?: string }) {
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
  const [license, setLicense] = useState('Use is subject to the configured provider terms.')
  const [projectId, setProjectId] = useState('')
  const [projectRevision, setProjectRevision] = useState(1)
  const [seriesMode, setSeriesMode] = useState('model')
  const [error, setError] = useState('')
  const [peers, setPeers] = useState<Peer[]>([])
  const [peerId, setPeerId] = useState('')
  const [approved, setApproved] = useState<Record<string, boolean>>({})
  const [deliveries, setDeliveries] = useState<Record<string, string>>({})
  const [creating, setCreating] = useState(true)

  async function list() {
    const data = await fetch(apiRoot).then(response => response.json())
    setItems(data.items || [])
  }
  async function listPeers() {
    const data = await fetch(`${apiRoot}/peer-feedback/peers`).then(response => response.json())
    const available = data.items || []
    setPeers(available); setPeerId(current => current || available[0]?.id || '')
  }
  async function load(id: string) {
    const data = await fetch(`${apiRoot}/${id}`).then(response => response.json())
    setSelected(data); setCreating(false); await list()
  }
  async function create() {
    setError('')
    const dispatch = mode === 'planning' ? undefined
      : ability === 'image' ? { input: { prompt: mediaPrompt, size: imageSize, controls: {}, loras: [] } }
      : ability === 'video' ? { input: { prompt: mediaPrompt, duration_seconds: videoDuration, aspect_ratio: videoAspect, controls: {} } }
      : ability === 'music' ? { track_id: trackId, track_revision: trackRevision, prompt: mediaPrompt,
          music_length_ms: musicLength, force_instrumental: true, license }
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
  async function deliver(row: Reaction) {
    if (!selected || !peerId || !approved[row.id]) return
    setError('')
    const response = await fetch(`${apiRoot}/${selected.id}/feedback/${row.id}/deliver`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
        peer_id: peerId,
        approval: { decision: 'approved', commission_id: selected.id, peer_id: peerId,
          reaction_id: row.id, reaction_revision: row.revision },
      }),
    })
    const data = await response.json()
    if (!response.ok) return setError(data.error || 'Unable to deliver feedback')
    setApproved(current => ({ ...current, [row.id]: false }))
    setDeliveries(current => ({ ...current, [row.id]: `${data.receipt.state} · revision ${data.reaction_revision}` }))
  }

  useEffect(() => { void list(); void listPeers() }, [apiRoot])
  return <ListScaffold title="Commissions" right={<Button onClick={() => { setSelected(null); setCreating(true) }}><Plus size={16} aria-hidden/>New commission</Button>}>
    <p data-type="body-m" className="mb-xl max-w-[48rem] text-on-surface-low">Schedule a typed standing brief. Each occurrence creates one attributable direction project and retains its attempts, outputs, and bounded feedback context.</p>
    {error && <p role="alert" className="mb-m border-l-2 border-danger/40 pl-s text-danger">{error}</p>}
    <Surface className="grid min-h-[34rem] lg:grid-cols-[19rem_minmax(0,1fr)]"><aside className="border-b border-outline-variant/20 p-l lg:border-b-0 lg:border-r"><h2 data-type="label-l" className="mb-m text-on-surface-low">Standing briefs</h2>{items.length ? <div className="space-y-s">{items.map((item, index) => <ListRow key={item.id} index={index} onClick={() => void load(item.id)} label={item.name} accent={item.id === selected?.id ? 'var(--color-primary)' : undefined}><Clock3 size={17} className="shrink-0 text-on-surface-low" aria-hidden/><div className="min-w-0 flex-1"><p data-type="label-m" className="truncate">{item.name}</p><p data-type="caption" className="truncate text-on-surface-low">{item.mode === 'generate' ? 'generation' : 'planning'} · {item.enabled ? 'scheduled' : 'disabled'}</p></div></ListRow>)}</div> : <EmptyState icon={Clock3} title="No commissions yet" hint="Create a standing brief to keep recurring creative work attributable." action={{ label: 'New commission', onClick: () => setCreating(true), icon: Plus }}/>}</aside>
    <div className="min-w-0 p-l lg:p-2xl">{creating ? <section aria-label="Create commission" className="mx-auto max-w-[48rem] space-y-l"><div><h2 data-type="title-s">New commission</h2><p data-type="body-s" className="text-on-surface-low">Define the source, cadence, and approved output path before scheduling work.</p></div>
      <Field label="Name"><TextInput ariaLabel="Commission name" value={name} onChange={setName} /></Field>
      <Field label="Intent"><TextArea ariaLabel="Commission intent" value={intent} onChange={setIntent} /></Field>
      <Field label="Ability"><Select ariaLabel="Target ability" value={ability} onChange={setAbility} options={['video', 'image', 'music', 'music-video', 'series'].map(value => ({ value, label: value }))} /></Field>
      <Field label="Execution mode"><Select ariaLabel="Execution mode" value={mode} onChange={setMode} options={[{ value: 'planning', label: 'Planning only' }, { value: 'generate', label: 'Generate through approved backend' }]} /></Field>
      <Field label="Source kind"><Select ariaLabel="Source kind" value={sourceKind} onChange={setSourceKind} options={[{ value: 'work', label: 'Work' }, { value: 'series', label: 'Series' }]} /></Field>
      <Field label="Source ID"><TextInput ariaLabel="Source work ID" value={sourceId} onChange={setSourceId} /></Field>
      <Field label="Source revision"><NumberField ariaLabel="Source revision" value={sourceRevision} min={1} width="w-full" onChange={setSourceRevision} /></Field>
      <Field label="Cadence"><Select ariaLabel="Cadence kind" value={cadenceKind} onChange={setCadenceKind} options={[{ value: 'interval', label: 'Every 15 minutes' }, { value: 'recurrence', label: 'Calendar recurrence' }]} /></Field>
      {cadenceKind === 'recurrence' && <>
        <Field label="First local occurrence"><TextInput ariaLabel="Recurrence start" value={recurrenceStart} onChange={setRecurrenceStart} /></Field>
        <Field label="Recurrence rule"><TextInput ariaLabel="Recurrence rule" value={recurrenceRule} onChange={setRecurrenceRule} /></Field>
      </>}
      {mode === 'generate' && (ability === 'image' || ability === 'video' || ability === 'music') &&
        <Field label="Generation prompt"><TextArea ariaLabel="Generation prompt" value={mediaPrompt} onChange={setMediaPrompt} /></Field>}
      {mode === 'generate' && ability === 'image' && <Field label="Image size"><TextInput ariaLabel="Image size" value={imageSize} onChange={setImageSize} /></Field>}
      {mode === 'generate' && ability === 'video' && <><Field label="Duration seconds"><NumberField ariaLabel="Duration seconds" value={videoDuration} min={1} width="w-full" onChange={setVideoDuration} /></Field><Field label="Aspect ratio"><TextInput ariaLabel="Aspect ratio" value={videoAspect} onChange={setVideoAspect} /></Field></>}
      {mode === 'generate' && ability === 'music' && <><Field label="Track ID"><TextInput ariaLabel="Track ID" value={trackId} onChange={setTrackId} /></Field><Field label="Track revision"><NumberField ariaLabel="Track revision" value={trackRevision} min={1} width="w-full" onChange={setTrackRevision} /></Field><Field label="Length milliseconds"><NumberField ariaLabel="Length milliseconds" value={musicLength} min={1} width="w-full" onChange={setMusicLength} /></Field><Field label="License statement"><TextInput ariaLabel="License statement" value={license} onChange={setLicense} /></Field></>}
      {mode === 'generate' && ability === 'music-video' && <><Field label="Music video project ID"><TextInput ariaLabel="Music video project ID" value={projectId} onChange={setProjectId} /></Field><Field label="Project revision"><NumberField ariaLabel="Music video project revision" value={projectRevision} min={1} width="w-full" onChange={setProjectRevision} /></Field></>}
      {mode === 'generate' && ability === 'series' && <Field label="Series production mode"><Select ariaLabel="Series production mode" value={seriesMode} onChange={setSeriesMode} options={[{ value: 'model', label: 'Managed model' }, { value: 'authored', label: 'Authored approval' }]} /></Field>}
      <div className="flex flex-wrap gap-s"><Button onClick={() => void create()}>Create commission</Button><Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button></div>
    </section> : selected ? <section aria-label="Commission detail" className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-xl font-semibold">{selected.name}</h2><p className="mt-1 text-sm text-on-surface-low">{selected.target_ability} · {selected.mode === 'generate' ? 'generation' : 'planning only'}{selected.mode_source === 'legacy' ? ' (legacy)' : ''} · {selected.schedule_state || (selected.enabled ? 'scheduled' : 'disabled')} {selected.next_fire_at && `· next ${selected.next_fire_at}`} {selected.schedule_error && `· ${selected.schedule_error}`}</p></div><div className="flex flex-wrap gap-2"><Button variant="secondary" onClick={() => void update(!selected.enabled)}>{selected.enabled ? 'Disable' : 'Enable'}</Button><Button onClick={() => void runNow()}><Zap size={15} aria-hidden/>Run now</Button></div></div>
      <section className="space-y-3"><h3 className="font-semibold">Run history</h3>
      {(selected.runs || []).length ? <ul className="space-y-3">{(selected.runs || []).map(run => <li key={run.id} className="rounded-lg bg-surface-container p-l">
        <strong>{run.status}</strong> · project {run.project_id || 'not created'} · {run.attempts.length} attempt(s)
        {run.outputs.map(output => <div key={output.artifact_id} className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-surface-high p-3"><span className="min-w-0 flex-1 break-all">Output {output.artifact_id} v{output.artifact_version}</span>
          <Button size="sm" variant="secondary" onClick={() => void react(run, output, 'liked')}>Like</Button>
          <Button size="sm" variant="ghost" onClick={() => void react(run, output, 'disliked')}>Dislike</Button>
        </div>)}
        {(run.dispatch_receipts || []).map(receipt => <div key={receipt.request_id} className="mt-2 break-all text-xs text-on-surface-low">Dispatch {receipt.backend}/{receipt.operation}: {receipt.status} {receipt.resource_id || receipt.error_code}</div>)}
      </li>)}</ul> : <EmptyState icon={Clock3} title="No runs yet" hint="Run this commission now or wait for its next scheduled occurrence."/>}</section>
      <section className="space-y-3"><h3 className="font-semibold">Feedback</h3><ul className="space-y-2">{(selected.feedback || []).map(row => <li key={row.id} className="space-y-2 rounded-lg border border-outline-variant/20 p-3">
        {row.author}: {row.rating}{row.deleted ? ' (removed)' : ''}
        {!row.deleted && peers.length > 0 && <>
          <Field label="Feedback peer"><Select ariaLabel={`Feedback peer ${row.id}`} value={peerId} onChange={setPeerId} options={peers.map(peer => ({ value: peer.id, label: peer.label }))} /></Field>
          <div className="flex items-center gap-s"><Checkbox ariaLabel={`Approve feedback delivery ${row.id}`} checked={Boolean(approved[row.id])}
            onChange={value => setApproved(current => ({ ...current, [row.id]: value }))} /><span data-type="body-s">Approve revision {row.revision} for delivery</span></div>
          <Button size="sm" disabled={!peerId || !approved[row.id]} onClick={() => void deliver(row)}>Send feedback to peer</Button>
        </>}
        {deliveries[row.id] && <span role="status">Delivered: {deliveries[row.id]}</span>}
      </li>)}</ul></section>
    </section> : <EmptyState icon={Sparkles} title="Choose a commission" hint="Open a standing brief to inspect its status, render history, and feedback." action={{ label: 'New commission', onClick: () => setCreating(true), icon: Plus }}/>}</div></Surface>
  </ListScaffold>
}

export default Commissions
