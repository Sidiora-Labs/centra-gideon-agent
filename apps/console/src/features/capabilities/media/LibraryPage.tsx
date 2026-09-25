import NativeMediaPage from './NativeMediaPage'
import { useEffect, useRef, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import Annotations from './Annotations'

export type MediaItem = { id: string; name: string; kind: string; mime: string; version: number; updated_at: string; tags: string[]; collection: string; readonly: boolean; raw_url: string; provenance: Record<string, string | number> }
type Result = { items: MediaItem[]; total: number; offset: number; limit: number; facets: { kinds: Record<string, number>; tags: Record<string, number>; collections: Record<string, number> } }
const base = '/api/capabilities/media/library'

export function MediaCard({ item, onSelect }: { item: MediaItem; onSelect: () => void }) {
  return <article className="rounded-lg bg-surface-container p-l space-y-s min-w-0">
    {item.kind === 'image' ? <img alt={item.name} src={item.raw_url} loading="lazy" className="w-full h-40 object-contain" /> : <video aria-label={item.name} src={item.raw_url} controls preload="metadata" className="w-full h-40" />}
    <Button onClick={onSelect}>{item.name}</Button><p>{item.kind} · v{item.version}</p>
    <p>{item.collection || 'Unfiled'}{item.tags.length ? ' · ' + item.tags.join(', ') : ''}</p>
  </article>
}

export default function LibraryPage() {
  const [result, setResult] = useState<Result | null>(null)
  const [selected, setSelected] = useState<MediaItem | null>(null)
  const [q, setQ] = useState('')
  const [kind, setKind] = useState('')
  const [tag, setTag] = useState('')
  const [collection, setCollection] = useState('')
  const [offset, setOffset] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [tags, setTags] = useState('')
  const [membership, setMembership] = useState('')
  const file = useRef<HTMLInputElement>(null)
  async function json(response: Response) { const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Request failed'); return value }
  function choose(item: MediaItem) { setSelected(item); setName(item.name); setTags(item.tags.join(', ')); setMembership(item.collection) }
  async function action(work: () => Promise<void>) { setBusy(true); setError(''); try { await work() } catch (e) { setError((e as Error).message) } finally { setBusy(false) } }
  useEffect(() => {
    let active = true
    const query = new URLSearchParams({ q, kind, tag, collection, offset: String(offset), limit: '24' })
    fetch(base + '?' + query).then(json).then(value => { if (active) { setResult(value); setError('') } }).catch(e => { if (active) setError(e.message) })
    return () => { active = false }
  }, [q, kind, tag, collection, offset, refresh])
  useEffect(() => {
    let active = true
    const load = () => {
      const id = new URLSearchParams(location.hash.split('?')[1] || '').get('artifact')
      if (!id) { setSelected(null); return }
      fetch(base + '/' + encodeURIComponent(id)).then(json).then(value => { if (active) choose(value) }).catch(e => { if (active) setError(e.message) })
    }
    load(); window.addEventListener('hashchange', load)
    return () => { active = false; window.removeEventListener('hashchange', load) }
  }, [])
  const dirty = selected && (selected.name !== name || selected.tags.join(', ') !== tags || selected.collection !== membership)
  return <NativeMediaPage title="Media library" actions={<a href="#/capabilities/media">Image sketches</a>}>
    {error && <p role="alert">{error}</p>}
    <div className="grid gap-m rounded-lg bg-surface-container p-l sm:grid-cols-2 lg:grid-cols-5">
      <label>Search<input aria-label="Search media" type="search" value={q} onChange={e => { setQ(e.target.value); setOffset(0) }} /></label>
      <label>Kind<select aria-label="Media kind" value={kind} onChange={e => { setKind(e.target.value); setOffset(0) }}><option value="">Images and videos</option><option value="image">Images</option><option value="video">Videos</option></select></label>
      <label>Tag<input aria-label="Filter tag" value={tag} onChange={e => { setTag(e.target.value); setOffset(0) }} list="media-tags" /></label>
      <datalist id="media-tags">{Object.keys(result?.facets.tags || {}).map(value => <option key={value} value={value} />)}</datalist>
      <label>Collection<input aria-label="Filter collection" value={collection} onChange={e => { setCollection(e.target.value); setOffset(0) }} list="media-collections" /></label>
      <datalist id="media-collections">{Object.keys(result?.facets.collections || {}).map(value => <option key={value} value={value} />)}</datalist>
      <label>Import image<input aria-label="Import image" ref={file} type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={() => void action(async () => {
        const image = file.current?.files?.[0]; if (!image) return
        const item = await fetch(base + '/import', { method: 'POST', headers: { 'Content-Type': image.type, 'X-File-Name': encodeURIComponent(image.name), 'X-Request-ID': crypto.randomUUID() }, body: image }).then(json)
        choose(item); setRefresh(value => value + 1); location.hash = '/capabilities/media?view=library&artifact=' + item.id
        if (file.current) file.current.value = ''
      })} /></label>
    </div>
    {!result && !error && <p role="status">Loading media…</p>}
    {result && <><p role="status">{result.total} matching artifacts</p>
      {!result.items.length && <p>No matching media. Import an image or generate media in a conversation.</p>}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">{result.items.map(item => <MediaCard key={item.id} item={item} onSelect={() => { if (!dirty) location.hash = '/capabilities/media?view=library&artifact=' + item.id }} />)}</div>
      <div className="flex gap-3"><Button disabled={!offset || busy} onClick={() => setOffset(Math.max(0, offset - 24))}>Previous</Button><Button disabled={offset + 24 >= result.total || busy} onClick={() => setOffset(offset + 24)}>Next</Button></div>
    </>}
    {selected && <section aria-label="Media details" className="rounded-lg bg-surface-high p-l space-y-m">
      <h2>{selected.name}</h2><a href={selected.raw_url} download>Download original</a>
      <label>Name<input aria-label="Media name" value={name} onChange={e => setName(e.target.value)} maxLength={200} /></label>
      <label>Tags (comma separated)<input aria-label="Media tags" value={tags} onChange={e => setTags(e.target.value)} /></label>
      <label>Collection<input aria-label="Media collection" value={membership} onChange={e => setMembership(e.target.value)} maxLength={200} /></label>
      <Button disabled={!dirty || busy || selected.readonly} onClick={() => void action(async () => {
        const item = await fetch(base + '/' + selected.id, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ expected_updated_at: selected.updated_at, name, tags: tags.split(',').map(value => value.trim()).filter(Boolean), collection: membership }) }).then(json)
        choose(item); setRefresh(value => value + 1)
      })}>Save metadata</Button>
      <Button disabled={!dirty || busy} onClick={() => choose(selected)}>Discard changes</Button>
      {dirty && <p>Unsaved changes. Save or discard before selecting another artifact.</p>}
      {selected.readonly && <p>This artifact is read-only.</p>}
      <dl>{Object.entries(selected.provenance).map(([key, value]) => <div key={key}><dt>{key}</dt><dd className="break-all">{String(value)}</dd></div>)}</dl>
      <Annotations key={selected.id + ':' + selected.version} artifactId={selected.id} version={selected.version} />
    </section>}
  </NativeMediaPage>
}
