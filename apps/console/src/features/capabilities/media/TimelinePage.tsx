import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
type Entry = Record<string, string | number>
type Draft = { title: string; width: number; height: number; fps: number; segments: Entry[]; overlays: Entry[]; audio: Entry[] }
type Timeline = Draft & { id: string; revision: number; duration: number }
export const emptyTimeline = (): Draft => ({ title: '', width: 1280, height: 720, fps: 24, segments: [], overlays: [], audio: [] })
export function timelineEntry(track: 'segments' | 'overlays' | 'audio'): Entry {
  const common = { artifact_id: '', version: 1, start: 0, duration: 1 }
  return track === 'segments' ? { ...common, kind: 'image' } : track === 'overlays' ? { ...common, x: 0, y: 0, width: 64, height: 64 } : { ...common, trim: 0, volume: 1, fade_in: 0, fade_out: 0 }
}
export function moveEntry(entries: Entry[], index: number, delta: number) {
  const next = [...entries], target = index + delta
  if (target >= 0 && target < next.length) [next[index], next[target]] = [next[target], next[index]]
  return next
}
export function TimelineTrack({ track, entries, change }: { track: 'segments' | 'overlays' | 'audio'; entries: Entry[]; change: (entries: Entry[]) => void }) {
  return <section><h2>{track}</h2><ol>{entries.map((entry, index) => <li key={index} className="p-3 border rounded space-x-2"><strong>{index + 1}</strong>{Object.entries(entry).map(([key, value]) => <label key={key}>{key.replaceAll('_', ' ')}{key === 'kind' ? <select value={value} onChange={e => change(entries.map((item, i) => i === index ? { ...item, kind: e.target.value } : item))}><option value="image">Image</option><option value="video">Video</option></select> : <input type={typeof value === 'number' ? 'number' : 'text'} step="any" value={value} onChange={e => change(entries.map((item, i) => i === index ? { ...item, [key]: typeof value === 'number' ? Number(e.target.value) : e.target.value } : item))} />}</label>)}<Button disabled={index === 0} onClick={() => change(moveEntry(entries, index, -1))}>Move earlier</Button><Button disabled={index === entries.length - 1} onClick={() => change(moveEntry(entries, index, 1))}>Move later</Button><Button onClick={() => change(entries.filter((_, i) => i !== index))}>Remove</Button></li>)}</ol><Button disabled={entries.length >= 20} onClick={() => change([...entries, timelineEntry(track)])}>Add {track === 'segments' ? 'clip or still' : track === 'overlays' ? 'image overlay' : 'soundtrack'}</Button></section>
}
const base = '/api/capabilities/media/timelines'
async function api(path: string, method = 'GET', body?: unknown) {
  const response = await fetch(path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || 'Timeline request failed')
  return result
}
export default function TimelinePage() {
  const [items, setItems] = useState<Timeline[]>([]), [selected, setSelected] = useState<Timeline | null>(null), [draft, setDraft] = useState<Draft>(emptyTimeline)
  const [loading, setLoading] = useState(true)
  const [history, setHistory] = useState<Timeline[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false), [job, setJob] = useState('')
  const clean = (value: Timeline): Draft => ({ title: value.title, width: value.width, height: value.height, fps: value.fps, segments: value.segments, overlays: value.overlays, audio: value.audio })
  const dirty = JSON.stringify(draft) !== JSON.stringify(selected ? clean(selected) : emptyTimeline())
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); try { await work() } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  async function choose(id: string) { const value = id ? await api(base + '/' + encodeURIComponent(id)) : null; setSelected(value); setDraft(value ? clean(value) : emptyTimeline()); setHistory(value ? (await api(base + '/' + value.id + '/history')).items : []); setJob('') }
  useEffect(() => { void action(async () => { setItems((await api(base)).items); const id = new URLSearchParams(location.hash.split('?')[1] || '').get('timeline'); if (id) await choose(id) }).finally(() => setLoading(false)) }, [])
  return <main className="p-4 space-y-4"><h1>Video timeline</h1><a href="#/capabilities/media?view=jobs">Media jobs</a><p>Sequence pinned clips and stills, then place image overlays and soundtracks. Segment start trims the source; overlay/audio start is timeline placement. Original clip audio is muted; add an explicit soundtrack to include sound. Output is MP4, up to 300 seconds and 1920 pixels per side.</p>{loading && <p>Loading timelines…</p>}<fieldset disabled={busy || loading}><label>Project<select disabled={busy || dirty} value={selected?.id || ''} onChange={e => void action(() => choose(e.target.value))}><option value="">New timeline</option>{items.map(item => <option key={item.id} value={item.id}>{item.title} · revision {item.revision}</option>)}</select></label><Button disabled={busy || !dirty} onClick={() => setDraft(selected ? clean(selected) : emptyTimeline())}>Discard changes</Button>
    <label>Title<input value={draft.title} maxLength={120} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>{(['width', 'height', 'fps'] as const).map(key => <label key={key}>{key}<input type="number" value={draft[key]} onChange={e => setDraft({ ...draft, [key]: Number(e.target.value) })} /></label>)}
    {(['segments', 'overlays', 'audio'] as const).map(track => <TimelineTrack key={track} track={track} entries={draft[track]} change={entries => setDraft({ ...draft, [track]: entries })} />)}
    <Button disabled={busy || !dirty || !draft.title.trim() || !draft.segments.length} onClick={() => void action(async () => { const saved = await api(base + (selected ? '/' + selected.id : ''), selected ? 'PUT' : 'POST', { ...draft, revision: selected?.revision || 0, request_id: crypto.randomUUID() }); setSelected(saved); setDraft(clean(saved)); setItems((await api(base)).items); setHistory((await api(base + '/' + saved.id + '/history')).items) })}>Save timeline</Button>
    <Button disabled={busy || dirty || !selected} onClick={() => void action(async () => { const queued = await api('/api/capabilities/media/jobs', 'POST', { operation: 'timeline_render', request_id: crypto.randomUUID(), input: { timeline_id: selected!.id, revision: selected!.revision } }); setJob(queued.id) })}>Render saved revision</Button>
    {selected && <a href={'#/capabilities/media?view=timelines&timeline=' + encodeURIComponent(selected.id)}>Project link</a>}{job && <p>Queued {job}. <a href="#/capabilities/media?view=jobs">View progress and result</a></p>}
    <h2>Revision history</h2>{history.map(item => <div key={item.revision}>Revision {item.revision} · {item.duration}s <Button disabled={busy || dirty} onClick={() => setDraft(clean(item))}>Restore as draft</Button></div>)}</fieldset>{error && <p role="alert">{error}</p>}
  </main>
}
