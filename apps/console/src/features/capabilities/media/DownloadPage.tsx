import { Select, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import NativeMediaPage from './NativeMediaPage'
import { Button } from '../../../shared/ui/Button'

type Job = { id: string; status: string; error?: string; result?: { artifact_id: string; version: number; kind: string } }
const base = '/api/capabilities/media/jobs'
async function api(path: string, body?: unknown) { const response = await fetch(base + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined); const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Source download request failed'); return value }
export function artifactUrl(job: Job | null) { return job?.result ? `/api/artifacts/${encodeURIComponent(job.result.artifact_id)}/raw?version=${job.result.version}` : '' }

export default function DownloadPage({ jobId: selectedJobId }: { jobId?: string } = {}) {
  const [url, setUrl] = useState(''), [kind, setKind] = useState('video'), [jobId, setJobId] = useState(''), [job, setJob] = useState<Job | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); try { await work() } catch (reason) { setError(String((reason as Error).message)) } finally { setBusy(false) } }
  async function load(id: string) { const value = await api('/' + encodeURIComponent(id)); setJob(value); setJobId(value.id) }
  useEffect(() => { const id = selectedJobId ?? new URLSearchParams(location.hash.split('?')[1] || '').get('job'); if (id) void action(() => load(id)) }, [selectedJobId])
  const download = artifactUrl(job)
  return <NativeMediaPage title="Media source downloader" actions={<a href="#/capabilities/media?view=jobs">Media jobs</a>}><p>Acquire one bounded public YouTube source through the guarded media transport. Every request and redirect is checked against the session egress policy; successful bytes become a canonical artifact with source provenance.</p><fieldset disabled={busy}><label>Source URL<TextInput type="url" maxLength={2000} placeholder="https://www.youtube.com/watch?v=…" value={String(url)} onChange={nextValue => setUrl(nextValue)} /></label><label>Media kind<Select value={String(kind)} onChange={nextValue => setKind(nextValue)} options={[{ value: String("video"), label: "Video" }, { value: String("audio"), label: "Audio" }]} /></label><Button disabled={!url.trim()} onClick={() => void action(async () => { const value = await api('', { operation: 'source_download', request_id: crypto.randomUUID(), input: { url: url.trim(), kind } }); setJob(value); setJobId(value.id) })} disabledReason={busy ? BUSY_REASON : !url.trim() ? "Enter a source URL before queuing a download." : undefined}>Queue guarded download</Button><label>Download job ID<TextInput value={String(jobId)} onChange={nextValue => setJobId(nextValue)} /></label><Button disabled={!jobId} onClick={() => void action(() => load(jobId))} disabledReason={busy ? BUSY_REASON : !jobId ? "Choose or enter a job ID before loading the job." : undefined}>Load job</Button></fieldset>{job && <p role="status">{job.status}{job.error ? ` · ${job.error}` : ''}</p>}{download && <p><a href={download} download>Download canonical {job?.result?.kind}</a> · <a href="#/capabilities/media?view=library">Open media library</a></p>}{error && <p role="alert">{error}</p>}</NativeMediaPage>
}
