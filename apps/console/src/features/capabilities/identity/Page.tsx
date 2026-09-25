import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { TopBar } from '../../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../../shared/ui/WorkbenchLayout'
import { Surface } from '../../../shared/ui/Surface'
import { EmptyState, ListRow, ListSkeleton } from '../../../shared/ui/ListScaffold'
import { HeaderActions, HeaderControl } from '../../../shared/ui/HeaderActions'
import { BookOpen, Download, Pencil, Plus, RefreshCw } from 'lucide-react'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Story = {
  id: string; prompt: string; theme: string; text: string; parent_id: string | null
  created_at: string; updated_at: string; revision: number
}
const empty = { prompt: '', theme: '', text: '', parent_id: null as string | null }
const selectedId = () => new URLSearchParams(window.location.hash.split('?')[1] || '').get('story')

export default function Page({ endpoint = '/api/capabilities/identity' }: { endpoint?: string }) {
  const [stories, setStories] = useState<Story[]>([])
  const [selected, setSelected] = useState<string | null>(selectedId)
  const [mode, setMode] = useState<'idle' | 'read' | 'create' | 'edit'>(() => selectedId() ? 'read' : 'idle')
  const [chain, setChain] = useState<Story[]>([])
  const [draft, setDraft] = useState(empty)
  const [revision, setRevision] = useState(0)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const call = async <T,>(path: string, method = 'GET', body?: unknown): Promise<T> =>
    readJson<T>(await fetch(endpoint + path, { method, headers: { ...gatewayHeaders, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) }))
  const select = (id: string | null) => {
    if (id !== selected) setLoading(true)
    window.location.hash = '#/capabilities/identity' + (id ? '?story=' + encodeURIComponent(id) : '')
    setSelected(id)
    setMode(id ? 'read' : 'idle')
  }
  const load = async () => {
    setLoading(true)
    try {
      const rows = await call<Story[]>('/stories')
      setStories(rows)
      if (selected) {
        const [story, family] = await Promise.all([call<Story>('/stories/' + selected), call<Story[]>('/stories/' + selected + '/chain')])
        setDraft({ prompt: story.prompt, theme: story.theme, text: story.text, parent_id: story.parent_id })
        setRevision(story.revision)
        setChain(family)
      } else { setChain([]); setRevision(0) }
      setError('')
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setLoading(false) }
  }
  useEffect(() => { const change = () => setSelected(selectedId()); window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change) }, [])
  useEffect(() => { void load() }, [selected, endpoint])
  const save = async () => {
    setBusy(true); setError('')
    try {
      const story = await call<Story>(selected ? '/stories/' + selected : '/stories', selected ? 'PUT' : 'POST',
        { ...draft, ...(selected ? { expected_revision: revision } : { request_id: requestId }) })
      setRequestId(crypto.randomUUID()); setRevision(story.revision)
      if (selected === story.id) { await load(); setMode('read') }
      else select(story.id)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const remove = async () => {
    setBusy(true); setError('')
    try { await call('/stories/' + selected + '?expected_revision=' + revision, 'DELETE'); setDraft(empty); select(null) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  const newStory = (parent_id: string | null = null) => {
    setDraft({ ...empty, parent_id }); setRevision(0); setRequestId(crypto.randomUUID()); select(null); setMode('create')
  }
  const current = stories.find(story => story.id === selected)
  return <WorkbenchLayout topBar={<TopBar keepCornerPadding left={<PageTitle>Your life stories</PageTitle>} right={<HeaderActions><HeaderControl icon={RefreshCw} label="Reload stories" disabled={busy} onClick={() => void load()} /><HeaderControl icon={Plus} label="New story" variant="primary" priority="primary" disabled={busy} onClick={() => newStory()} /></HeaderActions>} />}>
  <main className="mx-auto grid w-full max-w-[72rem] gap-l px-l py-2xl text-on-surface lg:grid-cols-[minmax(260px,0.8fr)_minmax(0,1.4fr)]">
    <section className="space-y-m" aria-label="Life stories">
      <div><h2 data-type="title-m">Story collection</h2><p data-type="body-s" className="mt-xs text-on-surface-low">Keep your answers and follow-up stories together. Earlier answers remain in revision history.</p></div>
      {error && !stories.length ? <p role="alert" className="text-danger">{error}</p> : loading && !stories.length ? <ListSkeleton rows={3} what="life stories" /> : !stories.length ? <EmptyState icon={BookOpen} title="No stories yet" hint="Start with a question or memory you want to preserve." action={{ label: 'Write your first story', onClick: () => newStory(), icon: Plus }} /> : <ol className="flex flex-col gap-s">{stories.map((story, index) => <li key={story.id}><ListRow index={index} label={story.prompt} onClick={() => select(story.id)}><div className="min-w-0 flex-1"><p data-type="title-m" className="truncate">{story.prompt}</p><p data-type="caption" className="mt-xs truncate text-on-surface-low">{story.theme} · {new Date(story.updated_at).toLocaleDateString()}</p></div></ListRow></li>)}</ol>}
      <a className="inline-flex text-primary" href={endpoint + '/export'} download="life-stories.json"><Download size={15} className="mr-xs" />Export chronology</a>
    </section>
    <section className="min-w-0" aria-label="Story workspace">
      {error && !!stories.length && <p role="alert" className="mb-m text-danger">{error}</p>}
      {selected && !loading && !current ? <EmptyState title="Story not found" hint="It may have been removed. Choose another story from the collection." /> : mode === 'read' && current ? <Surface className="p-l"><article className="space-y-l"><header className="flex flex-wrap items-start justify-between gap-m"><div><p data-type="label-s" className="text-primary">{current.theme}</p><h2 data-type="title-l" className="mt-xs">{current.prompt}</h2></div><Button size="sm" variant="tonal" onClick={() => setMode('edit')}><Pencil size={15} />Edit story</Button></header><p data-type="body-l" className="whitespace-pre-wrap break-words">{current.text}</p><footer data-type="caption" className="text-on-surface-low">Updated {new Date(current.updated_at).toLocaleString()} · Revision {current.revision}</footer><div className="flex flex-wrap gap-s"><Button variant="tonal" onClick={() => newStory(current.id)}>Add follow-up</Button><Button variant="danger" disabled={busy} onClick={() => void remove()}>Delete story</Button></div></article></Surface> : mode === 'create' || mode === 'edit' ? <Surface className="p-l"><form className="space-y-m" onSubmit={event => { event.preventDefault(); void save() }}><div><h2 data-type="title-l">{mode === 'edit' ? 'Edit story' : 'New story'}</h2><p data-type="body-s" className="mt-xs text-on-surface-low">Capture the event in your own words. Follow-ups remain connected to the earlier story.</p></div><label className="block" htmlFor="story-prompt">Question</label><input id="story-prompt" className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" value={draft.prompt} maxLength={4000} required onChange={event => setDraft({ ...draft, prompt: event.target.value })} /><label className="block" htmlFor="story-theme">Theme</label><input id="story-theme" className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" value={draft.theme} maxLength={200} required onChange={event => setDraft({ ...draft, theme: event.target.value })} /><label className="block" htmlFor="story-text">Your answer</label><textarea id="story-text" className="w-full min-w-0 resize-y rounded-md border border-outline-variant/30 bg-surface-container p-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" rows={8} value={draft.text} maxLength={100000} required onChange={event => setDraft({ ...draft, text: event.target.value })} />{draft.parent_id && <p data-type="body-s" className="text-on-surface-low">Follow-up to {stories.find(story => story.id === draft.parent_id)?.prompt || draft.parent_id}</p>}<div className="flex flex-wrap gap-s"><Button type="submit" loading={busy} disabled={loading}>Save answer</Button><Button variant="ghost" disabled={busy} onClick={() => setMode(selected ? 'read' : 'idle')}>Cancel</Button></div></form></Surface> : !loading && stories.length ? <EmptyState icon={BookOpen} title="Choose a story" hint="Select a story to read it, or start a new one from the page header." /> : null}
      {selected && chain.length > 1 && <section aria-label="Story chain" className="mt-l"><h2 data-type="title-m">Story chain</h2><div className="mt-s space-y-s">{chain.map(story => <Surface key={story.id} className="p-m"><button className="text-start text-primary" onClick={() => select(story.id)}>{story.prompt}</button><p data-type="body-s" className="mt-s line-clamp-3 whitespace-pre-wrap break-words">{story.text}</p><p data-type="caption" className="mt-s text-on-surface-low">{new Date(story.created_at).toLocaleString()} · Revision {story.revision}</p></Surface>)}</div></section>}
    </section>
  </main></WorkbenchLayout>
}
