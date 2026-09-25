import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Segment = { start: number; end: number; text: string; source_link: string }
type Preview = { preview_id: string; title: string; video_id: string; url: string; format: string; language: string; segments: Segment[]; text: string }
type Event = { sequence: number; stage: string; status: string; detail: string; happened_at: string }
type Job = { id: string; video_id: string; url: string; title: string; status: string; stage: string; error: string; source_id?: string; transcript_id?: string; artifacts: { kind: string; bytes: number; sha256: string }[]; events: Event[] }
const root = '/api/capabilities/knowledge/videos'

export default function VideosPage() {
  const [jobs, setJobs] = useState<Job[]>([]), [selected, setSelected] = useState<Job | null>(null)
  const [url, setUrl] = useState(''), [title, setTitle] = useState(''), [language, setLanguage] = useState('en')
  const [format, setFormat] = useState('vtt'), [content, setContent] = useState('')
  const [wantTranscript, setWantTranscript] = useState(true), [wantVideo, setWantVideo] = useState(false), [wantAudio, setWantAudio] = useState(false)
  const [review, setReview] = useState<Preview | null>(null), [availability, setAvailability] = useState({ caption_retrieval: false, reason: '' })
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [transcript, setTranscript] = useState('')
  const requestId = useRef(crypto.randomUUID())
  async function load() { const data = await requestJson<{ items: Job[]; availability: typeof availability }>(root); setJobs(data.items); setAvailability(data.availability); setSelected(current => current ? data.items.find(job => job.id === current.id) ?? current : current) }
  useEffect(() => { load().catch(e => setError(e.message)) }, [])
  async function act(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  function changed() { setReview(null); requestId.current = crypto.randomUUID() }
  async function preview() { await act(async () => setReview(await requestJson<Preview>(root + '/preview', 'POST', { url, title, format, content, language }))) }
  async function importTranscript() { if (!review) return; await act(async () => { const job = await requestJson<Job>(root + '/import', 'POST', { request_id: requestId.current, url, title, format, content, language, preview_id: review.preview_id }); setSelected(job); await load(); setReview(null); requestId.current = crypto.randomUUID() }) }
  async function fetchVideo() { await act(async () => { const job = await requestJson<Job>(root + '/fetch', 'POST', { request_id: requestId.current, url, language, transcript: wantTranscript, video: wantVideo, audio: wantAudio }); setSelected(job); await load(); requestId.current = crypto.randomUUID() }) }
  async function refresh(job: Job) { await act(async () => { const current = await requestJson<Job>(`${root}/${job.id}`); setSelected(current); await load() }) }
  async function cancel(job: Job) { await act(async () => { setSelected(await requestJson<Job>(`${root}/${job.id}/cancel`, 'POST')); await load() }) }
  async function openTranscript(job: Job) { await act(async () => setTranscript((await requestJson<{ content: string }>(`${root}/${job.id}/transcript`)).content)) }
  return <main className="mx-auto max-w-4xl space-y-5 p-6"><h1 className="text-2xl font-semibold">Video sources</h1><p>Review supplied captions or acquire selected artifacts from one public YouTube video. Every caption keeps its timestamp and provenance.</p>{error && <p role="alert">{error}</p>}{!availability.caption_retrieval && availability.reason && <p role="status">{availability.reason}</p>}
    <label className="block">Video URL<input aria-label="Video URL" value={url} disabled={busy} onChange={e => { setUrl(e.target.value); changed() }} /></label><label className="block">Language<input aria-label="Caption language" value={language} disabled={busy} onChange={e => { setLanguage(e.target.value); changed() }} /></label>
    <section aria-label="Supplied transcript"><h2>Review supplied captions</h2><label>Title<input aria-label="Video title" value={title} disabled={busy} onChange={e => { setTitle(e.target.value); changed() }} /></label><label>Format<select aria-label="Transcript format" value={format} disabled={busy} onChange={e => { setFormat(e.target.value); changed() }}><option value="vtt">WebVTT</option><option value="srt">SRT</option><option value="json">Timed JSON</option></select></label><label className="block">Caption content<textarea aria-label="Caption content" value={content} disabled={busy} onChange={e => { setContent(e.target.value); changed() }} /></label><Button disabled={busy || !url || !title || !content} onClick={() => void preview()}>Preview captions</Button>{review && <section aria-label="Caption review"><p>{review.segments.length} timed segments · {review.language}</p><a href={review.segments[0]?.source_link}>Open first timestamp</a><pre>{review.text}</pre><Button disabled={busy} onClick={() => void importTranscript()}>Save reviewed transcript</Button></section>}</section>
    <section aria-label="Public acquisition"><h2>Acquire public source</h2><label><input type="checkbox" aria-label="Retrieve captions" checked={wantTranscript} onChange={e => { setWantTranscript(e.target.checked); changed() }} />Captions</label><label><input type="checkbox" aria-label="Download video" checked={wantVideo} onChange={e => { setWantVideo(e.target.checked); changed() }} />Video</label><label><input type="checkbox" aria-label="Download audio" checked={wantAudio} onChange={e => { setWantAudio(e.target.checked); changed() }} />Audio</label><Button disabled={busy || !url || (!wantTranscript && !wantVideo && !wantAudio)} onClick={() => void fetchVideo()}>Start acquisition</Button></section>
    <nav aria-label="Video ingests">{jobs.length === 0 ? <p>No video ingests yet.</p> : jobs.map(job => <Button key={job.id} disabled={busy} onClick={() => setSelected(job)}>{job.title} · {job.status}</Button>)}</nav>
    {selected && <section aria-label="Selected video ingest"><h2>{selected.title}</h2><p>Status: {selected.status} · {selected.stage}</p>{selected.error && <p role="alert">{selected.error}</p>}<a href={selected.url}>Original video</a>{selected.source_id && <a href={`#/knowledge/item/${selected.source_id}`}>Open source record</a>}{selected.transcript_id && <Button onClick={() => void openTranscript(selected)}>Read transcript</Button>}<Button disabled={busy} onClick={() => void refresh(selected)}>Refresh status</Button>{['pending', 'running', 'cancelling'].includes(selected.status) && <Button disabled={busy} onClick={() => void cancel(selected)}>Cancel acquisition</Button>}<ul>{selected.events.map(event => <li key={event.sequence}>{event.stage} · {event.status} · {event.detail}</li>)}</ul>{selected.artifacts.map(item => <p key={item.sha256}>{item.kind} · {item.bytes} bytes · verified {item.sha256.slice(0, 12)}</p>)}</section>}
    {transcript && <section aria-label="Stored transcript"><pre className="whitespace-pre-wrap">{transcript}</pre></section>}
  </main>
}
