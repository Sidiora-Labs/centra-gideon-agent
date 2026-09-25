import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'

export type Annotation = { id: string; text: string; region?: number[]; time_seconds?: number }
type Record = { revision: number; source_kind: string; source_available: boolean; annotations: Annotation[]; attribution: { creator: string; license: string; source_url: string } }
export function AnnotationList({ entries, remove }: { entries: Annotation[]; remove: (id: string) => void }) {
  return <ul>{entries.map(entry => <li key={entry.id} className="rounded-lg bg-surface-high p-m my-s">
    <p>{entry.text}</p>{entry.region && <p>Image region: {entry.region.join(', ')}</p>}{entry.time_seconds !== undefined && <p>At {entry.time_seconds} seconds · duration not verified</p>}
    <Button onClick={() => remove(entry.id)}>Remove note</Button>
  </li>)}</ul>
}

export default function Annotations({ artifactId, version }: { artifactId: string; version: number }) {
  const [record, setRecord] = useState<Record | null>(null)
  const [entries, setEntries] = useState<Annotation[]>([])
  const [attribution, setAttribution] = useState({ creator: '', license: '', source_url: '' })
  const [note, setNote] = useState('')
  const [region, setRegion] = useState(false)
  const [rect, setRect] = useState([0, 0, 1, 1])
  const [time, setTime] = useState('')
  const [history, setHistory] = useState<{ revision: number; annotation_count: number }[]>([])
  const [historyTotal, setHistoryTotal] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const video = useRef<HTMLVideoElement>(null)
  const base = '/api/capabilities/media/library/' + encodeURIComponent(artifactId) + '/versions/' + version + '/annotations'
  const raw = '/api/artifacts/' + encodeURIComponent(artifactId) + '/raw?version=' + version
  async function response(value: Response) { const body = await value.json(); if (!value.ok) throw new Error(body.error || 'Request failed'); return body }
  function adopt(value: Record) { setRecord(value); setEntries(value.annotations); setAttribution(value.attribution) }
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); try { await work() } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  useEffect(() => {
    let active = true
    setRecord(null); setHistory([]); setError('')
    fetch(base).then(response).then(value => { if (active) adopt(value) }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [base])
  return <section aria-label="Media annotations" className="space-y-3 rounded-lg bg-surface-container p-l">
    <h3>Notes and attribution · artifact version {version}</h3>
    {error && <p role="alert">{error}</p>}
    {!record && !error && <p role="status">Loading annotations…</p>}
    {record && <>
      {!record.source_available && <p role="alert">The original media version is unavailable. Saved notes remain readable.</p>}
      {record.source_available && record.source_kind === 'image' && <div className="relative max-w-lg">
        <img src={raw} alt="Annotated image" className="w-full" />
        {entries.filter(entry => entry.region).map(entry => <span key={entry.id} aria-label={entry.text} className="absolute border-2 border-yellow-500 pointer-events-none" style={{ left: entry.region![0]*100+'%', top: entry.region![1]*100+'%', width: entry.region![2]*100+'%', height: entry.region![3]*100+'%' }} />)}
      </div>}
      {record.source_available && record.source_kind === 'video' && <><video ref={video} src={raw} controls preload="metadata" className="max-w-full" />{entries.filter(entry => entry.time_seconds !== undefined).map(entry => <Button key={entry.id} onClick={() => { if (video.current) video.current.currentTime = entry.time_seconds! }}>Jump to {entry.time_seconds}s</Button>)}</>}
      <AnnotationList entries={entries} remove={id => setEntries(old => old.filter(entry => entry.id !== id))} />
      {!entries.length && <p>No notes on this version.</p>}
      <label>New note<textarea aria-label="New media note" value={note} maxLength={4000} onChange={e => setNote(e.target.value)} /></label>
      {record.source_kind === 'image' && <><label><input type="checkbox" checked={region} onChange={e => setRegion(e.target.checked)} />Mark an image region</label>{region && <div className="flex flex-wrap gap-2">{['Left', 'Top', 'Width', 'Height'].map((label, i) => <label key={label}>{label}<input aria-label={'Region '+label} type="number" min="0" max="1" step="0.01" value={rect[i]} onChange={e => setRect(old => old.map((value, j) => j === i ? Number(e.target.value) : value))} /></label>)}</div>}</>}
      {record.source_kind === 'video' && <label>Time in seconds (optional)<input aria-label="Note time in seconds" type="number" min="0" max="604800" value={time} onChange={e => setTime(e.target.value)} /></label>}
      <Button disabled={!note.trim() || entries.length >= 100 || busy || !record.source_available} onClick={() => { setEntries(old => [...old, { id: crypto.randomUUID(), text: note, ...(record.source_kind === 'image' && region ? { region: rect } : {}), ...(record.source_kind === 'video' && time !== '' ? { time_seconds: Number(time) } : {}) }]); setNote('') }}>Add note</Button>
      <div className="flex flex-wrap gap-3">{(['creator', 'license', 'source_url'] as const).map(key => <label key={key}>{key === 'source_url' ? 'Source URL' : key === 'creator' ? 'Creator' : 'License'}<input aria-label={'Attribution '+key} value={attribution[key]} onChange={e => setAttribution(old => ({ ...old, [key]: e.target.value }))} /></label>)}</div>
      <Button disabled={busy || !record.source_available} onClick={() => void action(async () => adopt(await fetch(base, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision: record.revision, request_id: crypto.randomUUID(), annotations: entries, attribution }) }).then(response)))}>Save notes and attribution</Button>
      <Button disabled={busy} onClick={() => void action(async () => { const value = await fetch(base+'/history').then(response); setHistory(value.items); setHistoryTotal(value.total) })}>Revision history</Button>
      <p>Saved annotation revision {record.revision}. Import provenance stays unchanged.</p>
      {history.map(item => <Button key={item.revision} disabled={busy} onClick={() => void action(async () => { const previous = await fetch(base+'/history/'+item.revision).then(response); setEntries(previous.annotations); setAttribution(previous.attribution) })}>Load revision {item.revision} ({item.annotation_count} notes)</Button>)}
      {history.length < historyTotal && <Button disabled={busy} onClick={() => void action(async () => { const value = await fetch(base+'/history?offset='+history.length).then(response); setHistory(old => [...old, ...value.items]) })}>Older revisions</Button>}
    </>}
  </section>
}
