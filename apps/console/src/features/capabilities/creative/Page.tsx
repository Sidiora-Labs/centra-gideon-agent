import Series from './Series'
import Stories from './Stories'
import Works from './Works'
import Authors from './Authors'
import Universes from './Universes'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { ListScaffold } from '../../../shared/ui/ListScaffold'
import Moodboards from './Moodboards'
import ManuscriptExports from './ManuscriptExports'
import Production from './Production'
import CreativeDirection from './CreativeDirection'
import { Commissions } from './Commissions'
import { BookOpen, Boxes, Clapperboard, FileArchive, Feather, Images, Library, PenLine, Sparkles, Timer, Users } from 'lucide-react'
import { AreaNavigation, type AreaDestination } from '../AreaNavigation'

export type Ingredient = {
  id: string; type: string; title: string; body: string; tags: string[]; revision: number
  source_refs: { kind: string; id: string }[]; relations: { kind: string; target_id: string }[]
  source_status?: { kind: string; id: string; missing: boolean }[]
}
const kinds = ['character', 'place', 'object', 'theme', 'event', 'concept']
const base = '/api/capabilities/creative/ingredients'
const empty = () => ({ type: 'character', title: '', body: '', tags: '', sources: '', relations: '' })
const control = 'w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
const message = (error: unknown) => error instanceof Error ? error.message : 'Unable to load catalog'
function parseLines(value: string, key: 'id' | 'target_id') {
  return value.split('\n').filter(line => line.trim()).map(line => {
    const at = line.indexOf(':')
    if (at < 1) throw new Error('References need kind:identifier on each line')
    return { kind: line.slice(0, at).trim(), [key]: line.slice(at + 1).trim() }
  })
}

function CatalogPage({ apiRoot = base }: { apiRoot?: string } = {}) {
  const [items, setItems] = useState<Ingredient[]>([])
  const [selected, setSelected] = useState<Ingredient | null>(null)
  const [history, setHistory] = useState<Ingredient[]>([])
  const [draft, setDraft] = useState(empty)
  const [q, setQ] = useState('')
  const [type, setType] = useState('')
  const [tag, setTag] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [recordId, setRecordId] = useState(() => new URLSearchParams(location.hash.split('?')[1]).get('ingredient') || '')

  function accept(item: Ingredient) {
    setSelected(item)
    setDraft({ type: item.type, title: item.title, body: item.body, tags: item.tags.join(', '),
      sources: item.source_refs.map(ref => `${ref.kind}:${ref.id}`).join('\n'),
      relations: item.relations.map(ref => `${ref.kind}:${ref.target_id}`).join('\n') })
  }
  function choose(id: string) {
    location.hash = `/capabilities/creative${id ? `?ingredient=${encodeURIComponent(id)}` : ''}`
    setRecordId(id)
    setError('')
    if (!id) { setSelected(null); setHistory([]); setDraft(empty()); setRequestId(crypto.randomUUID()) }
  }
  useEffect(() => {
    const changed = () => setRecordId(new URLSearchParams(location.hash.split('?')[1]).get('ingredient') || '')
    addEventListener('hashchange', changed)
    return () => removeEventListener('hashchange', changed)
  }, [])
  useEffect(() => {
    let alive = true
    setLoading(true)
    requestJson<{ items: Ingredient[]; total: number }>(`${apiRoot}?${new URLSearchParams({ q, type, tag, offset: String(offset) })}`)
      .then(result => { if (alive) { setItems(result.items); setTotal(result.total) } })
      .catch(e => { if (alive) setError(message(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [q, type, tag, offset, refresh, apiRoot])
  useEffect(() => {
    let alive = true
    if (selected?.id !== recordId) { setSelected(null); setHistory([]); setDraft(empty()) }
    if (!recordId) { setBusy(false); return }
    setBusy(true)
    Promise.all([requestJson<Ingredient>(`${apiRoot}/${encodeURIComponent(recordId)}`),
      requestJson<{ items: Ingredient[] }>(`${apiRoot}/${encodeURIComponent(recordId)}/revisions`)])
      .then(([item, revisions]) => { if (alive) { accept(item); setHistory(revisions.items) } })
      .catch(e => { if (alive) setError(message(e)) })
      .finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [recordId, refresh, apiRoot])

  async function save(targetRevision?: number) {
    setBusy(true); setError('')
    try {
      const payload = { type: draft.type, title: draft.title, body: draft.body,
        tags: draft.tags.split(',').map(t => t.trim()).filter(Boolean),
        source_refs: parseLines(draft.sources, 'id'), relations: parseLines(draft.relations, 'target_id') }
      const result = targetRevision && selected
        ? await requestJson<Ingredient>(`${apiRoot}/${selected.id}/restore`, 'POST', { revision: selected.revision, target_revision: targetRevision })
        : await requestJson<Ingredient>(selected ? `${apiRoot}/${selected.id}` : apiRoot, selected ? 'PATCH' : 'POST',
          selected ? { ...payload, revision: selected.revision } : { ...payload, request_id: requestId })
      accept(result); choose(result.id); setRefresh(n => n + 1)
    } catch (e) { setError(message(e)) }
    finally { setBusy(false) }
  }

  return <ListScaffold title="Creative ingredients" right={<Button onClick={() => choose('')} disabled={busy}>New ingredient</Button>}>
    <main className="space-y-l text-on-surface">
    <p data-type="body-m" className="max-w-[48rem] text-on-surface-low">Keep characters, places and ideas with their sources and revision history.</p>
    {error && <div role="alert" className="border-l-2 border-danger/40 pl-s text-danger">{error} <Button onClick={() => { setError(''); setRefresh(n => n + 1) }}>Reload catalog</Button></div>}
    <section aria-label="Catalog filters" className="grid gap-m rounded-lg bg-surface-container p-l sm:grid-cols-3">
      <label>Search<input className={control} value={q} onChange={e => { setQ(e.target.value); setOffset(0) }} /></label>
      <label>Filter type<select className={control} value={type} onChange={e => { setType(e.target.value); setOffset(0) }}><option value="">All types</option>{kinds.map(k => <option key={k}>{k}</option>)}</select></label>
      <label>Filter tag<input className={control} value={tag} onChange={e => { setTag(e.target.value); setOffset(0) }} /></label>
    </section>
    <div className="grid min-w-0 gap-l lg:grid-cols-[minmax(17rem,24rem)_minmax(0,1fr)]">
      <section aria-label="Ingredient list" className="h-fit space-y-m rounded-lg bg-surface-container p-l">
        {loading ? <p role="status">Loading ingredients…</p> : !items.length ? <p>No ingredients found.</p> : <ul className="space-y-2">{items.map(item => <li key={item.id}>
          <Button variant={selected?.id === item.id ? 'tonal' : 'ghost'} ariaLabel={`${item.title} · ${item.type}`} ariaPressed={selected?.id === item.id} className="h-auto w-full justify-start rounded-xl px-m py-m text-left" onClick={() => choose(item.id)} disabled={busy}><span className="min-w-0"><strong className="block truncate">{item.title}</strong><span className="block text-on-surface-low">{item.type}{item.tags.length ? ` · ${item.tags.slice(0, 3).join(', ')}` : ''}</span></span></Button>
        </li>)}</ul>}
        <p className="text-on-surface-low">{total} ingredients</p>
        <div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" disabled={offset === 0 || loading} onClick={() => setOffset(n => Math.max(0, n - 25))}>Previous</Button>
        <Button size="sm" variant="secondary" disabled={offset + 25 >= total || loading} onClick={() => setOffset(n => n + 25)}>Next</Button></div>
      </section>
      <section aria-label="Ingredient editor" className="min-w-0 space-y-m rounded-lg bg-surface-container p-l">
        <div><h2 data-type="title-s">{selected ? `Edit ingredient · revision ${selected.revision}` : 'New ingredient'}</h2><p data-type="body-s" className="text-on-surface-low">Capture the idea, its canonical sources, and its relationships.</p></div>
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); void save() }}>
          <label className="block">Type<select className={control} value={draft.type} onChange={e => setDraft({ ...draft, type: e.target.value })}>{kinds.map(k => <option key={k}>{k}</option>)}</select></label>
          <label className="block">Title<input required maxLength={200} className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
          <label className="block">Body<textarea rows={6} maxLength={100000} className={control} value={draft.body} onChange={e => setDraft({ ...draft, body: e.target.value })} /></label>
          <label className="block">Tags (comma separated)<input className={control} value={draft.tags} onChange={e => setDraft({ ...draft, tags: e.target.value })} /></label>
          <label className="block">Source references<textarea className={control} placeholder="artifact:slug or knowledge:id, one per line" value={draft.sources} onChange={e => setDraft({ ...draft, sources: e.target.value })} /></label>
          <label className="block">Relations<textarea className={control} placeholder="related:ingredient-id or contains:ingredient-id" value={draft.relations} onChange={e => setDraft({ ...draft, relations: e.target.value })} /></label>
          <Button type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save ingredient'}</Button>
        </form>
        {selected && <p className="break-all text-on-surface-low">ID: {selected.id}</p>}
        {selected?.source_status?.map(ref => <p key={`${ref.kind}:${ref.id}`} className="break-all">{ref.kind}:{ref.id} — {ref.missing ? 'Source missing' : 'Source available'}</p>)}
        {!!history.length && <section aria-label="Revision history"><h3>Revision history</h3><ul>{history.map(item => <li key={item.revision}>
          Revision {item.revision}: {item.title} <Button disabled={busy || item.revision === selected?.revision} onClick={() => void save(item.revision)}>Restore revision {item.revision}</Button>
        </li>)}</ul></section>}
      </section>
    </div>
  </main></ListScaffold>
}

function ProductionView({ apiRoot }: { apiRoot?: string }) {
  const seriesRoot = apiRoot?.replace(/ingredients$/, 'series') || '/api/capabilities/creative/series'
  const id = new URLSearchParams(location.hash.split('?')[1]).get('series') || ''
  const [revision, setRevision] = useState<number>()
  const [error, setError] = useState('')
  useEffect(() => { let alive = true; setRevision(undefined); setError(''); if (id) requestJson<{ revision: number }>(`${seriesRoot}/${id}`).then(row => { if (alive) setRevision(row.revision) }).catch(reason => { if (alive) setError(message(reason)) }); return () => { alive = false } }, [id, seriesRoot])
  if (!id) return <ListScaffold title="Production"><div className="space-y-m"><p data-type="body-m" className="text-on-surface-low">Choose a series before opening production.</p><Button onClick={() => { window.location.hash = "/capabilities/creative?view=series" }}>Choose a series</Button></div></ListScaffold>
  if (error) return <ListScaffold title="Production"><p role="alert">{error}</p></ListScaffold>
  return revision ? <Production id={id} revision={revision} apiRoot={seriesRoot} /> : <ListScaffold title="Production"><p role="status">Loading series production…</p></ListScaffold>
}

export default function Page({ apiRoot }: { apiRoot?: string } = {}) {
  const readView = () => new URLSearchParams(location.hash.split('?')[1]).get('view') || 'ingredients'
  const [view, setView] = useState(readView)
  useEffect(() => { const changed = () => setView(readView()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
  const selectedSeries = new URLSearchParams(location.hash.split('?')[1]).get('series')
  const destinations: AreaDestination[] = [
    { id: 'ingredients', label: 'Ingredients', icon: Library, group: 'Library' },
    { id: 'boards', label: 'Moodboards', icon: Images, group: 'Library' },
    { id: 'universes', label: 'Universes', icon: Boxes, group: 'Library' },
    { id: 'authors', label: 'Authors', icon: Users, group: 'Library' },
    { id: 'works', label: 'Writing', icon: PenLine, group: 'Studio' },
    { id: 'stories', label: 'Stories', icon: BookOpen, group: 'Studio' },
    { id: 'series', label: 'Series', icon: Feather, group: 'Studio' },
    { id: 'production', label: 'Production', icon: Clapperboard, group: 'Studio' },
    { id: 'direction', label: 'Direction', icon: Sparkles, group: 'Studio' },
    { id: 'commissions', label: 'Commissions', icon: Timer, group: 'Studio' },
    { id: 'exports', label: 'Exports', icon: FileArchive, group: 'Studio' },
  ]
  const content = view === 'commissions' ? <Commissions apiRoot={apiRoot?.replace(/ingredients$/, 'commissions')} /> : view === 'direction' ? <CreativeDirection apiRoot={apiRoot?.replace(/ingredients$/, 'direction')} /> : view === 'production' ? <ProductionView apiRoot={apiRoot} /> : view === 'exports' ? <ManuscriptExports apiRoot={apiRoot?.replace(/ingredients$/, 'exports')} /> : view === 'series' ? <Series apiRoot={apiRoot?.replace(/ingredients$/, 'series')} /> : view === 'stories' ? <Stories apiRoot={apiRoot?.replace(/ingredients$/, 'stories')} /> : view === 'works' ? <Works apiRoot={apiRoot?.replace(/ingredients$/, 'works')} /> : view === 'authors' ? <Authors apiRoot={apiRoot?.replace(/ingredients$/, 'authors')} /> : view === 'universes' ? <Universes apiRoot={apiRoot?.replace(/ingredients$/, 'universes')} /> : view === 'boards' ? <Moodboards apiRoot={apiRoot?.replace(/ingredients$/, 'boards')} /> : <CatalogPage apiRoot={apiRoot} />
  const navigate = (next: string) => {
    location.hash = next === 'ingredients' ? '/capabilities/creative'
      : `/capabilities/creative?view=${encodeURIComponent(next)}${next === 'production' && selectedSeries ? `&series=${encodeURIComponent(selectedSeries)}` : ''}`
  }
  return <AreaNavigation label="Creative workspace" items={destinations} active={view} onChange={navigate}>{content}</AreaNavigation>
}
