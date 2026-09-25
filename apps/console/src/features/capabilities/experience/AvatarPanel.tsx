import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { useAgentActivity } from '../../../shared/data/useAgentActivity'
import { useReducedMotion } from '../../../shared/theme/motion'
import { Button } from '../../../shared/ui/Button'
import ModelViewer from '../music/ModelViewer'
import { useSpeechPlayback } from './speechPlayback'
import { avatarState, avatarClip, avatarStates } from './avatarState'
import { Field, Select, TextInput } from '../../../shared/ui/forms'
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
  return <section aria-label="Animated avatar" className="space-y-m">
    <h2 data-type="title-m">Animated avatar</h2>
    <p>Motion follows reported agent activity and actual speech playback.</p>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => void run(() => requestJson(baseUrl + '/avatars/bundled', 'POST', {}))}>Install bundled robot</Button><Button onClick={() => void refresh().catch(cause => setError(String(cause)))} disabled={busy}>Refresh avatars</Button></div>
    {selection && <div className="flex flex-wrap gap-2">
      <Field label="Avatar"><Select disabled={busy} value={selection.avatar_id || ''} onChange={value => void run(() => requestJson(baseUrl + '/avatar-selection', 'PUT', { ...selection, avatar_id: value || null }))} options={[{value:'',label:'No avatar'},...avatars.map(row=>({value:row.id,label:row.title+(row.availability !== 'ready'?' (source unavailable)':''),disabled:row.availability !== 'ready'}))]} /></Field>
      <Field label="Activity source"><Select disabled={busy} value={selection.entity_id} onChange={value => void run(() => requestJson(baseUrl + '/avatar-selection', 'PUT', { ...selection, entity_id: value }))} options={[{value:'',label:'Overall activity'},...activity.entities.map(entity=>({value:entity.id,label:entity.title})),...(selection.entity_id&&!activity.entities.some(entity=>entity.id===selection.entity_id)?[{value:selection.entity_id,label:'Selected source unavailable'}]:[])]} /></Field>
    </div>}
    <p role="status">Avatar state: {state}{activity.error ? ' — activity source unavailable' : ''}</p>
    {selected?.availability === 'ready' ? <><ModelViewer artifactRef={{ slug: selected.artifact_slug, version: selected.artifact_version }} artifactBase={baseUrl + '/avatar-assets'} animationName={avatarClip(state, selected.clips)} reducedMotion={reducedMotion || state === 'unknown'} />{state !== 'unknown' && !selected.clips[state] && <p>No {state} clip; showing the recorded idle clip.</p>}</> : <p>{selected ? 'Avatar source is unavailable.' : 'Choose a published animated model or install the bundled robot.'}</p>}
    <form onSubmit={event => { event.preventDefault(); if (source) void run(() => requestJson(baseUrl + '/avatars', 'POST', { title, artifact_slug: source.slug, artifact_version: source.version, clips: mapping })) }} className="space-y-2">
      <h3 data-type="title-m">Publish an avatar variant</h3><Field label="Animated model"><Select value={model} onChange={value => { setModel(value); const next = models.find(row => `${row.slug}:${row.version}` === value); setTitle(next?.name || ''); setMapping(next ? { idle: next.clips[0] } : {}) }} options={[{value:'',label:'Choose an existing animated model'},...models.map(row=>({value:`${row.slug}:${row.version}`,label:`${row.name} · version ${row.version}`}))]} /></Field>
      {source && <><Field label="Avatar name"><TextInput required maxLength={200} value={title} onChange={setTitle} /></Field>{avatarStates.map(name => <label key={name}>{name} clip<select className="h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m" value={mapping[name] || ''} required={name === 'idle'} onChange={event => setMapping(previous => { const next = { ...previous }; if (event.target.value) next[name] = event.target.value; else delete next[name]; return next })}><option value="">{name === 'idle' ? 'Choose idle clip' : 'Use idle fallback'}</option>{source.clips.map(clip => <option key={clip} value={clip}>{clip}</option>)}</select></label>)}</>}
      <Button type="submit" disabled={busy || !source}>Publish avatar</Button>
    </form>
  </section>
}
