import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListRow, ListScaffold } from '../../../shared/ui/ListScaffold'
import { FilePenLine, Plus, UserRound } from 'lucide-react'
import { SearchField } from '../../../shared/ui/SearchField'
import { Field, Select, TextArea, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

type Sample = { artifact_id: string; artifact_version: number }
type Voice = { perspective: string; tense: string; tone: string; diction: string; rhythm: string; avoid: string }
type Values = { title: string; biography: string; voice: Voice; sample_refs: Sample[] }
type Author = Values & { id: string; revision: number; sample_status?: (Sample & { missing: boolean; title: string })[] }
type Brief = { title: string; revision: number; voice: Voice; biography: string; samples: (Sample & { title: string; content: string; missing: boolean; truncated: boolean; original_characters: number })[] }
const blank = (): Values => ({ title: '', biography: '', voice: { perspective: 'any', tense: 'any', tone: '', diction: '', rhythm: '', avoid: '' }, sample_refs: [] })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('author') || ''
const values = (a: Author): Values => ({ title: a.title, biography: a.biography, voice: a.voice, sample_refs: a.sample_refs })

export default function Authors({ apiRoot = '/api/capabilities/creative/authors' }: { apiRoot?: string }) {
  const t = (value: string) => value
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
  const [creating, setCreating] = useState(true)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Unable to load authors'))
  function choose(next: string) {
    location.hash = `/capabilities/creative?view=authors${next ? `&author=${next}` : ''}`
    setId(next); setCreating(true); setError(''); setBrief(null); setExported('')
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setRequestId(crypto.randomUUID()) }
  }
  function startNew() {
    choose(''); setCreating(true)
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
  return <ListScaffold title={t('Literary authors')} right={<Button onClick={startNew} disabled={busy}><Plus size={16} aria-hidden/>{t('New author')}</Button>}><p data-type="body-m" className="mb-xl text-on-surface-low">{t('Build reusable voices from authored guidance and pinned writing samples.')}</p>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setRefresh(v => v + 1); setReload(v => v + 1) }}>{t('Retry')}</Button></div>}
    {loading && <p>{t('Loading authors…')}</p>}
    <Surface className="grid min-h-[32rem] lg:grid-cols-[19rem_minmax(0,1fr)]"><aside className="space-y-m border-b border-outline-variant/20 p-l lg:border-b-0 lg:border-r"><SearchField ariaLabel={t('Search authors')} placeholder={t('Search authors')} value={query} onChange={value => { setQuery(value); setOffset(0) }} />
      {!loading && !items.length ? <EmptyState icon={UserRound} title={t('No authors found.')} hint={t(query ? 'Try a different search.' : 'Create an author to reuse a consistent voice across manuscripts.')} action={query ? undefined : { label: t('New author'), onClick: startNew, icon: Plus }}/> : <div className="space-y-2">{items.map((item, index) => <ListRow key={item.id} index={index} onClick={() => choose(item.id)} label={item.title} accent={item.id === id ? 'var(--color-primary)' : undefined}><FilePenLine size={17} className="shrink-0 text-on-surface-low" aria-hidden/><div className="min-w-0 flex-1"><p className="truncate font-medium">{item.title}</p><p className="truncate text-xs text-on-surface-low">{item.voice.perspective} · {item.voice.tense} · {t('revision')} {item.revision}</p></div></ListRow>)}</div>}
      <div className="flex flex-wrap items-center gap-2 border-t border-outline-variant/20 pt-3"><p className="mr-auto text-xs text-on-surface-low">{total} {t('authors')}</p><Button size="sm" variant="ghost" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))}>{t('Previous page')}</Button><Button size="sm" variant="ghost" disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)}>{t('Next page')}</Button></div>
    </aside><section className="min-w-0 space-y-l p-l lg:p-2xl">
      {!selected && !creating ? <EmptyState icon={FilePenLine} title={t('Choose an author')} hint={t('Open a saved author to edit voice guidance, samples, and revision history.')} action={{ label: t('New author'), onClick: startNew, icon: Plus }}/> : <>
      {selected && <p>{t('Author revision')} {selected.revision}</p>}
      <Field label={t('Author name')}><TextInput value={draft.title} onChange={value => setDraft({ ...draft, title: value })} /></Field>
      <Field label={t('Author biography')}><TextArea value={draft.biography} onChange={value => setDraft({ ...draft, biography: value })} /></Field>
      <div className="grid gap-l sm:grid-cols-2"><Field label={t('Perspective')}><Select value={draft.voice.perspective} onChange={value => setDraft({ ...draft, voice: { ...draft.voice, perspective: value } })} options={[{ value: 'any', label: t('Any perspective') }, { value: 'first', label: t('First person') }, { value: 'third', label: t('Third person') }]} /></Field>
      <Field label={t('Tense')}><Select value={draft.voice.tense} onChange={value => setDraft({ ...draft, voice: { ...draft.voice, tense: value } })} options={[{ value: 'any', label: t('Any tense') }, { value: 'past', label: t('Past') }, { value: 'present', label: t('Present') }]} /></Field></div>
      {(['tone', 'diction', 'rhythm', 'avoid'] as const).map(field => <Field key={field} label={t(field === 'avoid' ? 'Avoid in writing' : field[0].toUpperCase() + field.slice(1))}><TextArea value={draft.voice[field]} onChange={value => setDraft({ ...draft, voice: { ...draft.voice, [field]: value } })} /></Field>)}
      <Field label={t('Search writing samples')}><TextInput value={sampleQuery} onChange={setSampleQuery} /></Field>
      <Field label={t('Pin writing sample')}><Select value="" onChange={value => { const sample = samples.find(s => s.id === value); if (sample && !draft.sample_refs.some(s => s.artifact_id === sample.id && s.artifact_version === sample.version)) setDraft({ ...draft, sample_refs: [...draft.sample_refs, { artifact_id: sample.id, artifact_version: sample.version }] }) }} options={[{ value: '', label: t('Choose text artifact') }, ...samples.map(sample => ({ value: sample.id, label: `${sample.title} · ${t('version')} ${sample.version}` }))]} /></Field>
      {draft.sample_refs.map(ref => <p key={`${ref.artifact_id}:${ref.artifact_version}`} className="break-all">{selected?.sample_status?.find(s => s.artifact_id === ref.artifact_id && s.artifact_version === ref.artifact_version)?.title || ref.artifact_id} · {t('pinned version')} {ref.artifact_version}{selected?.sample_status?.some(s => s.artifact_id === ref.artifact_id && s.artifact_version === ref.artifact_version && s.missing) && ` — ${t('Sample missing')}`}<Button onClick={() => setDraft({ ...draft, sample_refs: draft.sample_refs.filter(s => s !== ref) })}>{t('Unpin sample')} {ref.artifact_id}</Button></p>)}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()}>{t('Save author')}</Button>
      {selected && <><Button disabled={busy} onClick={() => void readOutput('brief')}>{t('Build voice brief')}</Button><Button disabled={busy} onClick={() => void readOutput('export')}>{t('Export author')}</Button><section aria-label={t('Author history')}>{history.map(item => <p key={item.revision}>{t('Revision')} {item.revision}: {item.title}<Button disabled={busy || item.revision === selected.revision} onClick={() => void save(item.revision)}>{t('Restore author revision')} {item.revision}</Button></p>)}</section></>}
      {brief && <section aria-label={t('Configured voice brief')}><h2>{t('Configured voice brief')} · {t('revision')} {brief.revision}</h2><p>{brief.biography}</p>{Object.entries(brief.voice).map(([key, value]) => <p key={key}>{t(key)}: {key === 'perspective' || key === 'tense' ? t(value) : value}</p>)}{brief.samples.map(sample => <article key={`${sample.artifact_id}:${sample.artifact_version}`}><h3>{sample.title} · {t('version')} {sample.artifact_version}</h3>{sample.missing ? <p>{t('Sample missing')}</p> : <><pre className="whitespace-pre-wrap break-words">{sample.content}</pre><p>{sample.original_characters} {t('source characters')}{sample.truncated && ` · ${t('Excerpt limited to 4000 characters')}`}</p></>}</article>)}</section>}
      {exported && <Field label={t('Author export')}><textarea aria-label={t('Author export')} readOnly value={exported} rows={8} className="w-full resize-y rounded-md border border-outline-variant/30 bg-surface-container px-m py-s font-mono text-on-surface outline-none focus:ring-2 focus:ring-inset focus:ring-primary" /></Field>}
      </>}
    </section></Surface></ListScaffold>
}
