import { useEffect, useState } from 'react'
import { Plus, Sparkles } from 'lucide-react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListScaffold } from '../../../shared/ui/ListScaffold'
import { Surface } from '../../../shared/ui/Surface'
import { SearchField } from '../../../shared/ui/SearchField'
import { HeaderActions, HeaderControl } from '../../../shared/ui/HeaderActions'

type Ref = { id: string; revision: number }
type Beat = { id: string; title: string; summary: string }
type Values = { title: string; genre: string; premise: string; protagonist_goal: string; conflict: string; stakes: string; ending: string; beats: Beat[]; stage: string; author_ref: Ref | null; universe_ref: Ref | null }
type Story = Values & { id: string; revision: number; next_stage: string; missing_for_next_stage: string[]; work_links?: { work_id: string; story_revision: number; missing: boolean }[]; source_status?: { field: string; title: string; revision: number; missing: boolean }[] }
type Suggestion = { mode: 'authored' | 'model'; id: string; base_revision: number; stage: string; patch: Partial<Values> }
const fields = ['genre', 'premise', 'protagonist_goal', 'conflict', 'stakes', 'ending'] as const
const labels = { genre: 'Genre', premise: 'Premise', protagonist_goal: 'Protagonist goal', conflict: 'Conflict', stakes: 'Stakes', ending: 'Ending' }
const blank = (): Values => ({ title: '', genre: '', premise: '', protagonist_goal: '', conflict: '', stakes: '', ending: '', beats: [], stage: 'premise', author_ref: null, universe_ref: null })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('story') || ''
const control = 'min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none transition-colors placeholder:text-on-surface-low focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
const editable = (story: Story): Values => ({ ...Object.fromEntries(Object.keys(blank()).map(key => [key, story[key as keyof Values]])) }) as Values

export default function Stories({ apiRoot = '/api/capabilities/creative/stories' }: { apiRoot?: string }) {
  const t = (value: string) => value
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
  const [creating, setCreating] = useState(true)
  const [error, setError] = useState('')
  const [instruction, setInstruction] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [suggestionRequest, setSuggestionRequest] = useState(() => crypto.randomUUID())
  const [workRequest, setWorkRequest] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Unable to load story development'))
  function choose(next: string) { location.hash = `/capabilities/creative?view=stories${next ? `&story=${next}` : ''}`; setId(next); setError(''); setCreating(true); if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setSuggestions([]); setRequestId(crypto.randomUUID()); setWorkRequest(crypto.randomUUID()) } }
  function startNew() { choose(''); setCreating(true) }
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
  const showEditor = !!selected || creating || (!loading && items.length === 0)
  return <ListScaffold title={t('Guided story development')} right={<HeaderActions><HeaderControl icon={Plus} label={t('New story')} variant="primary" priority="primary" disabled={busy} onClick={startNew} /></HeaderActions>}>{error && <div role="alert">{error}<Button onClick={() => { setError(''); setReload(v => v + 1); setRefresh(v => v + 1) }}>{t('Retry')}</Button></div>}{loading && <p>{t('Loading stories…')}</p>}
    <p data-type="body-m" className="mb-l max-w-[48rem] text-on-surface-low">{t('Develop a premise into a reviewed story plan, one explicit stage at a time.')}</p>
    <div className="grid min-w-0 gap-l lg:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]"><Surface className="h-fit p-l"><aside aria-label={t('Stories')} className="space-y-m"><SearchField value={query} onChange={value => { setQuery(value); setOffset(0) }} placeholder={t('Search stories')} ariaLabel={t('Search stories')} surface="container" />{!loading && !items.length && <p className="text-on-surface-low">{t('No stories found.')}</p>}<div className="space-y-xs">{items.map(story => <Button key={story.id} variant={selected?.id === story.id ? 'tonal' : 'ghost'} ariaPressed={selected?.id === story.id} className="w-full justify-start" onClick={() => choose(story.id)}>{story.title}</Button>)}</div><p className="text-on-surface-low">{total} {t('stories')}</p><div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>{t('Previous page')}</Button><Button size="sm" variant="secondary" disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>{t('Next page')}</Button></div></aside></Surface>
    <section className="min-w-0 space-y-l">{!showEditor ? <Surface className="p-xl"><EmptyState icon={Sparkles} title={t('Choose a story')} hint={t('Select a story to continue its current stage, or begin with a new premise.')} action={{ label: t('New story'), onClick: startNew, icon: Plus }} /></Surface> : <Surface className="space-y-m p-l">{selected && <><div className="flex flex-wrap items-center justify-between gap-s"><p>{t('Story revision')} {selected.revision}</p><p>{t('Current stage:')} {t(selected.stage)}</p></div>{selected.stage !== 'ready' && <p className="text-on-surface-low">{t('Next stage:')} {t(selected.next_stage)}{selected.missing_for_next_stage.length ? ` · ${t('Complete:')} ${selected.missing_for_next_stage.map(field => t(labels[field as keyof typeof labels] || field)).join(', ')}` : ` · ${t('Ready to advance')}`}</p>}</>}
      <div><h2 data-type="title-m" className="text-on-surface">{selected ? draft.title || t('Untitled story') : t('New story')}</h2><p className="text-on-surface-low">{t('Keep the current story decision visible while you shape the next one.')}</p></div>
      <label className="block">{t('Story title')}<input className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>{fields.map(field => <label key={field} className="block">{t(labels[field])}<textarea className={control} value={draft[field]} onChange={e => setDraft({ ...draft, [field]: e.target.value })} /></label>)}
      <Button onClick={() => setDraft({ ...draft, beats: [...draft.beats, { id: crypto.randomUUID(), title: '', summary: '' }] })}>{t('Add story beat')}</Button>{draft.beats.map((b, index) => <fieldset key={b.id} className="space-y-m border-t border-outline-variant/20 pt-l"><legend>{t('Story beat')} {index + 1}</legend><label className="block">{t('Beat title')} {index + 1}<input className={control} value={b.title} onChange={e => beat(index, { title: e.target.value })} /></label><label className="block">{t('Beat summary')} {index + 1}<textarea className={control} value={b.summary} onChange={e => beat(index, { summary: e.target.value })} /></label><Button disabled={!index} onClick={() => move(index)}>{t('Move beat')} {index + 1} {t('up')}</Button><Button onClick={() => setDraft({ ...draft, beats: draft.beats.filter((_, i) => i !== index) })}>{t('Remove beat')} {index + 1}</Button></fieldset>)}
      <SearchField value={references} onChange={setReferences} placeholder={t('Search story context')} ariaLabel={t('Search story context')} surface="container" /><label className="block">{t('Story author')}<select className={control} value={draft.author_ref?.id || ''} onChange={e => { const ref = authors.find(a => a.id === e.target.value); setDraft({ ...draft, author_ref: ref ? { id: ref.id, revision: ref.revision } : null }) }}><option value="">{t('No author')}</option>{authors.map(a => <option key={a.id} value={a.id}>{a.title} · {t('revision')} {a.revision}</option>)}</select></label><label className="block">{t('Story universe')}<select className={control} value={draft.universe_ref?.id || ''} onChange={e => { const ref = universes.find(u => u.id === e.target.value); setDraft({ ...draft, universe_ref: ref ? { id: ref.id, revision: ref.revision } : null }) }}><option value="">{t('No universe')}</option>{universes.map(u => <option key={u.id} value={u.id}>{u.title} · {t('revision')} {u.revision}</option>)}</select></label>
      {selected?.source_status?.map(ref => <p key={ref.field}>{ref.title} · {t('pinned revision')} {ref.revision}{ref.missing && ` · ${t('Context missing')}`}</p>)}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>{t('Save story')}</Button>{selected && <>{selected.stage !== 'ready' ? <Button disabled={busy} onClick={() => void save(selected.next_stage)}>{t('Advance story stage')}</Button> : <Button disabled={busy} onClick={() => void createWork()}>{t('Create writing work')}</Button>}
        {selected.work_links?.map(link => <p key={link.work_id}>{link.missing ? t('Writing work missing') : <a href={`#/capabilities/creative?view=works&work=${link.work_id}`}>{t('Open writing work')}</a>} · {t('story revision')} {link.story_revision}</p>)}
        {selected.stage !== 'ready' && <><label className="block">{t('Guidance instruction')}<textarea className={control} value={instruction} onChange={e => { setInstruction(e.target.value); setSuggestionRequest(crypto.randomUUID()) }} /></label><Button disabled={busy} onClick={() => void suggest()}>{t('Request model guidance')}</Button></>}
        {suggestions.map((suggestion, index) => <section key={suggestion.id} aria-label={`${t('Story suggestion')} ${index + 1}`} className="space-y-s border-t border-outline-variant/20 pt-l"><h2 data-type="title-s">{t('Story suggestion')} · {t(suggestion.stage)}</h2><p>{t('Suggestion source:')} {t(suggestion.mode)}</p>{fields.filter(field => suggestion.patch[field] !== undefined).map(field => <p key={field}>{t(labels[field])}: {suggestion.patch[field]}</p>)}{suggestion.patch.beats?.map(b => <p key={b.id}>{b.title}: {b.summary}</p>)}<Button disabled={busy || suggestion.base_revision !== selected.revision} onClick={() => void adopt(suggestion.id)}>{t('Adopt suggestion')} {index + 1}</Button></section>)}
        <section aria-label={t('Story history')}>{history.map(story => <p key={story.revision}>{t('Revision')} {story.revision}: {story.title}<Button disabled={busy || story.revision === selected.revision} onClick={() => void save(undefined, story.revision)}>{t('Restore story revision')} {story.revision}</Button></p>)}</section>
      </>}
    </Surface>}</section></div></ListScaffold>
}
