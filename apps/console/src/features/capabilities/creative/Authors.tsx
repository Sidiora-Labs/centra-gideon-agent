import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Sample = { artifact_id: string; artifact_version: number }
type Voice = { perspective: string; tense: string; tone: string; diction: string; rhythm: string; avoid: string }
type Values = { title: string; biography: string; voice: Voice; sample_refs: Sample[] }
type Author = Values & { id: string; revision: number; sample_status?: (Sample & { missing: boolean; title: string })[] }
type Brief = { title: string; revision: number; voice: Voice; biography: string; samples: (Sample & { title: string; content: string; missing: boolean; truncated: boolean; original_characters: number })[] }
const blank = (): Values => ({ title: '', biography: '', voice: { perspective: 'any', tense: 'any', tone: '', diction: '', rhythm: '', avoid: '' }, sample_refs: [] })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('author') || ''
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
const values = (a: Author): Values => ({ title: a.title, biography: a.biography, voice: a.voice, sample_refs: a.sample_refs })

export default function Authors({ apiRoot = '/api/capabilities/creative/authors' }: { apiRoot?: string }) {
  const [id, setId] = useState(readId)
  const [selected, setSelected] = useState<Author | null>(null)
  const [draft, setDraft] = useState<Values>(blank)
  const [items, setItems] = useState<Author[]>([])
  const [history, setHistory] = useState<Author[]>([])
  const [samples, setSamples] = useState<{ id: string; title: string; version: number }[]>([])
  const [query, setQuery] = useState('')
  const [sampleQuery, setSampleQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [brief, setBrief] = useState<Brief | null>(null)
  const [exported, setExported] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Unable to load authors')
  function choose(next: string) {
    location.hash = `/capabilities/creative?view=authors${next ? `&author=${next}` : ''}`
    setId(next); setError(''); setBrief(null); setExported('')
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setRequestId(crypto.randomUUID()) }
  }
  useEffect(() => { const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
  useEffect(() => {
    let alive = true; setLoading(true)
    Promise.all([requestJson<{ items: Author[]; total: number }>(`${apiRoot}?q=${encodeURIComponent(query)}&offset=${offset}&limit=25`), requestJson<{ items: { id: string; title: string; version: number }[] }>(`${apiRoot}/sources?q=${encodeURIComponent(sampleQuery)}`)])
      .then(([list, sources]) => { if (alive) { setItems(list.items); setTotal(list.total); setSamples(sources.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [apiRoot, query, sampleQuery, offset, refresh])
  useEffect(() => {
    let alive = true
    if (selected?.id !== id) { setSelected(null); setDraft(blank()); setHistory([]); setBrief(null); setExported('') }
    if (!id) { setBusy(false); return }
    setBusy(true)
    Promise.all([requestJson<Author>(`${apiRoot}/${id}`), requestJson<{ items: Author[] }>(`${apiRoot}/${id}/revisions`)])
      .then(([record, versions]) => { if (alive) { setSelected(record); setDraft(values(record)); setHistory(versions.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [apiRoot, id, reload])
  async function save(target?: number) {
    setBusy(true); setError('')
    try {
      const record = target && selected ? await requestJson<Author>(`${apiRoot}/${id}/restore`, 'POST', { revision: selected.revision, target_revision: target })
        : await requestJson<Author>(`${apiRoot}${selected ? `/${id}` : ''}`, selected ? 'PATCH' : 'POST', { ...draft, ...(selected ? { revision: selected.revision } : { request_id: requestId }) })
      const [detail, versions] = await Promise.all([requestJson<Author>(`${apiRoot}/${record.id}`), requestJson<{ items: Author[] }>(`${apiRoot}/${record.id}/revisions`)])
      setSelected(detail); setDraft(values(detail)); setHistory(versions.items); choose(record.id); setRefresh(v => v + 1)
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function readOutput(kind: 'brief' | 'export') {
    try {
      const result = await requestJson<Brief>(`${apiRoot}/${id}/${kind}?revision=${selected!.revision}`)
      if (kind === 'brief') setBrief(result)
      else {
        const json = JSON.stringify(result, null, 2); setExported(json)
        const a = document.createElement('a'); a.href = `data:application/json;charset=utf-8,${encodeURIComponent(json)}`; a.download = `author-${id}.json`; a.click()
      }
    } catch (e) { fail(e) }
  }
  return <main className="space-y-4 p-4 text-on-surface"><h1 className="text-xl font-semibold">Literary authors</h1>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setRefresh(v => v + 1); setReload(v => v + 1) }}>Retry</Button></div>}
    {loading && <p>Loading authors…</p>}
    <div className="grid gap-4 lg:grid-cols-[18rem_1fr]"><aside className="space-y-3"><label>Search authors<input className={control} value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} /></label>
      <Button onClick={() => choose('')}>New author</Button>{!loading && !items.length && <p>No authors found.</p>}
      {items.map(item => <Button key={item.id} onClick={() => choose(item.id)}>{item.title}</Button>)}
      <p>{total} authors</p><Button disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>Previous page</Button><Button disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>Next page</Button>
    </aside><section className="min-w-0 space-y-3">
      {selected && <p>Author revision {selected.revision}</p>}
      <label className="block">Author name<input className={control} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
      <label className="block">Author biography<textarea className={control} value={draft.biography} onChange={e => setDraft({ ...draft, biography: e.target.value })} /></label>
      <label className="block">Perspective<select className={control} value={draft.voice.perspective} onChange={e => setDraft({ ...draft, voice: { ...draft.voice, perspective: e.target.value } })}><option value="any">Any perspective</option><option value="first">First person</option><option value="third">Third person</option></select></label>
      <label className="block">Tense<select className={control} value={draft.voice.tense} onChange={e => setDraft({ ...draft, voice: { ...draft.voice, tense: e.target.value } })}><option value="any">Any tense</option><option value="past">Past</option><option value="present">Present</option></select></label>
      {(['tone', 'diction', 'rhythm', 'avoid'] as const).map(field => <label key={field} className="block">{field === 'avoid' ? 'Avoid in writing' : field[0].toUpperCase() + field.slice(1)}<textarea className={control} value={draft.voice[field]} onChange={e => setDraft({ ...draft, voice: { ...draft.voice, [field]: e.target.value } })} /></label>)}
      <label className="block">Search writing samples<input className={control} value={sampleQuery} onChange={e => setSampleQuery(e.target.value)} /></label>
      <label className="block">Pin writing sample<select className={control} value="" onChange={e => { const sample = samples.find(s => s.id === e.target.value); if (sample && !draft.sample_refs.some(s => s.artifact_id === sample.id && s.artifact_version === sample.version)) setDraft({ ...draft, sample_refs: [...draft.sample_refs, { artifact_id: sample.id, artifact_version: sample.version }] }) }}><option value="">Choose text artifact</option>{samples.map(sample => <option key={sample.id} value={sample.id}>{sample.title} · version {sample.version}</option>)}</select></label>
      {draft.sample_refs.map(ref => <p key={`${ref.artifact_id}:${ref.artifact_version}`} className="break-all">{selected?.sample_status?.find(s => s.artifact_id === ref.artifact_id && s.artifact_version === ref.artifact_version)?.title || ref.artifact_id} · pinned version {ref.artifact_version}{selected?.sample_status?.some(s => s.artifact_id === ref.artifact_id && s.artifact_version === ref.artifact_version && s.missing) && ' — Sample missing'}<Button onClick={() => setDraft({ ...draft, sample_refs: draft.sample_refs.filter(s => s !== ref) })}>Unpin sample {ref.artifact_id}</Button></p>)}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>Save author</Button>
      {selected && <><Button disabled={busy} onClick={() => void readOutput('brief')}>Build voice brief</Button><Button disabled={busy} onClick={() => void readOutput('export')}>Export author</Button><section aria-label="Author history">{history.map(item => <p key={item.revision}>Revision {item.revision}: {item.title}<Button disabled={busy || item.revision === selected.revision} onClick={() => void save(item.revision)}>Restore author revision {item.revision}</Button></p>)}</section></>}
      {brief && <section aria-label="Configured voice brief"><h2>Configured voice brief · revision {brief.revision}</h2><p>{brief.biography}</p>{Object.entries(brief.voice).map(([key, value]) => <p key={key}>{key}: {value}</p>)}{brief.samples.map(sample => <article key={`${sample.artifact_id}:${sample.artifact_version}`}><h3>{sample.title} · version {sample.artifact_version}</h3>{sample.missing ? <p>Sample missing</p> : <><pre className="whitespace-pre-wrap break-words">{sample.content}</pre><p>{sample.original_characters} source characters{sample.truncated && ' · Excerpt limited to 4000 characters'}</p></>}</article>)}</section>}
      {exported && <label className="block">Author export<textarea className={control} readOnly value={exported} /></label>}
    </section></div></main>
}
