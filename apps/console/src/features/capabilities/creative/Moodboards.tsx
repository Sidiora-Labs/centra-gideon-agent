import { TextInput, Select, TextArea } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Images, Plus } from 'lucide-react'
import { EmptyState, ListRow, ListScaffold } from '../../../shared/ui/ListScaffold'

type Card = { id: string; artifact_id: string; artifact_version: number; caption: string; colors: string[]; provenance?: { title: string; kind: string; added_at: string } }
type Group = { id: string; title: string; cards: Card[] }
type Board = { id: string; title: string; revision: number; groups: Group[]; ingredient_ids: string[]; ingredient_status?: { id: string; missing: boolean }[]; source_status?: { card_id: string; missing: boolean; preview_url: string | null }[] }
type Source = { id: string; title: string; version: number; kind: string }
const blank = () => ({ title: '', groups: [] as Group[], ingredient_ids: [] as string[] })
const control = 'w-full rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary'
const readId = () => new URLSearchParams(location.hash.split('?')[1]).get('board') || ''
function editable(board: ReturnType<typeof blank>) {
  return { ...board, groups: board.groups.map(group => ({ ...group, cards: group.cards.map(({ provenance: _provenance, ...card }) => card) })) }
}
function move<T>(items: T[], index: number, delta: number): T[] {
  const result = [...items]
  const destination = index + delta
  if (destination >= 0 && destination < result.length) [result[index], result[destination]] = [result[destination], result[index]]
  return result
}
function Colors({ label, colors, onChange }: { label: string; colors: string[]; onChange: (colors: string[]) => void }) {
  const [value, setValue] = useState(colors.join(', '))
  const serialized = colors.join(', ')
  useEffect(() => setValue(serialized), [serialized])
  return <label className="block">{label}<TextInput className={control} placeholder="#112233, #aabbcc" value={value} onChange={nextValue => {
    setValue(nextValue); onChange(nextValue.split(',').map(s => s.trim()).filter(Boolean))
  }} /></label>
}

export default function Moodboards({ apiRoot = '/api/capabilities/creative/boards', boardId, onSelectBoard }: { apiRoot?: string; boardId?: string; onSelectBoard?: (id: string) => void }) {
  const t = (value: string) => value
  const [boards, setBoards] = useState<Board[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [ingredients, setIngredients] = useState<{ id: string; title: string }[]>([])
  const [selected, setSelected] = useState<Board | null>(null)
  const [draft, setDraft] = useState(blank)
  const [history, setHistory] = useState<Board[]>([])
  const [id, setId] = useState(() => boardId ?? readId())
  const [query, setQuery] = useState('')
  const [sourceQuery, setSourceQuery] = useState('')
  const [ingredientQuery, setIngredientQuery] = useState('')
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)
  const [refresh, setRefresh] = useState(0)
  const [reload, setReload] = useState(0)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [exported, setExported] = useState('')
  const [creating, setCreating] = useState(true)
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Unable to load moodboards'))
  function choose(next: string) {
    if (onSelectBoard) onSelectBoard(next)
    else location.hash = `/capabilities/creative?view=boards${next ? `&board=${encodeURIComponent(next)}` : ''}`
    setId(next); setCreating(true); setError(''); setExported('')
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setRequestId(crypto.randomUUID()) }
  }
  function startNew() { choose(''); setCreating(true) }
  useEffect(() => { if (boardId !== undefined) setId(boardId) }, [boardId])
  useEffect(() => { if (onSelectBoard) return; const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [onSelectBoard])
  useEffect(() => {
    let alive = true
    setLoading(true)
    Promise.all([requestJson<{ items: Board[]; total: number }>(`${apiRoot}?q=${encodeURIComponent(query)}&limit=25&offset=${offset}`),
      requestJson<{ items: Source[] }>(`${apiRoot}/sources?q=${encodeURIComponent(sourceQuery)}`),
      requestJson<{ items: { id: string; title: string }[] }>(`${apiRoot.replace(/boards$/, 'ingredients')}?limit=100&q=${encodeURIComponent(ingredientQuery)}`)])
      .then(([list, refs, catalog]) => { if (alive) { setBoards(list.items); setTotal(list.total); setSources(refs.items); setIngredients(catalog.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [apiRoot, query, sourceQuery, ingredientQuery, offset, refresh])
  useEffect(() => {
    let alive = true
    if (selected?.id !== id) { setSelected(null); setDraft(blank()); setHistory([]) }
    if (!id) { setBusy(false); return }
    setBusy(true)
    Promise.all([requestJson<Board>(`${apiRoot}/${id}`), requestJson<{ items: Board[] }>(`${apiRoot}/${id}/revisions`)])
      .then(([board, versions]) => { if (alive) { setSelected(board); setDraft({ title: board.title, groups: board.groups, ingredient_ids: board.ingredient_ids }); setHistory(versions.items) } })
      .catch(e => { if (alive) fail(e) }).finally(() => { if (alive) setBusy(false) })
    return () => { alive = false }
  }, [apiRoot, id, reload])
  async function save(target?: number) {
    setBusy(true); setError('')
    try {
      const record = target && selected ? await requestJson<Board>(`${apiRoot}/${id}/restore`, 'POST', { revision: selected.revision, target_revision: target })
        : await requestJson<Board>(selected ? `${apiRoot}/${id}` : apiRoot, selected ? 'PATCH' : 'POST', { ...editable(draft), ...(selected ? { revision: selected.revision } : { request_id: requestId }) })
      const [detail, versions] = await Promise.all([requestJson<Board>(`${apiRoot}/${record.id}`), requestJson<{ items: Board[] }>(`${apiRoot}/${record.id}/revisions`)])
      setSelected(detail); setDraft({ title: detail.title, groups: detail.groups, ingredient_ids: detail.ingredient_ids }); setHistory(versions.items)
      choose(record.id); setRefresh(n => n + 1)
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  function groupChange(index: number, group: Group) { setDraft({ ...draft, groups: draft.groups.map((g, i) => i === index ? group : g) }) }
  async function exportBoard() {
    try {
      const body = JSON.stringify(await requestJson(`${apiRoot}/${id}/export?revision=${selected!.revision}`), null, 2)
      setExported(body)
      const url = URL.createObjectURL(new Blob([body], { type: 'application/json' }))
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = `moodboard-${id}.json`; anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { fail(e) }
  }
  return <ListScaffold title={t('Moodboards')} right={<Button disabled={busy} onClick={startNew} disabledReason={busy ? BUSY_REASON : undefined}><Plus className="h-4 w-4" />{t('New moodboard')}</Button>}><main className="space-y-xl text-on-surface">
    <p data-type="body-m" className="text-on-surface-low">{t('Arrange saved artifacts into inspiration groups. Each card keeps its original source version.')}</p>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setRefresh(n => n + 1); setReload(n => n + 1) }}>{t('Retry moodboards')}</Button></div>}
    <label className="block">{t('Search boards')}<TextInput className={control} value={query} onChange={nextValue => { setQuery(nextValue); setOffset(0) }} /></label>
    {loading ? <p role="status">{t('Loading moodboards…')}</p> : !boards.length ? <EmptyState icon={Images} title={t('No moodboards yet.')} hint={t('Group pinned visual references into a reusable board.')} action={{ label: t('New moodboard'), onClick: startNew, icon: Plus }} /> : <div className="space-y-2">{boards.map((board, index) => <ListRow key={board.id} index={index} onClick={() => choose(board.id)} label={board.title}><Images className="h-5 w-5 shrink-0 text-primary" /><div className="min-w-0"><p className="font-medium">{board.title}</p><p data-type="body-s" className="text-on-surface-var">{t('Moodboard revision')} {board.revision} · {board.groups.length} {t('Group')}</p></div></ListRow>)}</div>}
    <p>{total} {t('moodboards')}</p><Button disabled={loading || offset === 0} disabledReason={loading ? BUSY_REASON : undefined} onClick={() => setOffset(n => n - 25)}>{t('Previous boards')}</Button><Button disabled={loading || offset + 25 >= total} disabledReason={loading ? BUSY_REASON : undefined} onClick={() => setOffset(n => n + 25)}>{t('Next boards')}</Button>
    {(creating || selected) ? <section aria-label={selected ? t('Moodboard revision') : t('Create moodboard')} className="space-y-l rounded-lg bg-surface-container p-l"><h2 data-type="title-m">{selected ? `${t('Moodboard revision')} ${selected.revision}` : t('Create moodboard')}</h2>
    <label className="block">{t('Board title')}<TextInput className={control} value={draft.title} maxLength={200} onChange={nextValue => setDraft({ ...draft, title: nextValue })} /></label>
    <label className="block">{t('Find ingredients')}<TextInput className={control} value={ingredientQuery} onChange={nextValue => setIngredientQuery(nextValue)} /></label>
    <label className="block">{t('Link ingredient')}<Select className={control} value="" onChange={nextValue => setDraft({ ...draft, ingredient_ids: [...new Set([...draft.ingredient_ids, nextValue])] })} options={[{value: "", label: String(t('Choose ingredient')), disabled: true}, ...(ingredients.map(i => ({value: i.id, label: String(i.title)})) ?? [])]} /></label>
    {draft.ingredient_ids.map(link => <p key={link}>{ingredients.find(i => i.id === link)?.title || `${selected?.ingredient_status?.find(i => i.id === link)?.missing ? t('Ingredient missing') : t('Ingredient')}: ${link}`} <Button onClick={() => setDraft({ ...draft, ingredient_ids: draft.ingredient_ids.filter(x => x !== link) })}>{t('Unlink ingredient')}</Button></p>)}
    <Button onClick={() => setDraft({ ...draft, groups: [...draft.groups, { id: crypto.randomUUID(), title: `${t('Group')} ${draft.groups.length + 1}`, cards: [] }] })}>{t('Add group')}</Button>
    <label className="block">{t('Find source artifacts')}<TextInput className={control} value={sourceQuery} onChange={nextValue => setSourceQuery(nextValue)} /></label>
    {draft.groups.map((group, index) => <section key={group.id} className="space-y-2 rounded-md bg-surface-high p-l" aria-label={`${t('Group')} ${index + 1}`}>
      <label className="block">{t('Group name')} {index + 1}<TextInput className={control} value={group.title} onChange={nextValue => groupChange(index, { ...group, title: nextValue })} /></label>
      <Button disabled={index === 0} onClick={() => setDraft({ ...draft, groups: move(draft.groups, index, -1) })} disabledReason={index === 0 ? 'This item is already first and cannot be moved earlier.' : undefined}>{t('Move group up')}</Button>
      <Button disabled={index === draft.groups.length - 1} onClick={() => setDraft({ ...draft, groups: move(draft.groups, index, 1) })} disabledReason={index === draft.groups.length - 1 ? 'This item is already last and cannot be moved later.' : undefined}>{t('Move group down')}</Button>
      <Button onClick={() => setDraft({ ...draft, groups: draft.groups.filter(g => g.id !== group.id) })}>{t('Remove group')}</Button>
      <label className="block">{t('Add source to group')} {index + 1}<Select className={control} value="" onChange={nextValue => { const source = sources.find(s => s.id === nextValue)!; groupChange(index, { ...group, cards: [...group.cards, { id: crypto.randomUUID(), artifact_id: source.id, artifact_version: source.version, caption: '', colors: [] }] }) }} options={[{value: "", label: String(t('Choose saved artifact')), disabled: true}, ...(sources.map(s => ({value: s.id, label: String(s.title) + " · " + String(t('version')) + " " + String(s.version)})) ?? [])]} /></label>
      <div className="grid gap-3 sm:grid-cols-2">{group.cards.map((card, position) => {
        const status = selected?.source_status?.find(s => s.card_id === card.id)
        const update = (next: Card) => groupChange(index, { ...group, cards: group.cards.map(c => c.id === card.id ? next : c) })
        return <article key={card.id} className="min-w-0 space-y-2 rounded-md bg-surface-high p-m" aria-label={`${t('Card')} ${index + 1}.${position + 1}`}>
          <p>{card.provenance?.title || sources.find(s => s.id === card.artifact_id)?.title || card.artifact_id} · {t('version')} {card.artifact_version}</p>
          {status?.missing ? <p>{t('Source missing')}</p> : status?.preview_url && <img alt={card.caption || t('Inspiration reference')} className="max-h-64 w-full object-contain" src={new URL(status.preview_url, new URL(apiRoot, location.origin)).href} />}
          <label className="block">{t('Caption')} {index + 1}.{position + 1}<TextArea className={control} value={card.caption} onChange={nextValue => update({ ...card, caption: nextValue })} /></label>
          <Colors key={`${card.id}:${selected?.revision || 0}`} label={`${t('Colors')} ${index + 1}.${position + 1}`} colors={card.colors} onChange={colors => update({ ...card, colors })} />
          <div className="flex gap-1">{card.colors.filter(color => /^#[a-fA-F0-9]{6}$/.test(color)).map((color, n) => <span key={n} role="img" aria-label={color} className="h-5 w-5 rounded" style={{ backgroundColor: color }} />)}</div>
          <Button disabled={position === 0} onClick={() => groupChange(index, { ...group, cards: move(group.cards, position, -1) })} disabledReason={position === 0 ? 'This item is already first and cannot be moved earlier.' : undefined}>{t('Move card up')}</Button>
          <Button disabled={position === group.cards.length - 1} onClick={() => groupChange(index, { ...group, cards: move(group.cards, position, 1) })} disabledReason={position === group.cards.length - 1 ? 'This item is already last and cannot be moved later.' : undefined}>{t('Move card down')}</Button>
          <Button onClick={() => groupChange(index, { ...group, cards: group.cards.filter(c => c.id !== card.id) })}>{t('Remove card')}</Button>
        </article>
      })}</div>
    </section>)}
    <Button disabled={busy || !draft.title.trim()} onClick={() => void save()} disabledReason={busy ? BUSY_REASON : undefined}>{t('Save moodboard')}</Button>
    {selected && <Button disabled={busy} onClick={() => void exportBoard()} disabledReason={busy ? BUSY_REASON : undefined}>{t('Export moodboard')}</Button>}
    {exported && <TextArea ariaLabel={t('Exported moodboard')} readOnly className={control} rows={8} value={exported} onChange={() => {}} />}
    {history.length > 0 && <section aria-label={t('Board revisions')}><h3>{t('Previous revisions')}</h3>{history.map(r => <p key={r.revision}>{r.title} · {r.revision} <Button disabled={busy || r.revision === selected?.revision} onClick={() => void save(r.revision)} disabledReason={busy ? BUSY_REASON : undefined}>{t('Restore board revision')} {r.revision}</Button></p>)}</section>}
    </section> : boards.length > 0 && <EmptyState icon={Images} title={t('Choose a moodboard')} hint={t('Select a board to review its pinned sources and revision history.')} action={{ label: t('New moodboard'), onClick: startNew, icon: Plus }} />}
  </main></ListScaffold>
}
