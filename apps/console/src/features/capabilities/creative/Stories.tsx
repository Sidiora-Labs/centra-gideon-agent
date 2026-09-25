import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Ref = { id: string; revision: number }
type Beat = { id: string; title: string; summary: string }
type Values = { title: string; genre: string; premise: string; protagonist_goal: string; conflict: string; stakes: string; ending: string; beats: Beat[]; stage: string; author_ref: Ref | null; universe_ref: Ref | null }
type Story = Values & { id: string; revision: number; next_stage: string; missing_for_next_stage: string[]; work_links?: { work_id: string; story_revision: number; missing: boolean }[]; source_status?: { field: string; title: string; revision: number; missing: boolean }[] }
type Suggestion = { mode: string; id: string; base_revision: number; stage: string; patch: Partial<Values> }
const fields = ['genre', 'premise', 'protagonist_goal', 'conflict', 'stakes', 'ending'] as const
const labels = { genre: 'Genre', premise: 'Premise', protagonist_goal: 'Protagonist goal', conflict: 'Conflict', stakes: 'Stakes', ending: 'Ending' }
const blank = (): Values => ({ title: '', genre: '', premise: '', protagonist_goal: '', conflict: '', stakes: '', ending: '', beats: [], stage: 'premise', author_ref: null, universe_ref: null })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('story') || ''
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
const editable = (story: Story): Values => ({ ...Object.fromEntries(Object.keys(blank()).map(key => [key, story[key as keyof Values]])) }) as Values

export default function Stories({ apiRoot = '/api/capabilities/creative/stories' }: { apiRoot?: string }) {
  const [id, setId] = useState(readId)
  const [selected, setSelected] = useState<Story | null>(null)
  const [draft, setDraft] = useState<Values>(blank)
  const [items, setItems] = useState<Story[]>([])
  const [history, setHistory] = useState<Story[]>([])
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [authors, setAuthors] = useState<(Ref & { title: string })[]>([])
  const [universes, setUniverses] = useState<(Ref & { title: string })[]>([])
  const [query, setQuery] = useState('')
  const [references, setReferences] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [instruction, setInstruction] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [suggestionRequest, setSuggestionRequest] = useState(() => crypto.randomUUID())
  const [workRequest, setWorkRequest] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Unable to load story development')
  function choose(next: string) { location.hash = `/capabilities/creative?view=stories${next ? `&story=${next}` : ''}`; setId(next); setError(''); if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setSuggestions([]); setRequestId(crypto.randomUUID()); setWorkRequest(crypto.randomUUID()) } }
  async function load(storyId: string) { return Promise.all([requestJson<Story>(`${apiRoot}/${storyId}`), requestJson<{ items: Story[] }>(`${apiRoot}/${storyId}/revisions`), requestJson<{ items: Suggestion[] }>(`${apiRoot}/${storyId}/suggestions`)]) }
  function apply([story, versions, guidance]: Awaited<ReturnType<typeof load>>) { setSelected(story); setDraft(editable(story)); setHistory(versions.items); setSuggestions(guidance.items) }
  useEffect(() => { const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
  useEffect(() => {
    let alive = true; setLoading(true)
    Promise.all([requestJson<{ items: Story[]; total: number }>(`${apiRoot}?q=${encodeURIComponent(query)}&offset=${offset}&limit=25`), requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/stories$/, 'authors')}?q=${encodeURIComponent(references)}&limit=100`), requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/stories$/, 'universes')}?q=${encodeURIComponent(references)}&limit=100`)])
      .then(([list, a, u]) => { if (alive) { setItems(list.items); setTotal(list.total); setAuthors(a.items); setUniverses(u.items) } }).catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [apiRoot, query, references, offset, refresh])
  useEffect(() => {
    let alive = true
    if (selected?.id !== id) { setSelected(null); setDraft(blank()); setHistory([]); setSuggestions([]); setInstruction(''); setWorkRequest(crypto.randomUUID()) }
    if (!id) { setBusy(false); return }
    setBusy(true); load(id).then(result => { if (alive) apply(result) }).catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [apiRoot, id, reload])
  async function save(stage?: string, restore?: number) {
    setBusy(true); setError('')
    try {
      const story = restore && selected ? await requestJson<Story>(`${apiRoot}/${id}/restore`, 'POST', { revision: selected.revision, target_revision: restore }) : await requestJson<Story>(`${apiRoot}${selected ? `/${id}` : ''}`, selected ? 'PATCH' : 'POST', { ...draft, ...(stage ? { stage } : {}), ...(selected ? { revision: selected.revision } : { request_id: requestId }) })
      apply(await load(story.id)); choose(story.id); setRefresh(v => v + 1); setSuggestionRequest(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function suggest() {
    setBusy(true); setError('')
    try { await requestJson(`${apiRoot}/${id}/suggestions`, 'POST', { request_id: suggestionRequest, revision: selected!.revision, instruction }); setSuggestions((await requestJson<{ items: Suggestion[] }>(`${apiRoot}/${id}/suggestions`)).items); setSuggestionRequest(crypto.randomUUID()) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function adopt(suggestionId: string) {
    setBusy(true); setError('')
    try { await requestJson(`${apiRoot}/${id}/adopt`, 'POST', { revision: selected!.revision, suggestion_id: suggestionId }); apply(await load(id)); setRefresh(v => v + 1) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function createWork() {
    setBusy(true); setError('')
    try { await requestJson(`${apiRoot}/${id}/work`, 'POST', { request_id: workRequest, revision: selected!.revision }); apply(await load(id)) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  function beat(index: number, patch: Partial<Beat>) { setDraft({ ...draft, beats: draft.beats.map((b, i) => i === index ? { ...b, ...patch } : b) }) }
  function move(index: number) { const beats = [...draft.beats]; [beats[index - 1], beats[index]] = [beats[index], beats[index - 1]]; setDraft({ ...draft, beats }) }
  return <main className="space-y-4 p-4 text-on-surface"><h1 className="text-xl font-semibold">Guided story development</h1>{error && <div role="alert">{error}<Button onClick={() => { setError(''); setReload(v => v + 1); setRefresh(v => v + 1) }}>Retry</Button></div>}{loading && <p>Loading stories…</p>}
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]"><aside className="space-y-3"><label>Search stories<input className={control} value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} /></label><Button onClick={() => choose('')}>New story</Button>{!loading && !items.length && <p>No stories found.</p>}{items.map(story => <Button key={story.id} onClick={() => choose(story.id)}>{story.title}</Button>)}<p>{total} stories</p><Button disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>Previous page</Button><Button disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>Next page</Button></aside>
    <section className="min-w-0 space-y-3">{selected && <><p>Story revision {selected.revision}</p><p>Current stage: {selected.stage}</p>{selected.stage !== 'ready' && <p>Next stage: {selected.next_stage}{selected.missing_for_next_stage.length ? ` · Complete: ${selected.missing_for_next_stage.join(', ')}` : ' · Ready to advance'}</p>}</>}
      <label className="block">Story title<input className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>{fields.map(field => <label key={field} className="block">{labels[field]}<textarea className={control} value={draft[field]} onChange={e => setDraft({ ...draft, [field]: e.target.value })} /></label>)}
      <Button onClick={() => setDraft({ ...draft, beats: [...draft.beats, { id: crypto.randomUUID(), title: '', summary: '' }] })}>Add story beat</Button>{draft.beats.map((b, index) => <fieldset key={b.id} className="space-y-2 border border-outline p-3"><legend>Story beat {index + 1}</legend><label className="block">Beat title {index + 1}<input className={control} value={b.title} onChange={e => beat(index, { title: e.target.value })} /></label><label className="block">Beat summary {index + 1}<textarea className={control} value={b.summary} onChange={e => beat(index, { summary: e.target.value })} /></label><Button disabled={!index} onClick={() => move(index)}>Move beat {index + 1} up</Button><Button onClick={() => setDraft({ ...draft, beats: draft.beats.filter((_, i) => i !== index) })}>Remove beat {index + 1}</Button></fieldset>)}
      <label className="block">Search story context<input className={control} value={references} onChange={e => setReferences(e.target.value)} /></label><label className="block">Story author<select className={control} value={draft.author_ref?.id || ''} onChange={e => { const ref = authors.find(a => a.id === e.target.value); setDraft({ ...draft, author_ref: ref ? { id: ref.id, revision: ref.revision } : null }) }}><option value="">No author</option>{authors.map(a => <option key={a.id} value={a.id}>{a.title} · revision {a.revision}</option>)}</select></label><label className="block">Story universe<select className={control} value={draft.universe_ref?.id || ''} onChange={e => { const ref = universes.find(u => u.id === e.target.value); setDraft({ ...draft, universe_ref: ref ? { id: ref.id, revision: ref.revision } : null }) }}><option value="">No universe</option>{universes.map(u => <option key={u.id} value={u.id}>{u.title} · revision {u.revision}</option>)}</select></label>
      {selected?.source_status?.map(ref => <p key={ref.field}>{ref.title} · pinned revision {ref.revision}{ref.missing && ' · Context missing'}</p>)}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>Save story</Button>{selected && <>{selected.stage !== 'ready' ? <Button disabled={busy} onClick={() => void save(selected.next_stage)}>Advance story stage</Button> : <Button disabled={busy} onClick={() => void createWork()}>Create writing work</Button>}
        {selected.work_links?.map(link => <p key={link.work_id}>{link.missing ? 'Writing work missing' : <a href={`#/capabilities/creative?view=works&work=${link.work_id}`}>Open writing work</a>} · story revision {link.story_revision}</p>)}
        {selected.stage !== 'ready' && <><label className="block">Guidance instruction<textarea className={control} value={instruction} onChange={e => { setInstruction(e.target.value); setSuggestionRequest(crypto.randomUUID()) }} /></label><Button disabled={busy} onClick={() => void suggest()}>Request model guidance</Button></>}
        {suggestions.map((suggestion, index) => <section key={suggestion.id} aria-label={`Story suggestion ${index + 1}`} className="border border-outline p-3"><h2>Story suggestion · {suggestion.stage}</h2><p>Suggestion source: {suggestion.mode}</p>{fields.filter(field => suggestion.patch[field] !== undefined).map(field => <p key={field}>{labels[field]}: {suggestion.patch[field]}</p>)}{suggestion.patch.beats?.map(b => <p key={b.id}>{b.title}: {b.summary}</p>)}<Button disabled={busy || suggestion.base_revision !== selected.revision} onClick={() => void adopt(suggestion.id)}>Adopt suggestion {index + 1}</Button></section>)}
        <section aria-label="Story history">{history.map(story => <p key={story.revision}>Revision {story.revision}: {story.title}<Button disabled={busy || story.revision === selected.revision} onClick={() => void save(undefined, story.revision)}>Restore story revision {story.revision}</Button></p>)}</section>
      </>}
    </section></div></main>
}
