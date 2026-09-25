import { useEffect, useState } from 'react'
import NativeMediaPage from './NativeMediaPage'
import { Button } from '../../../shared/ui/Button'

type Model = { name: string; max_duration_s: number; durations: number[]; aspect_ratios: string[]; supports_first_frame: boolean; supports_last_frame: boolean; supports_continuation: boolean; controls: Record<string, { minimum: number; maximum: number; integer: boolean }> }
type Caps = { available: boolean; media_tools_available: boolean; selection: string; models: Model[] }
export function videoInput(prompt: string, duration: number, aspect: string, controls: Record<string, number>, refs: Record<string, { id: string; version: number }>) {
  return { prompt, duration_seconds: duration, aspect_ratio: aspect, controls, ...Object.fromEntries(Object.entries(refs).filter(([, ref]) => ref.id.trim()).flatMap(([key, ref]) => [[key + '_artifact_id', ref.id.trim()], [key + '_version', ref.version]])) }
}
export default function VideoPage() {
  const [caps, setCaps] = useState<Caps | null>(null)
  const [prompt, setPrompt] = useState('')
  const [duration, setDuration] = useState(4)
  const [aspect, setAspect] = useState('')
  const [controls, setControls] = useState<Record<string, number>>({})
  const [refs, setRefs] = useState<Record<string, { id: string; version: number }>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [job, setJob] = useState('')
  useEffect(() => { let live = true; void fetch('/api/capabilities/media/videos').then(async response => { const value = await response.json(); if (!response.ok) throw new Error(value.error); if (live) { setCaps(value); setDuration(value.models[0]?.durations?.[0] || Math.min(4, value.models[0]?.max_duration_s || 4)) } }).catch(e => { if (live) setError(e.message) }); return () => { live = false } }, [])
  const model = caps?.models[0]
  async function submit() {
    setBusy(true); setError(''); setJob('')
    try { const response = await fetch('/api/capabilities/media/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation: 'video_generate', request_id: crypto.randomUUID(), input: videoInput(prompt, duration, aspect, controls, refs) }) }); const value = await response.json(); if (!response.ok) throw new Error(value.error); setJob(value.id) } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }
  return <NativeMediaPage title="Video generation" actions={<a href="#/capabilities/media?view=jobs">Media jobs</a>} width="content"><p>Use advertised model controls and pinned artifact versions. Continuation may use only the preceding clip’s final frame; it does not promise full temporal context.</p><p>Configuration availability does not verify inference. Originals remain unchanged; completed videos enter the media library.</p>{!caps && !error && <p>Loading video capabilities…</p>}{caps && <p>{caps.selection || 'No video provider selected'} · {caps.available ? 'Configured' : 'Unavailable'} · {caps.media_tools_available ? 'Video processing available' : 'FFmpeg and ffprobe required'}</p>}
    <label className="block">Prompt<textarea value={prompt} maxLength={4000} onChange={e => setPrompt(e.target.value)} /></label>
    <label className="block">Duration (seconds){model?.durations?.length ? <select value={duration} onChange={e => setDuration(Number(e.target.value))}>{model.durations.map(value => <option key={value}>{value}</option>)}</select> : <input type="number" min={1} max={model?.max_duration_s || 60} value={duration} onChange={e => setDuration(Number(e.target.value))} />}</label>
    <label className="block">Aspect ratio<select value={aspect} onChange={e => setAspect(e.target.value)}><option value="">Model default</option>{model?.aspect_ratios.map(value => <option key={value}>{value}</option>)}</select></label>
    {model && Object.entries(model.controls).map(([key, spec]) => <label key={key} className="block">{key}<input type="number" min={spec.minimum} max={spec.maximum} step={spec.integer ? 1 : 'any'} value={controls[key] ?? ''} onChange={e => setControls(previous => { const next = { ...previous }; if (e.target.value === '') delete next[key]; else next[key] = Number(e.target.value); return next })} /></label>)}
    {(['first_frame', 'last_frame', 'continuation'] as const).map(key => <fieldset key={key} disabled={!model?.[`supports_${key}`]}><legend>{key.replaceAll('_', ' ')} {!model?.[`supports_${key}`] && '(unsupported)'}</legend><label>Artifact ID<input value={refs[key]?.id || ''} onChange={e => setRefs({ ...refs, [key]: { version: refs[key]?.version || 1, id: e.target.value } })} /></label><label>Version<input type="number" min={1} value={refs[key]?.version || 1} onChange={e => setRefs({ ...refs, [key]: { id: refs[key]?.id || '', version: Number(e.target.value) } })} /></label></fieldset>)}
    <Button disabled={busy || !prompt.trim() || !caps?.available || !caps.media_tools_available || !model} onClick={() => void submit()}>Queue video</Button>{job && <p>Queued {job}. <a href="#/capabilities/media?view=jobs">View job</a></p>}{error && <p role="alert">{error}</p>}
  </NativeMediaPage>
}
