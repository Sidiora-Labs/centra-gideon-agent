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
import {Album,Boxes,Disc3,Layers3,ListMusic,Music2,Piano,Video,Waves} from 'lucide-react'
import {AreaNavigation} from '../AreaNavigation'
import {ListScaffold} from '../../../shared/ui/ListScaffold'
import {Field,NumberField,Select,TextArea,TextInput} from '../../../shared/ui/forms'
import {Surface} from '../../../shared/ui/Surface'

type Attachment = { slug: string; version: number }
type AttachmentAvailability = Attachment & { available: boolean; name: string; kind: string; mime: string; source: string }
type Attempt = { attempt_id: string; grade: number; occurred_at: string; timezone: string; due_at: string }
type Notation = { format: 'chordpro' | 'tab' | 'plain' | 'drum'; text: string }
type SongLink = { type: string; id: string; label: string }
type Item = { id: string; title: string; artist: string; instrument: string; body: string; tags: string[]; key: string; capo: number; tuning: string; notation: Notation; source_url: string; links: SongLink[]; scroll_duration_seconds: number | null; attachment_refs: Attachment[]; attachment_availability: AttachmentAvailability[]; stage: string; due_at: string | null; revision: number; practice_history: Attempt[] }
const selected = () => window.location.hash.split('/music/')[1]?.split('?')[0] || ''
const urlClass='h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors placeholder:text-on-surface-low focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'

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
  return <ListScaffold title="Repertoire and practice" bodyClassName="mx-auto flex w-full max-w-4xl flex-col gap-l px-l py-xl">
    {error && <p role="alert">{error}</p>}
    {loading ? <p role="status">Loading repertoire…</p> : <>
      {!id && <nav aria-label="Repertoire" className="grid gap-s sm:grid-cols-2">{items.length ? items.map(row => <a className="rounded-lg border border-outline-variant/25 bg-surface-container/60 p-l text-on-surface transition-colors hover:bg-surface-high" key={row.id} href={`#/capabilities/music/${row.id}`} onClick={() => setId(row.id)}><strong data-type="label-m" className="block">{row.title}</strong><span data-type="body-s" className="text-on-surface-low">{row.stage}</span></a>) : <p className="text-on-surface-low">No repertoire yet. Add your first piece.</p>}</nav>}
      {id && <Button variant="secondary" onClick={() => open('')}>All repertoire</Button>}
      <Surface className="p-l"><form className="grid gap-m sm:grid-cols-2" onSubmit={event => { event.preventDefault(); void save() }}>
        <Field label="Title"><TextInput required maxLength={300} value={title} onChange={setTitle}/></Field>
        <Field label="Artist"><TextInput maxLength={300} value={artist} onChange={setArtist}/></Field>
        <Field label="Instrument"><Select value={instrument} onChange={setInstrument} options={['guitar','piano','ukulele','bass','voice','drums','other'].map(value=>({value,label:value}))}/></Field>
        <Field label="Tags"><TextInput placeholder="folk, recital" value={tags} onChange={setTags}/></Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Song key"><TextInput maxLength={20} value={songKey} onChange={setSongKey}/></Field>
          <Field label="Capo"><NumberField width="w-full" min={0} max={12} value={capo} onChange={setCapo}/></Field>
          <Field label="Tuning"><TextInput maxLength={40} value={tuning} onChange={setTuning}/></Field>
        </div>
        <Field label="Notation format"><Select value={notationFormat} onChange={value=>setNotationFormat(value as Notation['format'])} options={['chordpro','tab','plain','drum'].map(value=>({value,label:value}))}/></Field>
        <div className="sm:col-span-2"><Field label="Notation"><TextArea mono rows={10} value={notationText} onChange={value=>setNotationText(value.slice(0,200000))}/></Field></div>
        <div className="sm:col-span-2"><Field label="Practice notes"><TextArea rows={4} value={body} onChange={value=>setBody(value.slice(0,100000))}/></Field></div>
        <label className="min-w-0"><span data-type="caption" className="mb-1.5 block uppercase tracking-wide text-on-surface-low">Source URL</span><input className={urlClass} type="url" maxLength={2000} value={sourceUrl} onChange={event=>setSourceUrl(event.target.value)}/></label>
        <Field label="Scroll duration seconds"><TextInput type="number" min={15} max={3600} value={scrollDuration} onChange={setScrollDuration}/></Field>
        <div className="sm:col-span-2"><Field label="Related records JSON"><TextArea mono rows={4} value={links} onChange={setLinks}/></Field></div>
        <div className="sm:col-span-2"><Field label="Artifact attachments (slug@version)"><TextArea mono value={refs} onChange={setRefs}/></Field></div>
        <Button type="submit" disabled={busy||!title.trim()}>{item?'Save changes':'Add piece'}</Button>
      </form></Surface>
      {item && <article aria-label="Practice reader" className="flex flex-col gap-3">
        <h2 data-type="title-m">{item.title}</h2><p data-type="body-s" className="text-on-surface-low">{item.artist || 'Unknown artist'} · {item.instrument}{item.key ? ` · ${item.key}` : ''}{item.capo ? ` · capo ${item.capo}` : ''}{item.tuning ? ` · ${item.tuning}` : ''}</p>
        {item.notation.text && <pre aria-label="Song notation" className="overflow-x-auto whitespace-pre font-mono">{item.notation.text}</pre>}
        {item.body && <p className="whitespace-pre-wrap break-words">{item.body}</p>}
        {item.source_url && <a className="text-primary" href={item.source_url} target="_blank" rel="noreferrer">Original source</a>}
        {item.links.length > 0 && <ul aria-label="Related music records">{item.links.map(link => <li key={`${link.type}:${link.id}`}>{link.label || link.id} · {link.type}</li>)}</ul>}
        {item.scroll_duration_seconds !== null && <p>Scroll target: {item.scroll_duration_seconds} seconds</p>}
        {item.attachment_availability.map(ref => ref.available
          ? <a key={`${ref.slug}@${ref.version}`} className="text-primary" href={`/api/artifacts/${encodeURIComponent(ref.slug)}/versions/${ref.version}`} target="_blank" rel="noreferrer">{ref.name || ref.slug} · {ref.kind || ref.mime} · version {ref.version}</a>
          : <p key={`${ref.slug}@${ref.version}`}>{ref.slug} version {ref.version} is not available in this workspace</p>)}
        <p>Stage: {item.stage}. Next practice: {item.due_at ? new Date(item.due_at).toLocaleString() : 'Not scheduled'}</p>
        <Field label="Practice grade"><Select value={String(grade)} onChange={value=>setGrade(Number(value))} options={['0 — No recall','1 — Incorrect','2 — Difficult recall','3 — Correct with effort','4 — Correct','5 — Easy'].map((label,index)=>({value:String(index),label}))}/></Field>
        <Field label="Practice timezone"><TextInput value={zone} onChange={setZone}/></Field>
        <Button disabled={busy} onClick={() => void practice()}>{pending.current ? 'Retry practice submission' : 'Log practice'}</Button>
        <ol aria-label="Practice history">{item.practice_history.map(attempt => <li key={attempt.attempt_id}>Grade {attempt.grade} · {attempt.occurred_at} · {attempt.timezone}</li>)}</ol>
      </article>}
    </>}
  </ListScaffold>
}

export default function Page(props: { apiBase?: string }) {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => { const changed = () => setHash(window.location.hash); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed) }, [])
  const path=hash.split('/music/')[1]?.split('?')[0]||''
  const active=path.startsWith('catalog')?'catalog':['generation','rounds','midi','videos','models3d','assemblies','listening','decks'].includes(path)?path:'repertoire'
  const items=[{id:'repertoire',label:'Repertoire',icon:Music2,group:'Library'},{id:'catalog',label:'Catalog',icon:Disc3,group:'Library'},{id:'listening',label:'Listening',icon:Waves,group:'Library'},{id:'generation',label:'Generation',icon:Music2,group:'Studios'},{id:'decks',label:'Card decks',icon:Album,group:'Studios'},{id:'rounds',label:'Canons',icon:ListMusic,group:'Studios'},{id:'midi',label:'Audio to MIDI',icon:Piano,group:'Studios'},{id:'videos',label:'Music videos',icon:Video,group:'Visuals'},{id:'models3d',label:'Image to 3D',icon:Boxes,group:'Visuals'},{id:'assemblies',label:'Assemblies',icon:Layers3,group:'Visuals'}]
  const content=active==='decks'?<DeckPage/>:active==='listening'?<ListeningPage/>:active==='assemblies'?<AssemblyPage/>:active==='models3d'?<Models3DPage/>:active==='videos'?<VideoPage/>:active==='midi'?<MidiPage/>:active==='rounds'?<RoundsPage/>:active==='generation'?<GenerationPage/>:active==='catalog'?<CatalogPage/>:<RepertoirePage {...props}/>
  return <AreaNavigation label="Music" items={items} active={active} onChange={view=>{window.location.hash=view==='repertoire'?'/capabilities/music':view==='catalog'?'/capabilities/music/catalog/tracks':`/capabilities/music/${view}`}}>{content}</AreaNavigation>
}
