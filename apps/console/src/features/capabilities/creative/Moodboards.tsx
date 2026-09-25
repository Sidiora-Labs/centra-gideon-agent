import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Card = { id: string; artifact_id: string; artifact_version: number; caption: string; colors: string[]; provenance?: { title: string; kind: string; added_at: string } }
type Group = { id: string; title: string; cards: Card[] }
type Board = { id: string; title: string; revision: number; groups: Group[]; ingredient_ids: string[]; ingredient_status?: { id: string; missing: boolean }[]; source_status?: { card_id: string; missing: boolean; preview_url: string | null }[] }
type Source = { id: string; title: string; version: number; kind: string }
const blank = () => ({ title: '', groups: [] as Group[], ingredient_ids: [] as string[] })
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'
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
  return <label className="block">{label}<input className={control} placeholder="#112233, #aabbcc" value={value} onChange={e => {
    setValue(e.target.value); onChange(e.target.value.split(',').map(s => s.trim()).filter(Boolean))
  }} /></label>
}

export default function Moodboards({ apiRoot = '/api/capabilities/creative/boards' }: { apiRoot?: string }) {
  const [boards, setBoards] = useState<Board[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [ingredients, setIngredients] = useState<{ id: string; title: string }[]>([])
  const [selected, setSelected] = useState<Board | null>(null)
  const [draft, setDraft] = useState(blank)
  const [history, setHistory] = useState<Board[]>([])
  const [id, setId] = useState(readId)
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
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Unable to load moodboards')
  function choose(next: string) {
    location.hash = `/capabilities/creative?view=boards${next ? `&board=${next}` : ''}`
    setId(next); setError(''); setExported('')
    if (!next) { setSelected(null); setDraft(blank()); setHistory([]); setRequestId(crypto.randomUUID()) }
  }
  useEffect(() => { const changed = () => setId(readId()); addEventListener('hashchange', changed); return () => removeEventListener('hashchange', changed) }, [])
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
  return <main className="mx-auto max-w-5xl space-y-4 p-4 text-on-surface">
    <h1 className="text-2xl">Moodboards</h1>
    <p>Arrange saved artifacts into inspiration groups. Each card keeps its original source version.</p>
    {error && <div role="alert">{error}<Button onClick={() => { setError(''); setRefresh(n => n + 1); setReload(n => n + 1) }}>Retry moodboards</Button></div>}
    <label className="block">Search boards<input className={control} value={query} onChange={e => { setQuery(e.target.value); setOffset(0) }} /></label>
    {loading ? <p role="status">Loading moodboards…</p> : !boards.length ? <p>No moodboards yet.</p> : <ul>{boards.map(board => <li key={board.id}><Button disabled={busy} onClick={() => choose(board.id)}>{board.title}</Button></li>)}</ul>}
    <p>{total} moodboards</p><Button disabled={loading || offset === 0} onClick={() => setOffset(n => n - 25)}>Previous boards</Button><Button disabled={loading || offset + 25 >= total} onClick={() => setOffset(n => n + 25)}>Next boards</Button>
    <Button disabled={busy} onClick={() => choose('')}>New moodboard</Button>
    <h2>{selected ? `Moodboard revision ${selected.revision}` : 'Create moodboard'}</h2>
    <label className="block">Board title<input className={control} value={draft.title} maxLength={200} onChange={e => setDraft({ ...draft, title: e.target.value })} /></label>
    <label className="block">Find ingredients<input className={control} value={ingredientQuery} onChange={e => setIngredientQuery(e.target.value)} /></label>
    <label className="block">Link ingredient<select className={control} value="" onChange={e => setDraft({ ...draft, ingredient_ids: [...new Set([...draft.ingredient_ids, e.target.value])] })}><option value="" disabled>Choose ingredient</option>{ingredients.map(i => <option key={i.id} value={i.id}>{i.title}</option>)}</select></label>
    {draft.ingredient_ids.map(link => <p key={link}>{ingredients.find(i => i.id === link)?.title || `${selected?.ingredient_status?.find(i => i.id === link)?.missing ? 'Ingredient missing' : 'Ingredient'}: ${link}`} <Button onClick={() => setDraft({ ...draft, ingredient_ids: draft.ingredient_ids.filter(x => x !== link) })}>Unlink ingredient</Button></p>)}
    <Button onClick={() => setDraft({ ...draft, groups: [...draft.groups, { id: crypto.randomUUID(), title: `Group ${draft.groups.length + 1}`, cards: [] }] })}>Add group</Button>
    <label className="block">Find source artifacts<input className={control} value={sourceQuery} onChange={e => setSourceQuery(e.target.value)} /></label>
    {draft.groups.map((group, index) => <section key={group.id} className="space-y-2 rounded border border-outline p-3" aria-label={`Group ${index + 1}`}>
      <label className="block">Group name {index + 1}<input className={control} value={group.title} onChange={e => groupChange(index, { ...group, title: e.target.value })} /></label>
      <Button disabled={index === 0} onClick={() => setDraft({ ...draft, groups: move(draft.groups, index, -1) })}>Move group up</Button>
      <Button disabled={index === draft.groups.length - 1} onClick={() => setDraft({ ...draft, groups: move(draft.groups, index, 1) })}>Move group down</Button>
      <Button onClick={() => setDraft({ ...draft, groups: draft.groups.filter(g => g.id !== group.id) })}>Remove group</Button>
      <label className="block">Add source to group {index + 1}<select className={control} value="" onChange={e => { const source = sources.find(s => s.id === e.target.value)!; groupChange(index, { ...group, cards: [...group.cards, { id: crypto.randomUUID(), artifact_id: source.id, artifact_version: source.version, caption: '', colors: [] }] }) }}><option value="" disabled>Choose saved artifact</option>{sources.map(s => <option key={s.id} value={s.id}>{s.title} · version {s.version}</option>)}</select></label>
      <div className="grid gap-3 sm:grid-cols-2">{group.cards.map((card, position) => {
        const status = selected?.source_status?.find(s => s.card_id === card.id)
        const update = (next: Card) => groupChange(index, { ...group, cards: group.cards.map(c => c.id === card.id ? next : c) })
        return <article key={card.id} className="min-w-0 space-y-2 rounded bg-surface-container p-3" aria-label={`Card ${index + 1}.${position + 1}`}>
          <p>{card.provenance?.title || sources.find(s => s.id === card.artifact_id)?.title || card.artifact_id} · version {card.artifact_version}</p>
          {status?.missing ? <p>Source missing</p> : status?.preview_url && <img alt={card.caption || 'Inspiration reference'} className="max-h-64 w-full object-contain" src={new URL(status.preview_url, new URL(apiRoot, location.origin)).href} />}
          <label className="block">Caption {index + 1}.{position + 1}<textarea className={control} value={card.caption} onChange={e => update({ ...card, caption: e.target.value })} /></label>
          <Colors key={`${card.id}:${selected?.revision || 0}`} label={`Colors ${index + 1}.${position + 1}`} colors={card.colors} onChange={colors => update({ ...card, colors })} />
          <div className="flex gap-1">{card.colors.filter(color => /^#[a-fA-F0-9]{6}$/.test(color)).map((color, n) => <span key={n} aria-label={color} className="h-5 w-5 rounded" style={{ backgroundColor: color }} />)}</div>
          <Button disabled={position === 0} onClick={() => groupChange(index, { ...group, cards: move(group.cards, position, -1) })}>Move card up</Button>
          <Button disabled={position === group.cards.length - 1} onClick={() => groupChange(index, { ...group, cards: move(group.cards, position, 1) })}>Move card down</Button>
          <Button onClick={() => groupChange(index, { ...group, cards: group.cards.filter(c => c.id !== card.id) })}>Remove card</Button>
        </article>
      })}</div>
    </section>)}
    <Button disabled={busy || !draft.title.trim()} onClick={() => void save()}>Save moodboard</Button>
    {selected && <Button disabled={busy} onClick={() => void exportBoard()}>Export moodboard</Button>}
    {exported && <textarea aria-label="Exported moodboard" readOnly className={control} rows={8} value={exported} />}
    {history.length > 0 && <section aria-label="Board revisions"><h3>Previous revisions</h3>{history.map(r => <p key={r.revision}>{r.title} · {r.revision} <Button disabled={busy || r.revision === selected?.revision} onClick={() => void save(r.revision)}>Restore board revision {r.revision}</Button></p>)}</section>}
  </main>
}
