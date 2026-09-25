import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'

type Render = { id: string; artifact_ref: { slug: string; version: number }; duration_seconds: number; source: { kind: string; label: string; license: string; model: string | null } }
type RecordItem = { id: string; revision: number; name?: string; title?: string; bio?: string; notes?: string; artist_id?: string; track_ids?: string[]; archived: boolean; renders?: Render[]; selected_render_id?: string }
const route = () => window.location.hash.split('/catalog/')[1]?.split('/') || ['tracks']
const inputClass = 'w-full rounded-lg border border-outline bg-surface p-2 text-on-surface'

export default function CatalogPage({ apiBase = '/api/capabilities/music/catalog' }: { apiBase?: string }) {
  const [parts, setParts] = useState(route)
  const kind = ['artists', 'albums', 'tracks'].includes(parts[0]) ? parts[0] : 'tracks'
  const id = parts[1] || ''
  const [rows, setRows] = useState<RecordItem[]>([])
  const [item, setItem] = useState<RecordItem | null>(null)
  const [label, setLabel] = useState('')
  const [details, setDetails] = useState('')
  const [artist, setArtist] = useState('')
  const [tracks, setTracks] = useState('')
  const [q, setQ] = useState('')
  const [archived, setArchived] = useState(false)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [slug, setSlug] = useState('')
  const [version, setVersion] = useState(1)
  const [source, setSource] = useState('')
  const [license, setLicense] = useState('')
  function open(collection: string, selected = '') { window.location.hash = `#/capabilities/music/catalog/${collection}${selected ? '/' + selected : ''}`; setParts([collection, selected]); setOffset(0) }
  async function request(path: string, method = 'GET', body?: unknown) {
    const response = await fetch(apiBase + path, { method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.message || data.error || 'Catalog request failed')
    return data
  }
  function show(value: RecordItem) { setItem(value); setLabel(value.name || value.title || ''); setDetails(value.bio || value.notes || ''); setArtist(value.artist_id || ''); setTracks((value.track_ids || []).join('\n')) }
  useEffect(() => { const changed = () => setParts(route()); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed) }, [])
  useEffect(() => {
    if (id && item?.id === id) return
    let active = true
    setLoading(true); setError(''); setItem(null)
    request(id ? `/${kind}/${id}` : `/${kind}?q=${encodeURIComponent(q)}&archived=${archived}&offset=${offset}&limit=25`).then(data => {
      if (!active) return
      if (id) show(data.item)
      else { setRows(data.items); setLabel(''); setDetails(''); setArtist(''); setTracks('') }
    }).catch(err => { if (active) setError(err.message) }).finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [kind, id, q, archived, offset, apiBase])
  async function mutate(path: string, method: string, data: unknown) {
    setBusy(true); setError('')
    try { const result = await request(path, method, data); show(result.item); if (!id) open(kind, result.item.id) }
    catch (err) { setError((err as Error).message) } finally { setBusy(false) }
  }
  function save() {
    const data = kind === 'artists' ? { name: label, bio: details } : kind === 'albums' ? { title: label, artist_id: artist, track_ids: tracks.split('\n').map(value => value.trim()).filter(Boolean) } : { title: label, artist_id: artist, notes: details }
    void mutate(`/${kind}${id ? '/' + id : ''}`, id ? 'PATCH' : 'POST', { ...data, ...(item ? { revision: item.revision } : {}) })
  }
  return <section className="mx-auto flex w-full max-w-4xl flex-col gap-4 overflow-auto p-4 text-on-surface">
    <h1>Music catalog</h1><a className="text-primary" href="#/capabilities/music">Repertoire</a>
    <nav aria-label="Music collections" className="flex flex-wrap gap-2">{['artists', 'albums', 'tracks'].map(collection => <Button key={collection} variant={kind === collection ? 'primary' : 'secondary'} onClick={() => open(collection)}>{collection}</Button>)}</nav>
    {error && <p role="alert">{error}</p>}
    {!id && <><label>Filter catalog<input className={inputClass} value={q} onChange={event => { setQ(event.target.value); setOffset(0) }} /></label><label><input type="checkbox" checked={archived} onChange={event => { setArchived(event.target.checked); setOffset(0) }} /> Archived records</label></>}
    {loading ? <p role="status">Loading catalog…</p> : <>
      {!id && <><ul>{rows.map(row => <li key={row.id}><a className="text-primary" href={`#/capabilities/music/catalog/${kind}/${row.id}`} onClick={() => setParts([kind, row.id])}>{row.name || row.title}</a> <span>{row.id}</span></li>)}</ul>{rows.length === 0 && <p>No matching records.</p>}<div className="flex gap-2"><Button disabled={!offset} onClick={() => setOffset(value => Math.max(0, value - 25))}>Previous</Button><Button disabled={rows.length < 25} onClick={() => setOffset(value => value + 25)}>Next</Button></div></>}
      {(!id || item) && <form className="flex flex-col gap-3" onSubmit={event => { event.preventDefault(); save() }}>
        <label>{kind === 'artists' ? 'Artist name' : 'Title'}<input className={inputClass} value={label} maxLength={200} required onChange={event => setLabel(event.target.value)} /></label>
        {kind !== 'artists' && <label>Artist ID (optional)<input className={inputClass} value={artist} onChange={event => setArtist(event.target.value)} /></label>}
        {kind === 'albums' ? <label>Ordered track IDs (one per line)<textarea className={inputClass} value={tracks} onChange={event => setTracks(event.target.value)} /></label> : <label>{kind === 'artists' ? 'Biography' : 'Notes'}<textarea className={inputClass} value={details} onChange={event => setDetails(event.target.value)} /></label>}
        <Button type="submit" disabled={busy || !label.trim()}>{item ? 'Save metadata' : 'Create record'}</Button>
      </form>}
      {item && <><p>Record ID: {item.id}</p><Button variant="secondary" disabled={busy} onClick={() => void mutate(`/${kind}/${id}`, 'PATCH', { revision: item.revision, archived: !item.archived })}>{item.archived ? 'Restore record' : 'Archive record'}</Button></>}
      {item && kind === 'tracks' && <article aria-label="Audio renders" className="flex flex-col gap-3">
        <h2>Audio renders</h2><p>Attach existing PCM WAV artifacts. Generation provenance is unavailable until a verified generation job adapter is connected.</p>
        <label>Audio artifact slug<input className={inputClass} value={slug} onChange={event => setSlug(event.target.value)} /></label>
        <label>Artifact version<input className={inputClass} type="number" min={1} value={version} onChange={event => setVersion(Number(event.target.value))} /></label>
        <label>Source attribution<input className={inputClass} value={source} onChange={event => setSource(event.target.value)} /></label>
        <label>License or rights statement<input className={inputClass} value={license} onChange={event => setLicense(event.target.value)} /></label>
        <Button disabled={busy || !slug || !source || !license} onClick={() => void mutate(`/tracks/${id}/renders`, 'POST', { revision: item.revision, artifact_ref: { slug, version }, source: { kind: 'imported', label: source, license } })}>Attach recording</Button>
        {(item.renders || []).map(render => <div key={render.id} className="rounded-lg border border-outline p-3">
          <p>{render.artifact_ref.slug} · {render.duration_seconds.toFixed(3)} seconds · {render.source.kind}</p><p>{render.source.label} · {render.source.license} (user supplied)</p>
          <audio aria-label={`Play ${render.artifact_ref.slug}`} controls src={`/api/artifacts/${encodeURIComponent(render.artifact_ref.slug)}/raw?version=${render.artifact_ref.version}`} />
          <Button disabled={busy || item.selected_render_id === render.id} onClick={() => void mutate(`/tracks/${id}/select`, 'POST', { revision: item.revision, render_id: render.id })}>{item.selected_render_id === render.id ? 'Selected render' : 'Select render'}</Button>
        </div>)}
      </article>}
    </>}
  </section>
}
