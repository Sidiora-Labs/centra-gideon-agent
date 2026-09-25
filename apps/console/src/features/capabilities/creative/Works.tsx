import Editorial from './Editorial'
import Continuity from './Continuity'
import Polishing from './Polishing'
import { useEffect, useState } from 'react'
import { BookOpen, FilePlus2 } from 'lucide-react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListScaffold } from '../../../shared/ui/ListScaffold'
import { Surface } from '../../../shared/ui/Surface'
import { SearchField } from '../../../shared/ui/SearchField'
import { HeaderActions, HeaderControl } from '../../../shared/ui/HeaderActions'

type Ref = { id: string; revision: number }
type Draft = { id: string; artifact_id: string; artifact_version: number; note: string; characters: number; missing?: boolean; text?: string }
type Values = { title: string; kind: string; prompt: string; author_ref: Ref | null; universe_ref: Ref | null; active_draft_id: string | null }
type Work = Values & { id: string; revision: number; active_draft?: Draft | null; draft_missing?: boolean; text?: string }
const blank = (): Values => ({ title: '', kind: 'work', prompt: '', author_ref: null, universe_ref: null, active_draft_id: null })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('work') || ''
const control = 'min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none transition-colors placeholder:text-on-surface-low focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
const values = (w: Work): Values => ({ title: w.title, kind: w.kind, prompt: w.prompt, author_ref: w.author_ref, universe_ref: w.universe_ref, active_draft_id: w.active_draft_id })

export default function Works({ apiRoot = '/api/capabilities/creative/works' }: { apiRoot?: string }) {
  const [repairRefresh, setRepairRefresh] = useState(0)
  const t = (value: string) => value
  const [id, setId] = useState(readId)
  const [selected, setSelected] = useState<Work | null>(null)
  const [draft, setDraft] = useState<Values>(blank)
  const [items, setItems] = useState<Work[]>([])
  const [history, setHistory] = useState<Work[]>([])
  const [drafts, setDrafts] = useState<Draft[]>([])
  const [authors, setAuthors] = useState<(Ref & { title: string })[]>([])
  const [universes, setUniverses] = useState<(Ref & { title: string })[]>([])
  const [query, setQuery] = useState('')
  const [referenceQuery, setReferenceQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(true)
  const [error, setError] = useState('')
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [context, setContext] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [draftRequestId, setDraftRequestId] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Unable to load writing works'))
  function choose(next: string) {
    location.hash = `/capabilities/creative?view=works${next ? `&work=${next}` : ''}`
    setId(next); setError(''); setContext(''); setCreating(true)
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setDrafts([]); setText(''); setNote(''); setRequestId(crypto.randomUUID()); setDraftRequestId(crypto.randomUUID()) }
  }
  function startNew() { choose(''); setCreating(true) }
  async function load(workId: string) {
    return Promise.all([requestJson<Work>(`${apiRoot}/${workId}`), requestJson<{ items: Work[] }>(`${apiRoot}/${workId}/revisions`), requestJson<{ items: Draft[] }>(`${apiRoot}/${workId}/drafts`)])
  }
  function apply([work, versions, manuscripts]: Awaited<ReturnType<typeof load>>, preserveText = false) {
    setSelected(work); setDraft(values(work)); setHistory(versions.items); setDrafts(manuscripts.items); if (!preserveText) setText(work.text || '')
  }
  useEffect(() => { const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
  useEffect(() => {
    let alive = true; setLoading(true)
    Promise.all([requestJson<{ items: Work[]; total: number }>(`${apiRoot}?q=${encodeURIComponent(query)}&offset=${offset}&limit=25`),
      requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/works$/, 'authors')}?q=${encodeURIComponent(referenceQuery)}&limit=100`),
      requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/works$/, 'universes')}?q=${encodeURIComponent(referenceQuery)}&limit=100`)])
      .then(([list, a, u]) => { if (alive) { setItems(list.items); setTotal(list.total); setAuthors(a.items); setUniverses(u.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [apiRoot, query, referenceQuery, offset, refresh])
  useEffect(() => {
    let alive = true
    if (selected?.id !== id) { setSelected(null); setDraft(blank()); setHistory([]); setDrafts([]); setText(''); setContext(''); setNote('') }
    if (!id) { setBusy(false); return }
    setBusy(true)
    load(id).then(result => { if (alive) apply(result) }).catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [apiRoot, id, reload])
  async function save(target?: number) {
    setBusy(true); setError('')
    try {
      const work = target && selected ? await requestJson<Work>(`${apiRoot}/${id}/restore`, 'POST', { revision: selected.revision, target_revision: target })
        : await requestJson<Work>(`${apiRoot}${selected ? `/${id}` : ''}`, selected ? 'PATCH' : 'POST', { ...draft, ...(selected ? { revision: selected.revision } : { request_id: requestId }) })
      apply(await load(work.id), !target && !!selected); choose(work.id); setRefresh(v => v + 1)
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function saveText() {
    setBusy(true); setError('')
    try {
      await requestJson(`${apiRoot}/${id}/drafts`, 'POST', { request_id: draftRequestId, revision: selected!.revision, text, note })
      apply(await load(id)); setDraftRequestId(crypto.randomUUID()); setNote(''); setRefresh(v => v + 1)
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function readDraft(draftId: string) {
    try { const result = await requestJson<Draft>(`${apiRoot}/${id}/drafts/${draftId}`); if (result.missing) setError(t('Draft artifact missing')); else { setText(result.text || ''); setNote(''); setDraftRequestId(crypto.randomUUID()) } } catch (e) { fail(e) }
  }
  async function readContext() { try { setContext(JSON.stringify(await requestJson(`${apiRoot}/${id}/context`), null, 2)) } catch (e) { fail(e) } }
  const showEditor = !!selected || creating || (!loading && items.length === 0)
  return <ListScaffold title={t('Writing works and exercises')} right={<HeaderActions><HeaderControl icon={FilePlus2} label={t('New work')} variant="primary" priority="primary" disabled={busy} onClick={startNew} /></HeaderActions>}>
    <p data-type="body-m" className="mb-l max-w-[48rem] text-on-surface-low">{t('Choose a work from your library, then write and preserve manuscript drafts with pinned creative context.')}</p>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setReload(v => v + 1); setRefresh(v => v + 1) }}>{t('Retry')}</Button></div>}{loading && <p>{t('Loading writing works…')}</p>}
    <div className="grid min-w-0 gap-l lg:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]"><Surface className="h-fit p-l"><aside aria-label={t('Writing works')} className="space-y-m"><SearchField value={query} onChange={value => { setQuery(value); setOffset(0) }} placeholder={t('Search writing works')} ariaLabel={t('Search writing works')} surface="container" />
      {!loading && !items.length && <p className="text-on-surface-low">{t('No writing works found.')}</p>}<div className="space-y-xs">{items.map(work => <Button key={work.id} variant={selected?.id === work.id ? 'tonal' : 'ghost'} className="w-full justify-start" ariaPressed={selected?.id === work.id} onClick={() => choose(work.id)}>{work.title}</Button>)}</div><p className="text-on-surface-low">{total} {t('writing works')}</p><div className="flex flex-wrap gap-s"><Button variant="secondary" size="sm" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>{t('Previous page')}</Button><Button variant="secondary" size="sm" disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>{t('Next page')}</Button></div>
    </aside></Surface><section className="min-w-0 space-y-l">{!showEditor ? <Surface className="p-xl"><EmptyState icon={BookOpen} title={t('Choose a writing work')} hint={t('Select a work to continue writing, or start a new manuscript.')} action={{ label: t('New work'), onClick: startNew, icon: FilePlus2 }} /></Surface> : <>{selected && <p className="text-on-surface-low">{t('Work revision')} {selected.revision}</p>}<Surface className="space-y-m p-l">
      <div><h2 data-type="title-m" className="text-on-surface">{selected ? draft.title || t('Untitled work') : t('New work')}</h2><p className="text-on-surface-low">{t('Set the writing brief and pin the context this manuscript should follow.')}</p></div>
      <label className="block">{t('Work title')}<input className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      <label className="block">{t('Writing type')}<select className={control} value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value })}><option value="work">{t('Work')}</option><option value="exercise">{t('Exercise')}</option></select></label>
      <label className="block">{t('Writing prompt')}<textarea className={control} value={draft.prompt} onChange={e => setDraft({ ...draft, prompt: e.target.value })} /></label>
      <SearchField value={referenceQuery} onChange={setReferenceQuery} placeholder={t('Search writing context')} ariaLabel={t('Search writing context')} surface="container" />
      <label className="block">{t('Pin author')}<select className={control} value={draft.author_ref?.id || ''} onChange={e => { const a = authors.find(a => a.id === e.target.value); setDraft({ ...draft, author_ref: a ? { id: a.id, revision: a.revision } : null }) }}><option value="">{t('No author')}</option>{authors.map(a => <option key={a.id} value={a.id}>{a.title} · {t('revision')} {a.revision}</option>)}</select></label>
      <label className="block">{t('Pin universe')}<select className={control} value={draft.universe_ref?.id || ''} onChange={e => { const u = universes.find(u => u.id === e.target.value); setDraft({ ...draft, universe_ref: u ? { id: u.id, revision: u.revision } : null }) }}><option value="">{t('No universe')}</option>{universes.map(u => <option key={u.id} value={u.id}>{u.title} · {t('revision')} {u.revision}</option>)}</select></label>
      {draft.author_ref && <p>{t('Author pinned revision')} {draft.author_ref.revision}</p>}{draft.universe_ref && <p>{t('Universe pinned revision')} {draft.universe_ref.revision}</p>}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>{t('Save work details')}</Button>
      </Surface>
      {selected && <><Surface className="space-y-m p-l"><div className="flex flex-wrap items-start justify-between gap-m"><div><h2 data-type="title-m" className="text-on-surface">{t('Manuscript')}</h2><p className="text-on-surface-low">{t('Write in the focused editor, then preserve this text as a new immutable draft.')}</p></div><Button variant="secondary" onClick={() => void readContext()}>{t('Read pinned context')}</Button></div>{context && <label className="block">{t('Pinned writing context')}<textarea className={control} readOnly value={context} /></label>}
        {selected.draft_missing && <p role="alert">{t('Draft artifact missing')}</p>}<label className="block"><span className="sr-only">{t('Manuscript')}</span><textarea className={`${control} min-h-[24rem] font-serif leading-7`} value={text} onChange={e => setText(e.target.value)} /></label><div className="flex flex-col gap-m sm:flex-row sm:items-end"><label className="block min-w-0 flex-1">{t('Draft note')}<input className={control} value={note} onChange={e => setNote(e.target.value)} /></label><Button disabled={busy || !text.trim()} onClick={() => void saveText()}>{t('Save new draft')}</Button></div>
      </Surface>
        <Editorial key={selected.id + "-editorial"} id={selected.id} revision={selected.revision} text={selected.text || ""} apiRoot={apiRoot} onPrepared={() => setRepairRefresh(v => v + 1)} />
        <Continuity key={selected.id + "-continuity"} id={selected.id} revision={selected.revision} text={selected.text || ""} apiRoot={apiRoot} />
        {selected.active_draft_id && !selected.draft_missing && <Polishing key={selected.id + repairRefresh} id={selected.id} revision={selected.revision} text={selected.text || ''} apiRoot={apiRoot} onPromoted={() => { setReload(v => v + 1); setRefresh(v => v + 1) }} />}
        <section aria-label={t('Manuscript drafts')} className="space-y-s"><h2 data-type="title-m">{t('Manuscript drafts')}</h2>{drafts.map((d, index) => <div key={d.id} className="flex flex-wrap items-center justify-between gap-s border-t border-outline-variant/20 py-s"><p>{d.note || `${t('Draft')} ${drafts.length - index}`} · {d.characters} {t('characters')}{d.id === selected.active_draft_id && ` · ${t('Active')}`}</p><Button size="sm" variant="secondary" onClick={() => void readDraft(d.id)}>{t('Read draft')} {drafts.length - index}</Button></div>)}</section>
        <section aria-label={t('Work history')}>{history.map(work => <p key={work.revision}>{t('Revision')} {work.revision}: {work.title}<Button disabled={busy || work.revision === selected.revision} onClick={() => void save(work.revision)}>{t('Restore work revision')} {work.revision}</Button></p>)}</section>
      </>}
    </>}</section></div></ListScaffold>
}
