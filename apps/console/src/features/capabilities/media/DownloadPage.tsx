import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'

type Job = { id: string; status: string; error?: string; result?: { artifact_id: string; version: number; kind: string } }
const base = '/api/capabilities/media/jobs'
async function api(path: string, body?: unknown) { const response = await fetch(base + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined); const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Source download request failed'); return value }
export function artifactUrl(job: Job | null) { return job?.result ? `/api/artifacts/${encodeURIComponent(job.result.artifact_id)}/raw?version=${job.result.version}` : '' }

export default function DownloadPage() {
  const [url, setUrl] = useState(''), [kind, setKind] = useState('video'), [jobId, setJobId] = useState(''), [job, setJob] = useState<Job | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); try { await work() } catch (reason) { setError(String((reason as Error).message)) } finally { setBusy(false) } }
  async function load(id: string) { const value = await api('/' + encodeURIComponent(id)); setJob(value); setJobId(value.id) }
  useEffect(() => { const id = new URLSearchParams(location.hash.split('?')[1] || '').get('job'); if (id) void action(() => load(id)) }, [])
  const download = artifactUrl(job)
  return <main className="p-4 space-y-4"><h1>Media source downloader</h1><a href="#/capabilities/media?view=jobs">Media jobs</a><p>Acquire one bounded public YouTube source through the guarded media transport. Every request and redirect is checked against the session egress policy; successful bytes become a canonical artifact with source provenance.</p><fieldset disabled={busy}><label>Source URL<input type="url" maxLength={2000} placeholder="https://www.youtube.com/watch?v=…" value={url} onChange={event => setUrl(event.target.value)} /></label><label>Media kind<select value={kind} onChange={event => setKind(event.target.value)}><option value="video">Video</option><option value="audio">Audio</option></select></label><Button disabled={!url.trim()} onClick={() => void action(async () => { const value = await api('', { operation: 'source_download', request_id: crypto.randomUUID(), input: { url: url.trim(), kind } }); setJob(value); setJobId(value.id) })}>Queue guarded download</Button><label>Download job ID<input value={jobId} onChange={event => setJobId(event.target.value)} /></label><Button disabled={!jobId} onClick={() => void action(() => load(jobId))}>Load job</Button></fieldset>{job && <p role="status">{job.status}{job.error ? ` · ${job.error}` : ''}</p>}{download && <p><a href={download} download>Download canonical {job?.result?.kind}</a> · <a href="#/capabilities/media?view=library">Open media library</a></p>}{error && <p role="alert">{error}</p>}</main>
}
