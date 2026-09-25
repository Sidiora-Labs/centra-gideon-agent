import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Surface } from '../../../shared/ui/Surface'
import { Field, Select } from '../../../shared/ui/forms'
import { SearchField } from '../../../shared/ui/SearchField'

type Entry = { id: string; title: string; body: string }
type Universe = { id: string; title: string; revision: number; visual_identity: { colors: string[]; style_notes: string } }
type Node = { id: string; title: string; type: string | null; missing: boolean; external: boolean }
type Merge = { request_id: string; source_id: string; source_revision: number; target_id: string; target_revision: number; result_revision: number }
type Graph = { nodes: Node[]; edges: { source: string; target: string; kind: string }[]; merges: Merge[] }
type Preview = { target: Universe; source: Universe; canon_conflicts: { id: string; target: Entry; source: Entry }[]; identity_conflict: boolean; added_canon_ids: string[] }

export default function UniverseGraph({ id, revision, apiRoot = '/api/capabilities/creative/universes', onMerged }: { id: string; revision: number; apiRoot?: string; onMerged: () => void }) {
  const t = (value: string) => value
  const [graph, setGraph] = useState<Graph | null>(null)
  const [sources, setSources] = useState<Universe[]>([])
  const [query, setQuery] = useState('')
  const [source, setSource] = useState('')
  const [preview, setPreview] = useState<Preview | null>(null)
  const [choices, setChoices] = useState<Record<string, string>>({})
  const [identity, setIdentity] = useState('')
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : t('Unable to load universe graph'))
  useEffect(() => {
    let alive = true
    setGraph(null); setPreview(null); setError('')
    requestJson<Graph>(`${apiRoot}/${id}/graph`).then(value => { if (alive) setGraph(value) }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [apiRoot, id, revision, refresh])
  useEffect(() => {
    let alive = true
    requestJson<{ items: Universe[] }>(`${apiRoot}?q=${encodeURIComponent(query)}&limit=100`).then(value => { if (alive) setSources(value.items.filter(item => item.id !== id)) }).catch(e => { if (alive) fail(e) })
    return () => { alive = false }
  }, [apiRoot, id, query, refresh])
  async function prepare() {
    setBusy(true); setError(''); setPreview(null)
    try {
      const value = await requestJson<Preview>(`${apiRoot}/${id}/merge-preview`, 'POST', { source_id: source })
      setPreview(value); setChoices({}); setIdentity(value.identity_conflict ? '' : 'target'); setRequestId(crypto.randomUUID())
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  async function merge() {
    if (!preview) return
    setBusy(true); setError('')
    try {
      await requestJson(`${apiRoot}/${id}/merge`, 'POST', { request_id: requestId, source_id: preview.source.id,
        source_revision: preview.source.revision, target_revision: preview.target.revision, canon_choices: choices, identity_choice: identity })
      setPreview(null); setRefresh(value => value + 1); onMerged()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }
  const position = (index: number) => ({ x: 110 + index % 3 * 220, y: 35 + Math.floor(index / 3) * 70 })
  return <section className="space-y-l" aria-label={t('Universe relationships and merge')}><div><h2 data-type="title-m" className="text-on-surface">{t('Universe relationships and merge')}</h2><p data-type="body-m" className="text-on-surface-low">{t('Inspect the current canon network, then preview every merge conflict before applying a source universe.')}</p></div>
    {error && <p role="alert">{error}<Button onClick={() => setRefresh(value => value + 1)}>{t('Reload graph')}</Button></p>}
    {!graph && !error && <p>{t('Loading universe graph…')}</p>}
    <div className="grid min-w-0 gap-l xl:grid-cols-[minmax(0,1.25fr)_minmax(18rem,.75fr)]">{graph && <Surface className="min-w-0 space-y-m p-l"><h3 data-type="title-s" className="text-on-surface">{t('Relationship map')}</h3><p className="text-on-surface-low">{graph.nodes.length} {t('ingredients')} · {graph.edges.length} {t('relationships')}</p>
      {!!graph.nodes.length && <svg role="img" aria-label={t('Universe relationship graph')} viewBox={`0 0 660 ${Math.max(90, Math.ceil(graph.nodes.length / 3) * 70)}`} className="w-full max-h-[32rem]">
        {graph.edges.map((edge, index) => { const start = position(graph.nodes.findIndex(n => n.id === edge.source)); const end = position(graph.nodes.findIndex(n => n.id === edge.target)); return <line key={index} x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke="currentColor" opacity="0.5" /> })}
        {graph.nodes.map((node, index) => { const p = position(index); return <g key={node.id}><rect x={p.x - 95} y={p.y - 18} width="190" height="36" rx="6" fill="var(--color-surface)" stroke="currentColor" /><text x={p.x} y={p.y + 5} textAnchor="middle" fill="currentColor" fontSize="12">{node.title.slice(0, 24)}</text></g> })}
      </svg>}
      <ul>{graph.nodes.map(node => <li key={node.id} className="break-all">{node.title} — {node.missing ? t('Ingredient missing') : node.external ? t('Outside this universe') : t('Universe member')}</li>)}</ul>
      <ul>{graph.edges.map((edge, index) => <li key={index}>{graph.nodes.find(n => n.id === edge.source)?.title} → {edge.kind} → {graph.nodes.find(n => n.id === edge.target)?.title}</li>)}</ul>
      {!!graph.merges.length && <section aria-label={t('Universe merge history')}><h3 data-type="title-s">{t('Universe merge history')}</h3>{graph.merges.map(event => <p key={event.request_id} className="break-all">{t('Source')} {event.source_id} {t('revision')} {event.source_revision} → {t('universe revision')} {event.result_revision}</p>)}</section>}
    </Surface>}
    <Surface className="h-fit space-y-m p-l"><h3 data-type="title-m" className="text-on-surface">{t('Merge another universe')}</h3><p data-type="body-m" className="text-on-surface-low">{t('The preview lists additions and requires an explicit choice for every conflict. The source remains unchanged.')}</p><SearchField value={query} onChange={setQuery} placeholder={t('Search merge sources')} ariaLabel={t('Search merge sources')} surface="container" />
    <Field label={t('Source universe')}><Select value={source} onChange={value => { setSource(value); setPreview(null) }} options={[{ value: '', label: t('Choose universe') }, ...sources.map(item => ({ value: item.id, label: `${item.title} · ${t('revision')} ${item.revision}` }))]} /></Field>
    <Button disabled={busy || !source} onClick={() => void prepare()}>{t('Preview merge')}</Button>
    {preview && <section aria-label={t('Merge preview')} className="space-y-3 border-t border-outline-variant/30 pt-l"><h3 data-type="title-s">{t('Merge preview')}</h3><p>{preview.source.title} {t('revision')} {preview.source.revision} → {preview.target.title} {t('revision')} {preview.target.revision}</p>
      <p>{preview.added_canon_ids.length} {t('new canon entries. Ingredient links and pinned moodboards will be combined. The source universe stays unchanged.')}</p>
      {preview.canon_conflicts.map(conflict => <fieldset key={conflict.id} className="space-y-2 border p-2"><legend>{t('Canon conflict:')} {conflict.id}</legend><p>{t('Target:')} {conflict.target.title} — {conflict.target.body}</p><p>{t('Source:')} {conflict.source.title} — {conflict.source.body}</p>
        <Field label={`${t('Resolve canon')} ${conflict.id}`}><Select value={choices[conflict.id] || ''} onChange={value => setChoices({ ...choices, [conflict.id]: value })} options={[{ value: '', label: t('Choose value') }, { value: 'target', label: t('Keep target') }, { value: 'source', label: t('Use source') }]} /></Field>
      </fieldset>)}
      {preview.identity_conflict && <><p>{t('Target visual identity:')} {preview.target.visual_identity.colors.join(', ')} — {preview.target.visual_identity.style_notes}</p><p>{t('Source visual identity:')} {preview.source.visual_identity.colors.join(', ')} — {preview.source.visual_identity.style_notes}</p>
        <Field label={t('Resolve visual identity')}><Select value={identity} onChange={setIdentity} options={[{ value: '', label: t('Choose value') }, { value: 'target', label: t('Keep target') }, { value: 'source', label: t('Use source') }]} /></Field></>}
      <Button disabled={busy || !identity || preview.canon_conflicts.some(conflict => !choices[conflict.id])} onClick={() => void merge()}>{t('Apply merge')}</Button>
    </section>}</Surface></div>
  </section>
}
