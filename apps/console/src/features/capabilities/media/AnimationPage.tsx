import { useEffect, useState } from 'react'
import NativeMediaPage from './NativeMediaPage'
import { Button } from '../../../shared/ui/Button'

type Job = { id: string; status: string; error?: string; input?: Draft; result?: { artifact_id: string; version: number; frame: { width: number; height: number } } }
type Draft = { title: string; concept: string; renderer: string; duration_seconds: number; width: number; height: number; fps: number; interactive: boolean }
export const initialAnimation: Draft = { title: '', concept: '', renderer: 'canvas2d', duration_seconds: 20, width: 1280, height: 720, fps: 30, interactive: false }
const base = '/api/capabilities/media/jobs'
async function api(path: string, body?: unknown) { const response = await fetch(base + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined); const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Animation request failed'); return value }
export function previewUrl(job: Job) { return job.result ? `${base}/${encodeURIComponent(job.id)}/animation` : '' }
export function downloadUrl(job: Job) { return job.result ? `/api/artifacts/${encodeURIComponent(job.result.artifact_id)}/raw?version=${job.result.version}` : '' }

export default function AnimationPage() {
  const [draft, setDraft] = useState(initialAnimation), [job, setJob] = useState<Job | null>(null), [jobId, setJobId] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); try { await work() } catch (reason) { setError(String((reason as Error).message)) } finally { setBusy(false) } }
  async function load(id: string) { const value = await api('/' + encodeURIComponent(id)); setJob(value); setJobId(value.id); if (value.input) setDraft(value.input) }
  useEffect(() => { const id = new URLSearchParams(location.hash.split('?')[1] || '').get('job'); if (id) void action(() => load(id)) }, [])
  const url = job ? previewUrl(job) : ''
  return <NativeMediaPage title="Code animation" actions={<a href="#/capabilities/media?view=jobs">Animation jobs</a>}><p>Generate an original, self-contained HTML animation with the selected reasoning model. A successful job stores the exact validated model response as a canonical artifact. External network access and assets are rejected; local success does not claim external model availability.</p>
    <fieldset disabled={busy}><label>Title<input maxLength={120} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label><label>Concept<textarea maxLength={4000} value={draft.concept} onChange={event => setDraft({ ...draft, concept: event.target.value })} /></label><label>Renderer<select value={draft.renderer} onChange={event => setDraft({ ...draft, renderer: event.target.value })}>{['canvas2d', 'svg', 'css'].map(value => <option key={value}>{value}</option>)}</select></label>
      {(['duration_seconds', 'width', 'height', 'fps'] as const).map(key => <label key={key}>{key.replaceAll('_', ' ')}<input type="number" value={draft[key]} onChange={event => setDraft({ ...draft, [key]: Number(event.target.value) })} /></label>)}<label><input type="checkbox" checked={draft.interactive} onChange={event => setDraft({ ...draft, interactive: event.target.checked })} />Allow pointer or keyboard interaction</label>
      <Button disabled={!draft.title.trim() || !draft.concept.trim()} onClick={() => void action(async () => { const value = await api('', { operation: 'code_animation_generate', request_id: crypto.randomUUID(), input: draft }); setJob(value); setJobId(value.id) })}>Generate with reasoning model</Button><label>Animation job ID<input value={jobId} onChange={event => setJobId(event.target.value)} /></label><Button disabled={!jobId} onClick={() => void action(() => load(jobId))}>Load job</Button></fieldset>
    {job && <p role="status">{job.status}{job.error ? ` · ${job.error}` : ''}</p>}{job && url && <section><h2>Sandboxed preview</h2><iframe title="Code animation preview" sandbox="allow-scripts" src={url} style={{ width: job?.result?.frame.width, maxWidth: '100%', aspectRatio: `${job?.result?.frame.width}/${job?.result?.frame.height}` }} /><a href={downloadUrl(job)} download>Download canonical HTML</a></section>}{error && <p role="alert">{error}</p>}
  </NativeMediaPage>
}
