import Polishing from './Polishing'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Ref = { id: string; revision: number }
type Draft = { id: string; artifact_id: string; artifact_version: number; note: string; characters: number; missing?: boolean; text?: string }
type Values = { title: string; kind: string; prompt: string; author_ref: Ref | null; universe_ref: Ref | null; active_draft_id: string | null }
type Work = Values & { id: string; revision: number; active_draft?: Draft | null; draft_missing?: boolean; text?: string }
const blank = (): Values => ({ title: '', kind: 'work', prompt: '', author_ref: null, universe_ref: null, active_draft_id: null })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('work') || ''
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
const values = (w: Work): Values => ({ title: w.title, kind: w.kind, prompt: w.prompt, author_ref: w.author_ref, universe_ref: w.universe_ref, active_draft_id: w.active_draft_id })

export default function Works({ apiRoot = '/api/capabilities/creative/works' }: { apiRoot?: string }) {
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
  const [error, setError] = useState('')
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [context, setContext] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [draftRequestId, setDraftRequestId] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Unable to load writing works')
  function choose(next: string) {
    location.hash = `/capabilities/creative?view=works${next ? `&work=${next}` : ''}`
    setId(next); setError(''); setContext('')
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setDrafts([]); setText(''); setNote(''); setRequestId(crypto.randomUUID()); setDraftRequestId(crypto.randomUUID()) }
  }
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
      apply(await load(work.id), !target && Boolean(selected)); choose(work.id); setRefresh(v => v + 1)
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
    try { const result = await requestJson<Draft>(`${apiRoot}/${id}/drafts/${draftId}`); if (result.missing) setError('Draft artifact missing'); else { setText(result.text || ''); setNote(''); setDraftRequestId(crypto.randomUUID()) } } catch (e) { fail(e) }
  }
  async function readContext() { try { setContext(JSON.stringify(await requestJson(`${apiRoot}/${id}/context`), null, 2)) } catch (e) { fail(e) } }
  return <main className="space-y-4 p-4 text-on-surface"><h1 className="text-xl font-semibold">Writing works and exercises</h1>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setReload(v => v + 1); setRefresh(v => v + 1) }}>Retry</Button></div>}{loading && <p>Loading writing works…</p>}
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]"><aside className="space-y-3"><label>Search writing works<input className={control} value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} /></label><Button onClick={() => choose('')}>New work</Button>
      {!loading && !items.length && <p>No writing works found.</p>}{items.map(work => <Button key={work.id} onClick={() => choose(work.id)}>{work.title}</Button>)}<p>{total} writing works</p><Button disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>Previous page</Button><Button disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>Next page</Button>
    </aside><section className="min-w-0 space-y-3">{selected && <p>Work revision {selected.revision}</p>}
      <label className="block">Work title<input className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      <label className="block">Writing type<select className={control} value={draft.kind} onChange={e => setDraft({ ...draft, kind: e.target.value })}><option value="work">Work</option><option value="exercise">Exercise</option></select></label>
      <label className="block">Writing prompt<textarea className={control} value={draft.prompt} onChange={e => setDraft({ ...draft, prompt: e.target.value })} /></label>
      <label className="block">Search writing context<input className={control} value={referenceQuery} onChange={e => setReferenceQuery(e.target.value)} /></label>
      <label className="block">Pin author<select className={control} value={draft.author_ref?.id || ''} onChange={e => { const a = authors.find(a => a.id === e.target.value); setDraft({ ...draft, author_ref: a ? { id: a.id, revision: a.revision } : null }) }}><option value="">No author</option>{authors.map(a => <option key={a.id} value={a.id}>{a.title} · revision {a.revision}</option>)}</select></label>
      <label className="block">Pin universe<select className={control} value={draft.universe_ref?.id || ''} onChange={e => { const u = universes.find(u => u.id === e.target.value); setDraft({ ...draft, universe_ref: u ? { id: u.id, revision: u.revision } : null }) }}><option value="">No universe</option>{universes.map(u => <option key={u.id} value={u.id}>{u.title} · revision {u.revision}</option>)}</select></label>
      {draft.author_ref && <p>Author pinned revision {draft.author_ref.revision}</p>}{draft.universe_ref && <p>Universe pinned revision {draft.universe_ref.revision}</p>}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>Save work details</Button>
      {selected && <><Button onClick={() => void readContext()}>Read pinned context</Button>{context && <label className="block">Pinned writing context<textarea className={control} readOnly value={context} /></label>}
        {selected.draft_missing && <p role="alert">Draft artifact missing</p>}<label className="block">Manuscript<textarea className={`${control} min-h-64`} value={text} onChange={e => setText(e.target.value)} /></label><label className="block">Draft note<input className={control} value={note} onChange={e => setNote(e.target.value)} /></label><Button disabled={busy || !text.trim()} onClick={() => void saveText()}>Save new draft</Button>
        <section aria-label="Manuscript drafts"><h2>Manuscript drafts</h2>{drafts.map((d, index) => <p key={d.id}>{d.note || `Draft ${drafts.length - index}`} · {d.characters} characters{d.id === selected.active_draft_id && ' · Active'}<Button onClick={() => void readDraft(d.id)}>Read draft {drafts.length - index}</Button></p>)}</section>
        {selected.active_draft_id && !selected.draft_missing && <Polishing key={selected.id} id={selected.id} revision={selected.revision} text={selected.text || ''} apiRoot={apiRoot} onPromoted={() => { setReload(v => v + 1); setRefresh(v => v + 1) }} />}
        <section aria-label="Work history">{history.map(work => <p key={work.revision}>Revision {work.revision}: {work.title}<Button disabled={busy || work.revision === selected.revision} onClick={() => void save(work.revision)}>Restore work revision {work.revision}</Button></p>)}</section>
      </>}
    </section></div></main>
}
