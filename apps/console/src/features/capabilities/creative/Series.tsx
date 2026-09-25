import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Ref = { id: string; revision: number }
type Chapter = { id: string; title: string; prompt: string }
type Volume = { id: string; title: string; chapters: Chapter[] }
type Arc = { id: string; title: string; summary: string; chapter_ids: string[] }
type Values = { title: string; synopsis: string; volumes: Volume[]; arcs: Arc[]; author_ref: Ref | null; universe_ref: Ref | null }
type Status = { chapter_id: string; work_id: string | null; missing: boolean; draft_missing: boolean; work_revision: number | null; active_draft_id: string | null; ready_to_draft: boolean; stage: string }
type SeriesRecord = Values & { id: string; revision: number; chapter_status: Status[]; source_status?: { field: string; title: string; revision: number; missing: boolean }[] }
const blank = (): Values => ({ title: '', synopsis: '', volumes: [], arcs: [], author_ref: null, universe_ref: null })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('series') || ''
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
const values = (s: SeriesRecord): Values => ({ title: s.title, synopsis: s.synopsis, volumes: s.volumes, arcs: s.arcs, author_ref: s.author_ref, universe_ref: s.universe_ref })
function moved<T>(items: T[], index: number) { const result = [...items]; [result[index - 1], result[index]] = [result[index], result[index - 1]]; return result }

export default function Series({ apiRoot = '/api/capabilities/creative/series' }: { apiRoot?: string }) {
  const [id, setId] = useState(readId)
  const [selected, setSelected] = useState<SeriesRecord | null>(null)
  const [draft, setDraft] = useState<Values>(blank)
  const [items, setItems] = useState<SeriesRecord[]>([])
  const [history, setHistory] = useState<SeriesRecord[]>([])
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
  const [chapterId, setChapterId] = useState('')
  const selectionToken = useRef(0)
  const [manuscript, setManuscript] = useState('')
  const [mode, setMode] = useState('authored')
  const [instruction, setInstruction] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [draftRequest, setDraftRequest] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Unable to load series')
  function choose(next: string) { location.hash = `/capabilities/creative?view=series${next ? `&series=${next}` : ''}`; setId(next); setError(''); if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setChapterId(''); setManuscript(''); selectionToken.current++; setRequestId(crypto.randomUUID()) } }
  async function load(seriesId: string) { const [record, versions] = await Promise.all([requestJson<SeriesRecord>(`${apiRoot}/${seriesId}`), requestJson<{ items: SeriesRecord[] }>(`${apiRoot}/${seriesId}/revisions`)]); setSelected(record); setDraft(values(record)); setHistory(versions.items); return record }
  useEffect(() => { const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
  useEffect(() => {
    let alive = true; setLoading(true)
    Promise.all([requestJson<{ items: SeriesRecord[]; total: number }>(`${apiRoot}?q=${encodeURIComponent(query)}&offset=${offset}&limit=25`), requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/series$/, 'authors')}?q=${encodeURIComponent(references)}&limit=100`), requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/series$/, 'universes')}?q=${encodeURIComponent(references)}&limit=100`)])
      .then(([list, a, u]) => { if (alive) { setItems(list.items); setTotal(list.total); setAuthors(a.items); setUniverses(u.items) } }).catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [apiRoot, query, references, offset, refresh])
  useEffect(() => {
    let alive = true
    if (selected?.id !== id) { setSelected(null); setDraft(blank()); setHistory([]); setChapterId(''); setManuscript(''); selectionToken.current++ }
    if (!id) { setBusy(false); return }
    setBusy(true)
    Promise.all([requestJson<SeriesRecord>(`${apiRoot}/${id}`), requestJson<{ items: SeriesRecord[] }>(`${apiRoot}/${id}/revisions`)]).then(([record, versions]) => { if (alive) { setSelected(record); setDraft(values(record)); setHistory(versions.items) } }).catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [apiRoot, id, reload])
  async function save(target?: number) {
    setBusy(true); setError('')
    try { const record = target && selected ? await requestJson<SeriesRecord>(`${apiRoot}/${id}/restore`, 'POST', { revision: selected.revision, target_revision: target }) : await requestJson<SeriesRecord>(`${apiRoot}${selected ? `/${id}` : ''}`, selected ? 'PATCH' : 'POST', { ...draft, ...(selected ? { revision: selected.revision } : { request_id: requestId }) }); await load(record.id); choose(record.id); setRefresh(v => v + 1) } catch (e) { fail(e) } finally { setBusy(false) }
  }
  const status = selected?.chapter_status.find(s => s.chapter_id === chapterId)
  const chapters = draft.volumes.flatMap(v => v.chapters)
  async function selectChapter(next: string) {
    const token = ++selectionToken.current
    setChapterId(next); setManuscript(''); setError(''); setDraftRequest(crypto.randomUUID())
    const s = selected?.chapter_status.find(item => item.chapter_id === next)
    if (s?.work_id && !s.missing) { setBusy(true); try { const work = await requestJson<{ text: string }>(`${apiRoot.replace(/series$/, 'works')}/${s.work_id}`); if (token === selectionToken.current) setManuscript(work.text) } catch (e) { if (token === selectionToken.current) fail(e) } finally { if (token === selectionToken.current) setBusy(false) } }
  }
  async function operation(action: 'prepare' | 'draft' | 'review') {
    if (!selected) return
    setBusy(true); setError('')
    try {
      await requestJson(`${apiRoot}/${id}/chapters/${chapterId}/${action}`, 'POST', { revision: selected.revision, ...(action === 'prepare' ? {} : { work_revision: status!.work_revision }), ...(action === 'draft' ? { request_id: draftRequest, mode, ...(mode === 'authored' ? { text: manuscript, note: 'Series chapter draft' } : { instruction }) } : {}) })
      const record = await load(id); const next = record.chapter_status.find(s => s.chapter_id === chapterId)
      if (next?.work_id && action !== 'review') setManuscript((await requestJson<{ text: string }>(`${apiRoot.replace(/series$/, 'works')}/${next.work_id}`)).text)
      if (action === 'draft') setDraftRequest(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  function volume(index: number, patch: Partial<Volume>) { setDraft({ ...draft, volumes: draft.volumes.map((v, i) => i === index ? { ...v, ...patch } : v) }) }
  function chapter(vi: number, ci: number, patch: Partial<Chapter>) { volume(vi, { chapters: draft.volumes[vi].chapters.map((c, i) => i === ci ? { ...c, ...patch } : c) }) }
  function arc(index: number, patch: Partial<Arc>) { setDraft({ ...draft, arcs: draft.arcs.map((a, i) => i === index ? { ...a, ...patch } : a) }) }
  return <main className="space-y-4 p-4 text-on-surface"><h1 className="text-xl font-semibold">Series and staged drafting</h1>{error && <div role="alert">{error}<Button onClick={() => { setError(''); setReload(v => v + 1); setRefresh(v => v + 1) }}>Retry</Button></div>}{loading && <p>Loading series…</p>}
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]"><aside className="space-y-3"><label>Search series<input className={control} value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} /></label><Button onClick={() => choose('')}>New series</Button>{!loading && !items.length && <p>No series found.</p>}{items.map(s => <Button key={s.id} onClick={() => choose(s.id)}>{s.title}</Button>)}<p>{total} series</p><Button disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>Previous page</Button><Button disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>Next page</Button></aside>
    <section className="min-w-0 space-y-3">{selected && <p>Series revision {selected.revision}</p>}<label className="block">Series title<input className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label><label className="block">Series synopsis<textarea className={control} value={draft.synopsis} onChange={e => setDraft({ ...draft, synopsis: e.target.value })} /></label>
      <Button onClick={() => setDraft({ ...draft, volumes: [...draft.volumes, { id: crypto.randomUUID(), title: '', chapters: [] }] })}>Add volume</Button>
      {draft.volumes.map((v, vi) => <fieldset key={v.id} className="space-y-3 border border-outline p-3"><legend>Volume {vi + 1}</legend><label>Volume title {vi + 1}<input className={control} value={v.title} onChange={e => volume(vi, { title: e.target.value })} /></label><Button disabled={!vi} onClick={() => setDraft({ ...draft, volumes: moved(draft.volumes, vi) })}>Move volume {vi + 1} up</Button><Button onClick={() => setDraft({ ...draft, volumes: draft.volumes.filter((_, i) => i !== vi) })}>Remove volume {vi + 1}</Button><Button onClick={() => volume(vi, { chapters: [...v.chapters, { id: crypto.randomUUID(), title: '', prompt: '' }] })}>Add chapter to volume {vi + 1}</Button>
        {v.chapters.map((c, ci) => <fieldset key={c.id} className="space-y-2 border border-outline p-2"><legend>Chapter {vi + 1}.{ci + 1}</legend><label>Chapter title {vi + 1}.{ci + 1}<input className={control} disabled={!!selected?.chapter_status.find(s => s.chapter_id === c.id)?.work_id} value={c.title} onChange={e => chapter(vi, ci, { title: e.target.value })} /></label><label>Chapter prompt {vi + 1}.{ci + 1}<textarea className={control} disabled={!!selected?.chapter_status.find(s => s.chapter_id === c.id)?.work_id} value={c.prompt} onChange={e => chapter(vi, ci, { prompt: e.target.value })} /></label><Button disabled={!ci} onClick={() => volume(vi, { chapters: moved(v.chapters, ci) })}>Move chapter {vi + 1}.{ci + 1} up</Button><Button onClick={() => volume(vi, { chapters: v.chapters.filter((_, i) => i !== ci) })}>Remove chapter {vi + 1}.{ci + 1}</Button></fieldset>)}</fieldset>)}
      <Button onClick={() => setDraft({ ...draft, arcs: [...draft.arcs, { id: crypto.randomUUID(), title: '', summary: '', chapter_ids: [] }] })}>Add arc</Button>{draft.arcs.map((a, index) => <fieldset key={a.id} className="space-y-2 border border-outline p-3"><legend>Arc {index + 1}</legend><label>Arc title {index + 1}<input className={control} value={a.title} onChange={e => arc(index, { title: e.target.value })} /></label><label>Arc summary {index + 1}<textarea className={control} value={a.summary} onChange={e => arc(index, { summary: e.target.value })} /></label>{chapters.map(c => <label key={c.id} className="block"><input type="checkbox" checked={a.chapter_ids.includes(c.id)} onChange={e => arc(index, { chapter_ids: e.target.checked ? [...a.chapter_ids, c.id] : a.chapter_ids.filter(id => id !== c.id) })} />Arc {index + 1} includes {c.title}</label>)}<Button onClick={() => setDraft({ ...draft, arcs: draft.arcs.filter((_, i) => i !== index) })}>Remove arc {index + 1}</Button></fieldset>)}
      <label className="block">Search series context<input className={control} value={references} onChange={e => setReferences(e.target.value)} /></label><label className="block">Series author<select className={control} value={draft.author_ref?.id || ''} onChange={e => { const ref = authors.find(a => a.id === e.target.value); setDraft({ ...draft, author_ref: ref ? { id: ref.id, revision: ref.revision } : null }) }}><option value="">No author</option>{authors.map(a => <option key={a.id} value={a.id}>{a.title} · revision {a.revision}</option>)}</select></label><label className="block">Series universe<select className={control} value={draft.universe_ref?.id || ''} onChange={e => { const ref = universes.find(u => u.id === e.target.value); setDraft({ ...draft, universe_ref: ref ? { id: ref.id, revision: ref.revision } : null }) }}><option value="">No universe</option>{universes.map(u => <option key={u.id} value={u.id}>{u.title} · revision {u.revision}</option>)}</select></label>{selected?.source_status?.map(ref => <p key={ref.field}>{ref.title} · pinned revision {ref.revision}{ref.missing && ' · Context missing'}</p>)}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>Save series plan</Button>
      {selected && <><section aria-label="Staged chapter drafting" className="space-y-3"><h2>Staged chapter drafting</h2><p>Prepare each chapter, save its draft, then review it to unlock the next chapter. Revising an earlier draft invalidates later reviews.</p><label className="block">Drafting chapter<select className={control} disabled={busy} value={chapterId} onChange={e => void selectChapter(e.target.value)}><option value="">Choose chapter</option>{selected.volumes.flatMap(v => v.chapters).map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label>
        {status && <><p>Chapter stage: {status.stage}</p><p>Review applies to the saved active draft.</p>{status.missing && <p>Writing work missing</p>}{status.draft_missing && <p>Chapter draft missing</p>}{!status.ready_to_draft && <p>Review preceding chapters first</p>}{status.work_id ? <a href={`#/capabilities/creative?view=works&work=${status.work_id}`}>Open chapter writing work</a> : <Button disabled={busy} onClick={() => void operation('prepare')}>Prepare chapter work</Button>}
          {status.work_id && !status.missing && <><label className="block">Chapter drafting mode<select className={control} value={mode} onChange={e => { setMode(e.target.value); setDraftRequest(crypto.randomUUID()) }}><option value="authored">My manuscript</option><option value="model">Configured model</option></select></label>{mode === 'authored' ? <label className="block">Chapter manuscript<textarea className={`${control} min-h-56`} disabled={busy} value={manuscript} onChange={e => { setManuscript(e.target.value); setDraftRequest(crypto.randomUUID()) }} /></label> : <label className="block">Chapter drafting instruction<textarea className={control} value={instruction} onChange={e => { setInstruction(e.target.value); setDraftRequest(crypto.randomUUID()) }} /></label>}<Button disabled={busy || !status.ready_to_draft || (mode === 'authored' && !manuscript.trim())} onClick={() => void operation('draft')}>Save staged chapter draft</Button><Button disabled={busy || !status.active_draft_id || !status.ready_to_draft || status.draft_missing} onClick={() => void operation('review')}>Mark chapter reviewed</Button></>}
        </>}
      </section><section aria-label="Series history">{history.map(s => <p key={s.revision}>Revision {s.revision}: {s.title}<Button disabled={busy || s.revision === selected.revision} onClick={() => void save(s.revision)}>Restore series revision {s.revision}</Button></p>)}</section></>}
    </section></div></main>
}
