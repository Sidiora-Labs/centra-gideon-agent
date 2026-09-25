import { useEffect, useState } from 'react'

export type MediaJob = { id: string; operation?: string; input?: { prompt: string } | null; sketch_id: string; revision: number; status: string; state_revision: number; attempt: number; error: string | null; result: { artifact_id?: string; version?: number; adapter_id?: string } | null; events: { status: string; at: string; detail: string; attempt?: number; result?: { artifact_id?: string; version?: number; adapter_id?: string } | null }[] }
const base = '/api/capabilities/media/jobs'
async function api(path: string, body?: object) {
  const response = await fetch(base + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
  const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Media job request failed'); return result
}
export function JobCard({ job, act, busy }: { job: MediaJob; act: (job: MediaJob, action: string) => void; busy: boolean }) {
  return <article className="rounded border p-3 space-y-2">
    <h2>{job.operation === 'video_generate' ? 'Video generation' : job.operation === 'image_cleanup' ? 'Image cleanup' : job.operation === 'lora_train' ? 'LoRA training' : job.operation === 'image_generate' ? 'Image generation: ' + job.input?.prompt : <>Sketch {job.sketch_id} · revision {job.revision}</>}</h2><p role="status">{job.status} · attempt {job.attempt}</p>
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
  return <section className="p-6 space-y-4"><h1>Media jobs</h1><a href="#/capabilities/media">Sketches</a> · <a href="#/capabilities/media?view=library">Media library</a>
    <p>Saved sketch PNG exports run in the supervised media worker. Queued jobs wait while the application worker is disabled. Cancellation is cooperative; any completed output is retained.</p>
    {(error || loadError) && <p role="alert">{error || loadError}</p>}{!loaded && <p>Loading jobs…</p>}{loaded && jobs.length === 0 && <p>No media jobs.</p>}{jobs.map(job => <JobCard key={job.id} job={job} act={act} busy={busy} />)}
  </section>
}
