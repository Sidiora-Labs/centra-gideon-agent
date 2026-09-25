import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextArea, TextInput } from '../../../shared/ui/forms'

type Card = { id: string; front: string; back: string; source: string; tags: string[]; revision: number; archived: boolean; artifact: { slug: string; version: number; sha256: string }; schedule: { due_at: string; interval_days: number; repetitions: number; lapses: number; last_grade: string | null; rules_version: number }; practice: { grade: string; practiced_at: string } | null }
const base = '/api/capabilities/wellbeing/memory/cards'

function Editor({ card, saved }: { card: Card | null; saved: (id: string) => void }) {
  const [front, setFront] = useState(card?.front ?? ''), [back, setBack] = useState(card?.back ?? ''), [source, setSource] = useState(card?.source ?? ''), [tags, setTags] = useState(card?.tags.join(', ') ?? ''), [archived, setArchived] = useState(card?.archived ?? false)
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  const receipt = useRef({ fingerprint: '', id: '' })
  async function submit(event: FormEvent) {
    event.preventDefault(); if (busy) return
    const payload = { front, back, tags: tags.split(',').map(tag => tag.trim()).filter(Boolean), ...(card ? { revision: card.revision, archived } : { source }) }
    const fingerprint = JSON.stringify(payload)
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { const result = await requestJson<Card>(base + (card ? `/${card.id}` : ''), card ? 'PUT' : 'POST', { ...payload, request_id: receipt.current.id }); saved(result.id) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <form onSubmit={submit} className="space-y-m"><h2 data-type="title-m">{card ? 'Edit memory card' : 'New memory card'}</h2><Field label="Memory prompt"><TextArea value={front} onChange={setFront} /></Field><Field label="Memory answer"><TextArea value={back} onChange={setBack} /></Field><Field label="Memory source"><TextInput value={source} onChange={setSource} disabled={!!card} required /></Field><Field label="Tags (comma separated)"><TextInput value={tags} onChange={setTags} /></Field>{card && <label><input className="size-4 rounded border-outline-variant/40 text-primary focus:ring-primary" type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} />Archived memory card</label>}{error && <p role="alert">{error}</p>}<Button type="submit" loading={busy}>Save memory card</Button></form>
}

export default function MemoryPractice() {
  const { query, setQuery } = useHashRoute('capabilities')
  const identity = query.card, edit = query.mode === 'edit'
  const [cards, setCards] = useState<Card[]>([]), [selected, setSelected] = useState<Card | null>(null), [history, setHistory] = useState<Card[]>([])
  const [dueOnly, setDueOnly] = useState(false), [includeArchived, setIncludeArchived] = useState(false), [revealed, setRevealed] = useState(false)
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [error, setError] = useState(''), [generation, setGeneration] = useState(0)
  const receipt = useRef({ fingerprint: '', id: '' })
  useEffect(() => {
    let active = true; setLoading(true); setError(''); setSelected(null); setRevealed(false)
    Promise.all([requestJson<{ cards: Card[] }>(`${base}?${new URLSearchParams({ due_only: String(dueOnly), include_archived: String(includeArchived) })}`), identity ? requestJson<Card>(`${base}/${identity}`) : Promise.resolve(null), identity ? requestJson<{ history: Card[] }>(`${base}/${identity}/history`) : Promise.resolve({ history: [] })])
      .then(([list, row, revisions]) => { if (active) { setCards(list.cards); setSelected(row); setHistory(revisions.history) } })
      .catch(err => { if (active) setError(err instanceof Error ? err.message : String(err)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [identity, dueOnly, includeArchived, generation])
  async function grade(value: string) {
    if (!selected || !revealed || busy) return
    const payload = { revision: selected.revision, grade: value }
    const fingerprint = JSON.stringify([selected.id, payload])
    if (receipt.current.fingerprint !== fingerprint) receipt.current = { fingerprint, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try { await requestJson(`${base}/${selected.id}/practice`, 'POST', { ...payload, request_id: receipt.current.id }); setGeneration(n => n + 1) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  return <main style={{ maxWidth: 'var(--content-width)' }} className="mx-auto w-full space-y-2xl px-l py-2xl text-on-surface"><h2 data-type="title-m">Spaced memory practice</h2><p>Reveal the answer, then report your recall honestly. Version 1 scheduling uses elapsed UTC days and self-reported grades.</p><Button onClick={() => setQuery({ card: null, mode: null })}>New memory card</Button><div className="flex flex-wrap gap-l"><label><input className="size-4 rounded border-outline-variant/40 text-primary focus:ring-primary" type="checkbox" checked={dueOnly} onChange={e => setDueOnly(e.target.checked)} />Due cards only</label><label><input className="size-4 rounded border-outline-variant/40 text-primary focus:ring-primary" type="checkbox" checked={includeArchived} onChange={e => setIncludeArchived(e.target.checked)} />Include archived memory cards</label></div>{error && <p role="alert">{error} <Button onClick={() => setGeneration(n => n + 1)}>Reload cards</Button></p>}{loading && <p role="status">Loading memory cards…</p>}
    <section aria-label="Memory card list" className="space-y-l rounded-lg bg-surface-container p-l">{cards.map(card => <button key={card.id} className="block rounded-lg border border-outline-variant/20 bg-surface-container p-l my-s text-left" onClick={() => setQuery({ card: card.id, mode: null })}>{card.front}{card.archived ? ' (archived)' : ''}</button>)}{!loading && !cards.length && <p>No matching memory cards.</p>}</section>
    {!loading && (!identity || edit && selected) && <Editor key={selected ? `${selected.id}:${selected.revision}` : 'new'} card={selected} saved={id => { setQuery({ card: id, mode: null }); setGeneration(n => n + 1) }} />}
    {selected && !edit && <section className="space-y-m"><h2 data-type="title-m">Recall prompt</h2><p className="whitespace-pre-wrap break-words">{selected.front}</p><p>Source: {selected.source} · {selected.tags.join(', ')}</p><p>Due: {selected.schedule.due_at}</p><p>Interval: {selected.schedule.interval_days} days · Successful repetitions: {selected.schedule.repetitions} · Lapses: {selected.schedule.lapses}</p><p>Content artifact: {selected.artifact.slug} · version {selected.artifact.version}</p><Button variant="secondary" onClick={() => setQuery({ mode: 'edit' })}>Edit selected card</Button>{selected.archived ? <p>This card is archived.</p> : <><Button onClick={() => setRevealed(true)} disabled={revealed}>Reveal memory answer</Button>{revealed && <section aria-label="Revealed answer" className="space-y-l rounded-lg bg-surface-container p-l"><p className="whitespace-pre-wrap break-words">{selected.back}</p><div className="flex flex-wrap gap-s">{['again', 'hard', 'good', 'easy'].map(value => <Button key={value} disabled={busy} onClick={() => grade(value)}>{value}</Button>)}</div></section>}</>}</section>}
    {!!history.length && <section aria-label="Memory history" className="space-y-l rounded-lg bg-surface-container p-l"><h2 data-type="title-m">Memory history</h2>{history.map(row => <p key={row.revision}>Revision {row.revision}: {row.practice ? `Self-grade ${row.practice.grade}` : 'Content or archive change'} · due {row.schedule.due_at} · content {row.artifact.sha256.slice(0, 12)}</p>)}</section>}
  </main>
}
