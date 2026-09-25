import NativeMediaPage from './NativeMediaPage'
import { EpisodeScenes } from './EpisodePage'
import { useEffect, useState } from 'react'

export type MediaJob = { progress?: number; id: string; operation?: string; input?: { prompt?: string; title?: string; url?: string; kind?: string } | null; sketch_id: string; revision: number; status: string; state_revision: number; attempt: number; error: string | null; result: { artifact_id?: string; version?: number; adapter_id?: string } | null; events: { status: string; at: string; detail: string; attempt?: number; result?: { artifact_id?: string; version?: number; adapter_id?: string } | null }[] }
const base = '/api/capabilities/media/jobs'
async function api(path: string, body?: object) {
  const response = await fetch(base + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Media job request failed'); return result
}
export function JobCard({ job, act, busy }: { job: MediaJob; act: (job: MediaJob, action: string) => void; busy: boolean }) {
  return <article className="rounded-lg bg-surface-container p-l space-y-s">
    {job.progress !== undefined && <progress aria-label="Render progress" value={job.progress} max={1} />}{job.operation === 'source_download' && <a href={'#/capabilities/media?view=downloads&job=' + encodeURIComponent(job.id)}>Open source downloader</a>}{job.operation === 'episode_render' && <EpisodeScenes jobId={job.id} />}{job.operation?.startsWith('sprite_') && <a href={'#/capabilities/media?view=sprites&job=' + encodeURIComponent(job.id)}>Open sprite workspace</a>}{job.operation === 'code_animation_generate' && <a href={'#/capabilities/media?view=animation&job=' + encodeURIComponent(job.id)}>Open animation workspace</a>}<h2>{job.operation === 'source_download' ? `Source ${job.input?.kind} download` : job.operation === 'code_animation_generate' ? 'Code animation: ' + job.input?.title : job.operation === 'sprite_generate' ? 'Sprite frame generation' : job.operation === 'sprite_compile' ? 'Sprite atlas compilation' : job.operation === 'episode_render' ? 'Episode render' : job.operation === 'timeline_render' ? 'Timeline render' : job.operation === 'video_generate' ? 'Video generation' : job.operation === 'image_cleanup' ? 'Image cleanup' : job.operation === 'lora_train' ? 'LoRA training' : job.operation === 'image_generate' ? 'Image generation: ' + job.input?.prompt : <>Sketch {job.sketch_id} · revision {job.revision}</>}</h2><p role="status">{job.status} · attempt {job.attempt}</p>
    {job.error && <p role="alert">{job.error}</p>}
    {job.result?.artifact_id && <a href={'/api/artifacts/' + job.result.artifact_id + '/raw?version=' + job.result.version}>Open media artifact</a>}
    {job.result?.adapter_id && <p>Trained adapter: {job.result.adapter_id}</p>}
    {job.operation === 'lora_train' && <a href={'/api/capabilities/media/jobs/' + job.id + '/checkpoints'}>Retained checkpoint inventory</a>}
    {['queued', 'running'].includes(job.status) && <button disabled={busy} onClick={() => act(job, 'cancel')}>Cancel</button>}
    {['failed', 'cancelled'].includes(job.status) && <button disabled={busy} onClick={() => act(job, 'retry')}>Retry</button>}
    <details><summary>Attempt history</summary>{job.events.map((event, index) => <p key={index}>{event.at} · {event.status} · {event.detail}{event.result?.artifact_id && <> · <a href={'/api/artifacts/' + event.result.artifact_id + '/raw?version=' + event.result.version}>Attempt output</a></>}</p>)}</details>
  </article>
}
export default function JobsPage() {
  const [jobs, setJobs] = useState<MediaJob[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false), [loadError, setLoadError] = useState(''), [loaded, setLoaded] = useState(false)
  useEffect(() => { let alive = true; const load = () => api('').then(value => { if (alive) { setJobs(value.items); setLoadError(''); setLoaded(true) } }).catch(reason => { if (alive) setLoadError(String(reason)) }); void load(); const timer = setInterval(load, 2000); return () => { alive = false; clearInterval(timer) } }, [])
  const act = async (job: MediaJob, action: string) => { setBusy(true); try { const updated = await api('/' + job.id + '/' + action, { state_revision: job.state_revision }); setJobs(old => old.map(item => item.id === updated.id ? updated : item)); setError('') } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  return <NativeMediaPage title="Media jobs" actions={<><a href="#/capabilities/media">Sketches</a><a href="#/capabilities/media?view=library">Library</a></>} width="content">
    <p>Saved sketch PNG exports run in the supervised media worker. Queued jobs wait while the application worker is disabled. Cancellation is cooperative; any completed output is retained.</p>
    {(error || loadError) && <p role="alert">{error || loadError}</p>}{!loaded && <p>Loading jobs…</p>}{loaded && jobs.length === 0 && <p>No media jobs.</p>}{jobs.map(job => <JobCard key={job.id} job={job} act={act} busy={busy} />)}
  </NativeMediaPage>
}
