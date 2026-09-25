import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
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
      if (selected === story.id) await load()
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
    setDraft({ ...empty, parent_id }); setRevision(0); setRequestId(crypto.randomUUID()); select(null)
  }
  return <section className="p-4 space-y-4 text-on-surface max-w-5xl mx-auto">
    <h1 className="text-2xl">Your life stories</h1>
    <p>Keep your answers and follow-up stories together. Earlier answers remain in revision history.</p>
    <div className="flex flex-wrap gap-2"><Button onClick={() => newStory()} disabled={busy}>New story</Button>
      <Button variant="secondary" onClick={() => void load()} disabled={busy}>Reload stories</Button>
      <a className="underline p-2" href={endpoint + '/export'} download="life-stories.json">Export chronology</a></div>
    {error && <p role="alert" className="text-danger">{error}</p>}
    {loading && <p role="status">Loading stories…</p>}
    <div className="grid gap-4 md:grid-cols-2">
      <nav aria-label="Life stories" className="space-y-2 min-w-0">
        {!loading && stories.length === 0 && <p>No stories yet. Start with a question you want to remember.</p>}
        {stories.map(story => <a key={story.id} className="block underline break-words" href={'#/capabilities/identity?story=' + story.id}
          onClick={() => select(story.id)}>{story.prompt}</a>)}
      </nav>
      <form className="space-y-3 min-w-0" onSubmit={e => { e.preventDefault(); void save() }}>
        <label className="block" htmlFor="story-prompt">Question</label>
        <input id="story-prompt" className="w-full bg-surface-high p-2 rounded" value={draft.prompt} maxLength={4000} required onChange={e => setDraft({ ...draft, prompt: e.target.value })} />
        <label className="block" htmlFor="story-theme">Theme</label>
        <input id="story-theme" className="w-full bg-surface-high p-2 rounded" value={draft.theme} maxLength={200} required onChange={e => setDraft({ ...draft, theme: e.target.value })} />
        <label className="block" htmlFor="story-text">Your answer</label>
        <textarea id="story-text" className="w-full bg-surface-high p-2 rounded" rows={8} value={draft.text} maxLength={100000} required onChange={e => setDraft({ ...draft, text: e.target.value })} />
        {draft.parent_id && <p>Follow-up to {stories.find(s => s.id === draft.parent_id)?.prompt || draft.parent_id}</p>}
        <div className="flex flex-wrap gap-2"><Button type="submit" loading={busy} disabled={loading}>Save answer</Button>
          {selected && <><Button variant="secondary" disabled={busy || loading} onClick={() => newStory(selected)}>Add follow-up</Button>
            <Button variant="danger" disabled={busy || loading} onClick={() => void remove()}>Delete story</Button></>}</div>
      </form>
    </div>
    {selected && <section aria-label="Story chain"><h2 className="text-xl">Story chain</h2>
      {chain.map(story => <article key={story.id} className="py-3 border-b border-outline"><a className="underline" href={'#/capabilities/identity?story=' + story.id}>{story.prompt}</a>
        <p className="whitespace-pre-wrap break-words">{story.text}</p><small>{story.created_at} · Revision {story.revision}</small></article>)}
    </section>}
  </section>
}
