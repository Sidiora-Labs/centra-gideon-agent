import { TextInput, TextArea, Select } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import UniverseGraph from './UniverseGraph'
import { useEffect, useState } from 'react'
import { Globe2, Plus } from 'lucide-react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListScaffold } from '../../../shared/ui/ListScaffold'
import { Surface } from '../../../shared/ui/Surface'
import { ResultAnnouncement } from '../../../shared/ui/ListControls'
import { SearchField } from '../../../shared/ui/SearchField'
import { HeaderActions, HeaderControl } from '../../../shared/ui/HeaderActions'

type Entry = { id: string; title: string; body: string }
type Ref = { id: string; revision: number }
type Values = { title: string; canon: Entry[]; visual_identity: { colors: string[]; style_notes: string }; ingredient_ids: string[]; board_refs: Ref[] }
type Universe = Values & { id: string; revision: number; ingredient_status?: { id: string; missing: boolean }[]; board_status?: (Ref & { missing: boolean })[] }
const blank = (): Values => ({ title: '', canon: [], visual_identity: { colors: [], style_notes: '' }, ingredient_ids: [], board_refs: [] })
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('universe') || ''
const control = 'min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none transition-colors placeholder:text-on-surface-low focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
const values = (item: Universe): Values => ({ title: item.title, canon: item.canon, visual_identity: item.visual_identity, ingredient_ids: item.ingredient_ids, board_refs: item.board_refs })

export default function Universes({ apiRoot = '/api/capabilities/creative/universes', universeId, onSelectUniverse }: { apiRoot?: string; universeId?: string; onSelectUniverse?: (id: string) => void }) {
  const t = (value: string) => value
  const [id, setId] = useState(() => universeId ?? readId())
  const [selected, setSelected] = useState<Universe | null>(null)
  const [draft, setDraft] = useState<Values>(blank)
  const [palette, setPalette] = useState('')
  const [items, setItems] = useState<Universe[]>([])
  const [history, setHistory] = useState<Universe[]>([])
  const [ingredients, setIngredients] = useState<{ id: string; title: string }[]>([])
  const [boards, setBoards] = useState<(Ref & { title: string })[]>([])
  const [query, setQuery] = useState('')
  const [references, setReferences] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [reload, setReload] = useState(0)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [resultRequest, setResultRequest] = useState('')
  const [creating, setCreating] = useState(true)
  const [error, setError] = useState('')
  const [exported, setExported] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const resultKey = JSON.stringify([apiRoot, query, references, offset, refresh])
  const resultsReady = resultRequest === resultKey && !loading && !error
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Unable to load universes'))
  function apply(record: Universe) { setSelected(record); setDraft(values(record)); setPalette(record.visual_identity.colors.join(', ')) }
  function choose(next: string) {
    if (onSelectUniverse) onSelectUniverse(next)
    else location.hash = `/capabilities/creative?view=universes${next ? `&universe=${encodeURIComponent(next)}` : ''}`
    setId(next); setError(''); setExported(''); setCreating(true)
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setPalette(''); setRequestId(crypto.randomUUID()) }
  }
  function startNew() { choose(''); setCreating(true) }
  useEffect(() => { if (universeId !== undefined) setId(universeId) }, [universeId])
  useEffect(() => { if (onSelectUniverse) return; const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [onSelectUniverse])
  useEffect(() => {
    let alive = true
    setLoading(true); setResultRequest('')
    Promise.all([requestJson<{ items: Universe[]; total: number }>(`${apiRoot}?q=${encodeURIComponent(query)}&offset=${offset}&limit=25`),
      requestJson<{ items: { id: string; title: string }[] }>(`${apiRoot.replace(/universes$/, 'ingredients')}?q=${encodeURIComponent(references)}&limit=100`),
      requestJson<{ items: (Ref & { title: string })[] }>(`${apiRoot.replace(/universes$/, 'boards')}?q=${encodeURIComponent(references)}&limit=100`)])
      .then(([list, catalog, moodboards]) => { if (alive) { setResultRequest(resultKey); setItems(list.items); setTotal(list.total); setIngredients(catalog.items); setBoards(moodboards.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [apiRoot, query, references, offset, refresh])
  useEffect(() => {
    let alive = true
    if (selected?.id !== id) { setSelected(null); setDraft(blank()); setHistory([]); setPalette('') }
    if (!id) { setBusy(false); return }
    setBusy(true)
    Promise.all([requestJson<Universe>(`${apiRoot}/${id}`), requestJson<{ items: Universe[] }>(`${apiRoot}/${id}/revisions`)])
      .then(([record, versions]) => { if (alive) { apply(record); setHistory(versions.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [apiRoot, id, reload])
  async function save(target?: number) {
    setBusy(true); setError('')
    try {
      const payload = { ...draft, visual_identity: { ...draft.visual_identity, colors: palette.split(',').map(c => c.trim()).filter(Boolean) } }
      const record = target && selected ? await requestJson<Universe>(`${apiRoot}/${id}/restore`, 'POST', { revision: selected.revision, target_revision: target })
        : await requestJson<Universe>(`${apiRoot}${selected ? `/${id}` : ''}`, selected ? 'PATCH' : 'POST', { ...payload, ...(selected ? { revision: selected.revision } : { request_id: requestId }) })
      const [detail, versions] = await Promise.all([requestJson<Universe>(`${apiRoot}/${record.id}`), requestJson<{ items: Universe[] }>(`${apiRoot}/${record.id}/revisions`)])
      apply(detail); setHistory(versions.items); choose(record.id); setRefresh(v => v + 1)
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function download() {
    try {
      const value = await requestJson<Universe>(`${apiRoot}/${id}/export?revision=${selected!.revision}`)
      const json = JSON.stringify(value, null, 2); setExported(json)
      const href = `data:application/json;charset=utf-8,${encodeURIComponent(json)}`
      const anchor = document.createElement('a'); anchor.href = href; anchor.download = `universe-${id}.json`; anchor.click()
    } catch (e) { fail(e) }
  }
  function entry(index: number, changes: Partial<Entry>) { setDraft({ ...draft, canon: draft.canon.map((item, i) => i === index ? { ...item, ...changes } : item) }) }
  function move(index: number, delta: number) {
    const canon = [...draft.canon]; const next = index + delta
    if (next >= 0 && next < canon.length) { [canon[index], canon[next]] = [canon[next], canon[index]]; setDraft({ ...draft, canon }) }
  }
  const showEditor = !!selected || creating || (!loading && items.length === 0)
  return <ListScaffold title={t('Universes')} right={<HeaderActions><HeaderControl icon={Plus} label={t('New universe')} variant="primary" priority="primary" disabled={busy} onClick={startNew} /></HeaderActions>}>
    <p data-type="body-m" className="mb-l max-w-[48rem] text-on-surface-low">{t('Keep story canon, visual direction, ingredients, and inspiration boards together in one reusable world.')}</p>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setRefresh(v => v + 1); setReload(v => v + 1) }}>{t('Retry')}</Button></div>}
    {loading && <p>{t('Loading universes…')}</p>}
    <div className="grid min-w-0 gap-l lg:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]"><Surface className="h-fit p-l"><aside aria-label={t('Universes')} className="space-y-m">
      <SearchField value={query} onChange={value => { setQuery(value); setOffset(0) }} placeholder={t('Search universes')} ariaLabel={t('Search universes')} surface="container" /><ResultAnnouncement count={items.length} noun="universes" singular="universe" active={resultsReady && !!query.trim()} />
      {!loading && !items.length && <p className="text-on-surface-low">{t('No universes found.')}</p>}
      <div className="space-y-xs">{items.map(item => <Button key={item.id} variant={selected?.id === item.id ? 'tonal' : 'ghost'} ariaPressed={selected?.id === item.id} className="w-full justify-start" onClick={() => choose(item.id)}>{item.title}</Button>)}</div>
      <p className="text-on-surface-low">{total} {t('universes')}</p><div className="flex flex-wrap gap-s"><Button size="sm" variant="secondary" disabled={!offset} onClick={() => setOffset(v => Math.max(0, v - 25))} disabledReason={!offset ? 'This is the first page; there is no previous page.' : undefined}>{t('Previous page')}</Button><Button size="sm" variant="secondary" disabled={offset + 25 >= total} onClick={() => setOffset(v => v + 25)} disabledReason={offset + 25 >= total ? 'There is no next page.' : undefined}>{t('Next page')}</Button></div>
    </aside></Surface><section className="min-w-0 space-y-l">{!showEditor ? <Surface className="p-xl"><EmptyState icon={Globe2} title={t('Choose a universe')} hint={t('Select a universe to edit its canon, visual identity, and references.')} action={{ label: t('New universe'), onClick: startNew, icon: Plus }} /></Surface> : <Surface className="space-y-m p-l">
      {selected && <p className="text-on-surface-low">{t('Universe revision')} {selected.revision}</p>}
      <div><h2 data-type="title-m" className="text-on-surface">{selected ? draft.title || t('Untitled universe') : t('New universe')}</h2><p className="text-on-surface-low">{t('Define the durable canon and the visual references used throughout this world.')}</p></div>
      <label className="block">{t('Universe title')}<TextInput className={control} value={draft.title} onChange={nextValue => setDraft({ ...draft, title: nextValue })} /></label>
      <Button onClick={() => setDraft({ ...draft, canon: [...draft.canon, { id: crypto.randomUUID(), title: '', body: '' }] })}>{t('Add canon entry')}</Button>
      {draft.canon.map((item, index) => <fieldset key={item.id} className="space-y-m border-t border-outline-variant/20 pt-l"><legend>{t('Canon entry')} {index + 1}</legend>
        <label className="block">{t('Canon title')} {index + 1}<TextInput className={control} value={item.title} onChange={nextValue => entry(index, { title: nextValue })} /></label>
        <label className="block">{t('Canon text')} {index + 1}<TextArea className={control} value={item.body} onChange={nextValue => entry(index, { body: nextValue })} /></label>
        <Button disabled={!index} onClick={() => move(index, -1)} disabledReason={!index ? 'This item is already first and cannot be moved earlier.' : undefined}>{t('Move entry')} {index + 1} {t('up')}</Button><Button disabled={index === draft.canon.length - 1} onClick={() => move(index, 1)} disabledReason={index === draft.canon.length - 1 ? 'This item is already last and cannot be moved later.' : undefined}>{t('Move entry')} {index + 1} {t('down')}</Button>
        <Button onClick={() => setDraft({ ...draft, canon: draft.canon.filter((_, i) => i !== index) })}>{t('Remove entry')} {index + 1}</Button>
      </fieldset>)}
      <label className="block">{t('Universe colors')}<TextInput className={control} value={palette} placeholder="#112233, #aabbcc" onChange={nextValue => setPalette(nextValue)} /></label>
      <div className="flex gap-2" aria-label={t('Universe palette')}>{palette.split(',').map(color => color.trim()).filter(color => /^#[0-9a-fA-F]{6}$/.test(color)).map((color, index) => <span key={index} className="h-6 w-6 rounded border" style={{ backgroundColor: color }} title={color} />)}</div>
      <label className="block">{t('Visual style notes')}<TextArea className={control} value={draft.visual_identity.style_notes} onChange={nextValue => setDraft({ ...draft, visual_identity: { ...draft.visual_identity, style_notes: nextValue } })} /></label>
      <SearchField value={references} onChange={setReferences} placeholder={t('Search library references')} ariaLabel={t('Search library references')} surface="container" /><ResultAnnouncement count={ingredients.length} noun="ingredients" active={resultsReady && !!references.trim()} /><ResultAnnouncement count={boards.length} noun="moodboards" active={resultsReady && !!references.trim()} />
      <label className="block">{t('Link canon ingredient')}<Select className={control} value="" onChange={nextValue => { if (nextValue && !draft.ingredient_ids.includes(nextValue)) setDraft({ ...draft, ingredient_ids: [...draft.ingredient_ids, nextValue] }) }} options={[{value: "", label: String(t('Choose ingredient'))}, ...(ingredients.map(item => ({value: item.id, label: String(item.title)})) ?? [])]} /></label>
      {draft.ingredient_ids.map(link => <p key={link} className="break-all"><a href={`#/capabilities/creative?ingredient=${link}`}>{ingredients.find(i => i.id === link)?.title || link}</a>{selected?.ingredient_status?.find(s => s.id === link)?.missing && ` — ${t('Ingredient missing')}`}<Button onClick={() => setDraft({ ...draft, ingredient_ids: draft.ingredient_ids.filter(id => id !== link) })}>{t('Unlink ingredient')} {link}</Button></p>)}
      <label className="block">{t('Pin moodboard')}<Select className={control} value="" onChange={nextValue => { const board = boards.find(b => b.id === nextValue); if (board && !draft.board_refs.some(r => r.id === board.id && r.revision === board.revision)) setDraft({ ...draft, board_refs: [...draft.board_refs, { id: board.id, revision: board.revision }] }) }} options={[{value: "", label: String(t('Choose moodboard'))}, ...(boards.map(item => ({value: item.id, label: String(item.title) + " · " + String(t('revision')) + " " + String(item.revision)})) ?? [])]} /></label>
      {draft.board_refs.map(ref => <p key={`${ref.id}:${ref.revision}`} className="break-all">{boards.find(b => b.id === ref.id)?.title || ref.id} · {t('pinned revision')} {ref.revision}{selected?.board_status?.find(s => s.id === ref.id && s.revision === ref.revision)?.missing && ` — ${t('Moodboard missing')}`}<a href={`${apiRoot.replace(/universes$/, 'boards')}/${ref.id}/export?revision=${ref.revision}`}>{t('Open pinned moodboard')}</a><Button onClick={() => setDraft({ ...draft, board_refs: draft.board_refs.filter(r => r !== ref) })}>{t('Unpin moodboard')} {ref.id}</Button></p>)}
      <Button disabled={busy || (!!id && !selected)} onClick={() => void save()} disabledReason={busy ? BUSY_REASON : undefined}>{t('Save universe')}</Button>
      {selected && <><Button disabled={busy} onClick={() => void download()} disabledReason={busy ? BUSY_REASON : undefined}>{t('Export universe')}</Button><section aria-label={t('Universe history')}>{history.map(item => <p key={item.revision}>{t('Revision')} {item.revision}: {item.title}<Button disabled={busy || item.revision === selected.revision} onClick={() => void save(item.revision)} disabledReason={busy ? BUSY_REASON : undefined}>{t('Restore universe revision')} {item.revision}</Button></p>)}</section></>}
      {selected && <UniverseGraph key={selected.id} id={selected.id} revision={selected.revision} apiRoot={apiRoot} onMerged={() => { setReload(v => v + 1); setRefresh(v => v + 1) }} />}
      {exported && <label className="block">{t('Universe export')}<TextArea readOnly className={control} value={exported} onChange={() => {}} /></label>}
    </Surface>}</section></div></ListScaffold>
}
