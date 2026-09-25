import { useEffect, useRef, useState } from 'react'
import RoundsPage from './RoundsPage'
import GenerationPage from './GenerationPage'
import CatalogPage from './CatalogPage'
import { Button } from '../../../shared/ui/Button'

type Attachment = { slug: string; version: number }
type Attempt = { attempt_id: string; grade: number; occurred_at: string; timezone: string; due_at: string }
type Item = { id: string; title: string; instrument: string; body: string; attachment_refs: Attachment[]; stage: string; due_at: string | null; revision: number; practice_history: Attempt[] }
const selected = () => window.location.hash.split('/music/')[1]?.split('?')[0] || ''
const fieldClass = 'w-full rounded-lg border border-outline bg-surface p-2 text-on-surface'

function RepertoirePage({ apiBase = '/api/capabilities/music' }: { apiBase?: string }) {
  const [items, setItems] = useState<Item[]>([])
  const [id, setId] = useState(selected)
  const [item, setItem] = useState<Item | null>(null)
  const [title, setTitle] = useState('')
  const [instrument, setInstrument] = useState('')
  const [body, setBody] = useState('')
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
    setItem(next); setTitle(next.title); setInstrument(next.instrument); setBody(next.body)
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
      else { setItems(value.items); setItem(null); setTitle(''); setInstrument(''); setBody(''); setRefs('') }
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
      const value = await request(item ? `/items/${item.id}` : '/items', item ? 'PATCH' : 'POST', { title, instrument, body, attachment_refs, ...(item ? { revision: item.revision } : {}) })
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
        <label>Title<input className={fieldClass} required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} /></label>
        <label>Instrument<input className={fieldClass} maxLength={100} value={instrument} onChange={event => setInstrument(event.target.value)} /></label>
        <label>Score or practice notes<textarea className={fieldClass} rows={8} maxLength={100000} value={body} onChange={event => setBody(event.target.value)} /></label>
        <label>Artifact attachments (slug@version)<textarea className={fieldClass} value={refs} onChange={event => setRefs(event.target.value)} /></label>
        <Button type="submit" disabled={busy || !title.trim()}>{item ? 'Save changes' : 'Add piece'}</Button>
      </form>
      {item && <article aria-label="Practice reader" className="flex flex-col gap-3">
        <h2>{item.title}</h2><pre className="whitespace-pre-wrap break-words font-sans">{item.body}</pre>
        {item.attachment_refs.map(ref => <a key={`${ref.slug}@${ref.version}`} className="text-primary" href={`/api/artifacts/${encodeURIComponent(ref.slug)}/versions/${ref.version}`} target="_blank" rel="noreferrer">{ref.slug} version {ref.version}</a>)}
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
  if (hash.includes('/music/rounds')) return <RoundsPage />
  if (hash.includes('/music/generation')) return <GenerationPage />
  return hash.includes('/music/catalog') ? <CatalogPage /> : <><a className="p-4 text-primary" href="#/capabilities/music/catalog/tracks">Music catalog</a><a className="p-4 text-primary" href="#/capabilities/music/generation">Music generation</a><a className="p-4 text-primary" href="#/capabilities/music/rounds">Musical canons</a><RepertoirePage {...props} /></>
}
