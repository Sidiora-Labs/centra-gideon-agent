import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { useAgentActivity } from '../../../shared/data/useAgentActivity'
import { useReducedMotion } from '../../../shared/theme/motion'
import { Button } from '../../../shared/ui/Button'
import ModelViewer from '../music/ModelViewer'
import { useSpeechPlayback } from './speechPlayback'
import { avatarState, avatarClip, avatarStates } from './avatarState'
type Avatar = { id: string; title: string; artifact_slug: string; artifact_version: number; availability: string; clips: Record<string, string> }
type Selection = { revision: number; avatar_id: string | null; entity_id: string }
type Model = { slug: string; version: number; name: string; clips: string[] }
export default function AvatarPanel({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const reducedMotion = useReducedMotion()
  const activity = useAgentActivity()
  const speaking = useSpeechPlayback()
  const [avatars, setAvatars] = useState<Avatar[]>([])
  const [models, setModels] = useState<Model[]>([])
  const [selection, setSelection] = useState<Selection | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [model, setModel] = useState('')
  const [title, setTitle] = useState('')
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const refresh = async () => {
    const [registered, selected, available] = await Promise.all([requestJson<{ avatars: Avatar[] }>(baseUrl + '/avatars'), requestJson<{ selection: Selection }>(baseUrl + '/avatar-selection'), requestJson<{ models: Model[] }>(baseUrl + '/avatar-models')])
    setAvatars(registered.avatars); setSelection(selected.selection); setModels(available.models)
  }
  useEffect(() => { void refresh().catch(cause => setError(String(cause))) }, [baseUrl])
  const run = async (operation: () => Promise<unknown>) => {
    setBusy(true); setError('')
    try { await operation(); await refresh() } catch (cause) { setError(String(cause)) } finally { setBusy(false) }
  }
  const selected = avatars.find(row => row.id === selection?.avatar_id)
  const state = avatarState(activity.entities, selection?.entity_id || '', speaking, !!activity.error || activity.loading)
  const source = models.find(row => `${row.slug}:${row.version}` === model)
  return <section aria-label="Animated avatar" className="space-y-3 border rounded-xl p-4">
    <h2>Animated avatar</h2>
    <p>Motion follows reported agent activity and actual speech playback.</p>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void run(() => requestJson(baseUrl + '/avatars/bundled', 'POST', {}))}>Install bundled robot</Button><Button onClick={() => void refresh().catch(cause => setError(String(cause)))} disabled={busy}>Refresh avatars</Button></div>
    {selection && <div className="flex flex-wrap gap-2">
      <label>Avatar<select disabled={busy} value={selection.avatar_id || ''} onChange={event => void run(() => requestJson(baseUrl + '/avatar-selection', 'PUT', { ...selection, avatar_id: event.target.value || null }))}><option value="">No avatar</option>{avatars.map(row => <option key={row.id} disabled={row.availability !== 'ready'} value={row.id}>{row.title}{row.availability !== 'ready' ? ' (source unavailable)' : ''}</option>)}</select></label>
      <label>Activity source<select disabled={busy} value={selection.entity_id} onChange={event => void run(() => requestJson(baseUrl + '/avatar-selection', 'PUT', { ...selection, entity_id: event.target.value }))}><option value="">Overall activity</option>{activity.entities.map(entity => <option key={entity.id} value={entity.id}>{entity.title}</option>)}{selection.entity_id && !activity.entities.some(entity => entity.id === selection.entity_id) && <option value={selection.entity_id}>Selected source unavailable</option>}</select></label>
    </div>}
    <p role="status">Avatar state: {state}{activity.error ? ' — activity source unavailable' : ''}</p>
    {selected?.availability === 'ready' ? <><ModelViewer artifactRef={{ slug: selected.artifact_slug, version: selected.artifact_version }} artifactBase={baseUrl + '/avatar-assets'} animationName={avatarClip(state, selected.clips)} reducedMotion={reducedMotion || state === 'unknown'} />{state !== 'unknown' && !selected.clips[state] && <p>No {state} clip; showing the recorded idle clip.</p>}</> : <p>{selected ? 'Avatar source is unavailable.' : 'Choose a published animated model or install the bundled robot.'}</p>}
    <form onSubmit={event => { event.preventDefault(); if (source) void run(() => requestJson(baseUrl + '/avatars', 'POST', { title, artifact_slug: source.slug, artifact_version: source.version, clips: mapping })) }} className="space-y-2">
      <h3>Publish an avatar variant</h3><label>Animated model<select value={model} onChange={event => { setModel(event.target.value); const next = models.find(row => `${row.slug}:${row.version}` === event.target.value); setTitle(next?.name || ''); setMapping(next ? { idle: next.clips[0] } : {}) }}><option value="">Choose an existing animated model</option>{models.map(row => <option key={`${row.slug}:${row.version}`} value={`${row.slug}:${row.version}`}>{row.name} · version {row.version}</option>)}</select></label>
      {source && <><label>Avatar name<input required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} /></label>{avatarStates.map(name => <label key={name}>{name} clip<select value={mapping[name] || ''} required={name === 'idle'} onChange={event => setMapping(previous => { const next = { ...previous }; if (event.target.value) next[name] = event.target.value; else delete next[name]; return next })}><option value="">{name === 'idle' ? 'Choose idle clip' : 'Use idle fallback'}</option>{source.clips.map(clip => <option key={clip} value={clip}>{clip}</option>)}</select></label>)}</>}
      <Button type="submit" disabled={busy || !source}>Publish avatar</Button>
    </form>
  </section>
}
