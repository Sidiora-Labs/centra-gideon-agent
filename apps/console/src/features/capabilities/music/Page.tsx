import DeckPage from './DeckPage'
import ListeningPage from './ListeningPage'
import AssemblyPage from './AssemblyPage'
import Models3DPage from './Models3DPage'
import VideoPage from './VideoPage'
import MidiPage from './MidiPage'
import { useEffect, useRef, useState } from 'react'
import RoundsPage from './RoundsPage'
import GenerationPage from './GenerationPage'
import CatalogPage from './CatalogPage'
import { Button } from '../../../shared/ui/Button'

type Attachment = { slug: string; version: number }
type AttachmentAvailability = Attachment & { available: boolean; name: string; kind: string; mime: string; source: string }
type Attempt = { attempt_id: string; grade: number; occurred_at: string; timezone: string; due_at: string }
type Notation = { format: 'chordpro' | 'tab' | 'plain' | 'drum'; text: string }
type SongLink = { type: string; id: string; label: string }
type Item = { id: string; title: string; artist: string; instrument: string; body: string; tags: string[]; key: string; capo: number; tuning: string; notation: Notation; source_url: string; links: SongLink[]; scroll_duration_seconds: number | null; attachment_refs: Attachment[]; attachment_availability: AttachmentAvailability[]; stage: string; due_at: string | null; revision: number; practice_history: Attempt[] }
const selected = () => window.location.hash.split('/music/')[1]?.split('?')[0] || ''
const fieldClass = 'w-full rounded-lg border border-outline bg-surface p-2 text-on-surface'

function RepertoirePage({ apiBase = '/api/capabilities/music' }: { apiBase?: string }) {
  const [items, setItems] = useState<Item[]>([])
  const [id, setId] = useState(selected)
  const [item, setItem] = useState<Item | null>(null)
  const [title, setTitle] = useState('')
  const [artist, setArtist] = useState('')
  const [instrument, setInstrument] = useState('guitar')
  const [body, setBody] = useState('')
  const [tags, setTags] = useState('')
  const [songKey, setSongKey] = useState('')
  const [capo, setCapo] = useState(0)
  const [tuning, setTuning] = useState('')
  const [notationFormat, setNotationFormat] = useState<Notation['format']>('plain')
  const [notationText, setNotationText] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [links, setLinks] = useState('[]')
  const [scrollDuration, setScrollDuration] = useState('')
  const [refs, setRefs] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [grade, setGrade] = useState(3)
  const [zone, setZone] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  const pending = useRef<Record<string, unknown> | null>(null)
  async function request(path: string, method = 'GET', data?: unknown) {
    const response = await fetch(apiBase + path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data) })
    const value = await response.json()
    if (!response.ok) throw new Error(value.message || value.error || 'Request failed')
    return value
  }
  function show(next: Item) {
    setItem(next); setTitle(next.title); setArtist(next.artist); setInstrument(next.instrument); setBody(next.body)
    setTags(next.tags.join(', ')); setSongKey(next.key); setCapo(next.capo); setTuning(next.tuning)
    setNotationFormat(next.notation.format); setNotationText(next.notation.text); setSourceUrl(next.source_url)
    setLinks(JSON.stringify(next.links, null, 2)); setScrollDuration(next.scroll_duration_seconds === null ? '' : String(next.scroll_duration_seconds))
    setRefs(next.attachment_refs.map(ref => `${ref.slug}@${ref.version}`).join('\n'))
    setItems(previous => [...previous.filter(row => row.id !== next.id), next])
  }
  useEffect(() => {
    const changed = () => setId(selected())
    window.addEventListener('hashchange', changed)
    return () => window.removeEventListener('hashchange', changed)
  }, [])
  useEffect(() => {
    if (id && item?.id === id) return
    let active = true
    setItem(null)
    setLoading(true); setError(''); pending.current = null
    request(id ? `/items/${encodeURIComponent(id)}` : '/items?limit=100').then(value => {
      if (!active) return
      if (id) show(value.item)
      else { setItems(value.items); setItem(null); setTitle(''); setArtist(''); setInstrument('guitar'); setBody(''); setTags(''); setSongKey(''); setCapo(0); setTuning(''); setNotationFormat('plain'); setNotationText(''); setSourceUrl(''); setLinks('[]'); setScrollDuration(''); setRefs('') }
    }).catch(err => { if (active) setError(String(err.message)) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, apiBase])
  function open(next: string) { window.location.hash = `#/capabilities/music${next ? '/' + next : ''}`; setId(next) }
  async function save() {
    setBusy(true); setError('')
    try {
      const attachment_refs = refs.trim() ? refs.trim().split('\n').map(line => {
        const split = line.lastIndexOf('@')
        if (split < 1) throw new Error('Attachments use slug@version, one per line')
        return { slug: line.slice(0, split), version: Number(line.slice(split + 1)) }
      }) : []
      const related = JSON.parse(links)
      if (!Array.isArray(related)) throw new Error('Related records must be a JSON array')
      const value = await request(item ? `/items/${item.id}` : '/items', item ? 'PATCH' : 'POST', {
        title, artist, instrument, body, tags: tags.split(',').map(tag => tag.trim()).filter(Boolean), key: songKey,
        capo, tuning, notation: { format: notationFormat, text: notationText }, source_url: sourceUrl,
        links: related, scroll_duration_seconds: scrollDuration === '' ? null : Number(scrollDuration),
        attachment_refs, ...(item ? { revision: item.revision } : {})
      })
      show(value.item); open(value.item.id)
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  async function practice() {
    if (!item) return
    setBusy(true); setError('')
    pending.current ||= { attempt_id: crypto.randomUUID(), grade, timezone: zone, occurred_at: new Date().toISOString(), revision: item.revision }
    try {
      const value = await request(`/items/${item.id}/practice`, 'POST', pending.current)
      show(value.item); pending.current = null
    } catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  return <section className="mx-auto flex w-full max-w-4xl flex-col gap-4 overflow-auto p-4 text-on-surface">
    <h1 className="text-xl">Repertoire and practice</h1>
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Loading repertoire…</p> : <>
      {!id && <nav aria-label="Repertoire">{items.length ? items.map(row => <a className="block p-2 text-primary" key={row.id} href={`#/capabilities/music/${row.id}`} onClick={() => setId(row.id)}>{row.title} · {row.stage}</a>) : <p>No repertoire yet. Add your first piece.</p>}</nav>}
      {id && <Button variant="secondary" onClick={() => open('')}>All repertoire</Button>}
      <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); void save() }}>
        <label>Title<input className={fieldClass} required maxLength={300} value={title} onChange={event => setTitle(event.target.value)} /></label>
        <label>Artist<input className={fieldClass} maxLength={300} value={artist} onChange={event => setArtist(event.target.value)} /></label>
        <label>Instrument<select className={fieldClass} value={instrument} onChange={event => setInstrument(event.target.value)}>{['guitar', 'piano', 'ukulele', 'bass', 'voice', 'drums', 'other'].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Tags<input className={fieldClass} placeholder="folk, recital" value={tags} onChange={event => setTags(event.target.value)} /></label>
        <div className="grid gap-3 sm:grid-cols-3">
          <label>Song key<input className={fieldClass} maxLength={20} value={songKey} onChange={event => setSongKey(event.target.value)} /></label>
          <label>Capo<input className={fieldClass} type="number" min={0} max={12} value={capo} onChange={event => setCapo(Number(event.target.value))} /></label>
          <label>Tuning<input className={fieldClass} maxLength={40} value={tuning} onChange={event => setTuning(event.target.value)} /></label>
        </div>
        <label>Notation format<select className={fieldClass} value={notationFormat} onChange={event => setNotationFormat(event.target.value as Notation['format'])}>{['chordpro', 'tab', 'plain', 'drum'].map(value => <option key={value}>{value}</option>)}</select></label>
        <label>Notation<textarea className={`${fieldClass} font-mono`} rows={10} maxLength={200000} value={notationText} onChange={event => setNotationText(event.target.value)} /></label>
        <label>Practice notes<textarea className={fieldClass} rows={4} maxLength={100000} value={body} onChange={event => setBody(event.target.value)} /></label>
        <label>Source URL<input className={fieldClass} type="url" maxLength={2000} value={sourceUrl} onChange={event => setSourceUrl(event.target.value)} /></label>
        <label>Related records JSON<textarea className={`${fieldClass} font-mono`} rows={4} value={links} onChange={event => setLinks(event.target.value)} /></label>
        <label>Scroll duration seconds<input className={fieldClass} type="number" min={15} max={3600} value={scrollDuration} onChange={event => setScrollDuration(event.target.value)} /></label>
        <label>Artifact attachments (slug@version)<textarea className={fieldClass} value={refs} onChange={event => setRefs(event.target.value)} /></label>
        <Button type="submit" disabled={busy || !title.trim()}>{item ? 'Save changes' : 'Add piece'}</Button>
      </form>
      {item && <article aria-label="Practice reader" className="flex flex-col gap-3">
        <h2>{item.title}</h2><p>{item.artist || 'Unknown artist'} · {item.instrument}{item.key ? ` · ${item.key}` : ''}{item.capo ? ` · capo ${item.capo}` : ''}{item.tuning ? ` · ${item.tuning}` : ''}</p>
        {item.notation.text && <pre aria-label="Song notation" className="overflow-x-auto whitespace-pre font-mono">{item.notation.text}</pre>}
        {item.body && <p className="whitespace-pre-wrap break-words">{item.body}</p>}
        {item.source_url && <a className="text-primary" href={item.source_url} target="_blank" rel="noreferrer">Original source</a>}
        {item.links.length > 0 && <ul aria-label="Related music records">{item.links.map(link => <li key={`${link.type}:${link.id}`}>{link.label || link.id} · {link.type}</li>)}</ul>}
        {item.scroll_duration_seconds !== null && <p>Scroll target: {item.scroll_duration_seconds} seconds</p>}
        {item.attachment_availability.map(ref => ref.available
          ? <a key={`${ref.slug}@${ref.version}`} className="text-primary" href={`/api/artifacts/${encodeURIComponent(ref.slug)}/versions/${ref.version}`} target="_blank" rel="noreferrer">{ref.name || ref.slug} · {ref.kind || ref.mime} · version {ref.version}</a>
          : <p key={`${ref.slug}@${ref.version}`}>{ref.slug} version {ref.version} is not available in this workspace</p>)}
        <p>Stage: {item.stage}. Next practice: {item.due_at ? new Date(item.due_at).toLocaleString() : 'Not scheduled'}</p>
        <label>Practice grade<select className={fieldClass} value={grade} onChange={event => setGrade(Number(event.target.value))}>{['0 — No recall', '1 — Incorrect', '2 — Difficult recall', '3 — Correct with effort', '4 — Correct', '5 — Easy'].map((label, index) => <option key={index} value={index}>{label}</option>)}</select></label>
        <label>Practice timezone<input className={fieldClass} value={zone} onChange={event => setZone(event.target.value)} /></label>
        <Button disabled={busy} onClick={() => void practice()}>{pending.current ? 'Retry practice submission' : 'Log practice'}</Button>
        <ol aria-label="Practice history">{item.practice_history.map(attempt => <li key={attempt.attempt_id}>Grade {attempt.grade} · {attempt.occurred_at} · {attempt.timezone}</li>)}</ol>
      </article>}
    </>}
  </section>
}

export default function Page(props: { apiBase?: string }) {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => { const changed = () => setHash(window.location.hash); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed) }, [])
  if (hash.includes('/music/decks')) return <DeckPage />
  if (hash.includes('/music/listening')) return <ListeningPage />
  if (hash.includes('/music/assemblies')) return <AssemblyPage />
  if (hash.includes('/music/models3d')) return <Models3DPage />
  if (hash.includes('/music/videos')) return <VideoPage />
  if (hash.includes('/music/midi')) return <MidiPage />
  if (hash.includes('/music/rounds')) return <RoundsPage />
  if (hash.includes('/music/generation')) return <GenerationPage />
  return hash.includes('/music/catalog') ? <CatalogPage /> : <><a className="p-4 text-primary" href="#/capabilities/music/catalog/tracks">Music catalog</a><a className="p-4 text-primary" href="#/capabilities/music/generation">Music generation</a><a className="p-4 text-primary" href="#/capabilities/music/rounds">Musical canons</a><a className="p-4 text-primary" href="#/capabilities/music/midi">Audio to MIDI</a><a className="p-4 text-primary" href="#/capabilities/music/videos">Music videos</a><a className="p-4 text-primary" href="#/capabilities/music/models3d">Image to 3D</a><a className="p-4 text-primary" href="#/capabilities/music/assemblies">Procedural assemblies</a><a className="p-4 text-primary" href="#/capabilities/music/listening">Listening history</a><a className="p-4 text-primary" href="#/capabilities/music/decks">Card decks</a><RepertoirePage {...props} /></>
}
