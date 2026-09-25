import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Entry = { id: string; title: string; body: string }
type Universe = { id: string; title: string; revision: number; visual_identity: { colors: string[]; style_notes: string } }
type Node = { id: string; title: string; type: string | null; missing: boolean; external: boolean }
type Merge = { request_id: string; source_id: string; source_revision: number; target_id: string; target_revision: number; result_revision: number }
type Graph = { nodes: Node[]; edges: { source: string; target: string; kind: string }[]; merges: Merge[] }
type Preview = { target: Universe; source: Universe; canon_conflicts: { id: string; target: Entry; source: Entry }[]; identity_conflict: boolean; added_canon_ids: string[] }
const control = 'w-full rounded border border-outline bg-surface p-2 text-on-surface'

export default function UniverseGraph({ id, revision, apiRoot = '/api/capabilities/creative/universes', onMerged }: { id: string; revision: number; apiRoot?: string; onMerged: () => void }) {
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
  const fail = (e: unknown) => setError(e instanceof Error ? e.message : 'Unable to load universe graph')
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
  return <section className="space-y-3 rounded border border-outline p-3" aria-label="Universe relationships and merge"><h2>Universe relationships and merge</h2>
    {error && <p role="alert">{error}<Button onClick={() => setRefresh(value => value + 1)}>Reload graph</Button></p>}
    {!graph && !error && <p>Loading universe graph…</p>}
    {graph && <><p>{graph.nodes.length} ingredients · {graph.edges.length} relationships</p>
      {!!graph.nodes.length && <svg role="img" aria-label="Universe relationship graph" viewBox={`0 0 660 ${Math.max(90, Math.ceil(graph.nodes.length / 3) * 70)}`} className="w-full max-h-[32rem]">
        {graph.edges.map((edge, index) => { const start = position(graph.nodes.findIndex(n => n.id === edge.source)); const end = position(graph.nodes.findIndex(n => n.id === edge.target)); return <line key={index} x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke="currentColor" opacity="0.5" /> })}
        {graph.nodes.map((node, index) => { const p = position(index); return <g key={node.id}><rect x={p.x - 95} y={p.y - 18} width="190" height="36" rx="6" fill="var(--color-surface)" stroke="currentColor" /><text x={p.x} y={p.y + 5} textAnchor="middle" fill="currentColor" fontSize="12">{node.title.slice(0, 24)}</text></g> })}
      </svg>}
      <ul>{graph.nodes.map(node => <li key={node.id} className="break-all">{node.title} — {node.missing ? 'Ingredient missing' : node.external ? 'Outside this universe' : 'Universe member'}</li>)}</ul>
      <ul>{graph.edges.map((edge, index) => <li key={index}>{graph.nodes.find(n => n.id === edge.source)?.title} → {edge.kind} → {graph.nodes.find(n => n.id === edge.target)?.title}</li>)}</ul>
      {!!graph.merges.length && <section aria-label="Universe merge history"><h3>Universe merge history</h3>{graph.merges.map(event => <p key={event.request_id} className="break-all">Source {event.source_id} revision {event.source_revision} → universe revision {event.result_revision}</p>)}</section>}
    </>}
    <label className="block">Search merge sources<input className={control} value={query} onChange={e => setQuery(e.target.value)} /></label>
    <label className="block">Source universe<select className={control} value={source} onChange={e => { setSource(e.target.value); setPreview(null) }}><option value="">Choose universe</option>{sources.map(item => <option key={item.id} value={item.id}>{item.title} · revision {item.revision}</option>)}</select></label>
    <Button disabled={busy || !source} onClick={() => void prepare()}>Preview merge</Button>
    {preview && <section aria-label="Merge preview" className="space-y-3"><h3>Merge preview</h3><p>{preview.source.title} revision {preview.source.revision} → {preview.target.title} revision {preview.target.revision}</p>
      <p>{preview.added_canon_ids.length} new canon entries. Ingredient links and pinned moodboards will be combined. The source universe stays unchanged.</p>
      {preview.canon_conflicts.map(conflict => <fieldset key={conflict.id} className="space-y-2 border p-2"><legend>Canon conflict: {conflict.id}</legend><p>Target: {conflict.target.title} — {conflict.target.body}</p><p>Source: {conflict.source.title} — {conflict.source.body}</p>
        <label>Resolve canon {conflict.id}<select className={control} value={choices[conflict.id] || ''} onChange={e => setChoices({ ...choices, [conflict.id]: e.target.value })}><option value="">Choose value</option><option value="target">Keep target</option><option value="source">Use source</option></select></label>
      </fieldset>)}
      {preview.identity_conflict && <><p>Target visual identity: {preview.target.visual_identity.colors.join(', ')} — {preview.target.visual_identity.style_notes}</p><p>Source visual identity: {preview.source.visual_identity.colors.join(', ')} — {preview.source.visual_identity.style_notes}</p>
        <label className="block">Resolve visual identity<select className={control} value={identity} onChange={e => setIdentity(e.target.value)}><option value="">Choose value</option><option value="target">Keep target</option><option value="source">Use source</option></select></label></>}
      <Button disabled={busy || !identity || preview.canon_conflicts.some(conflict => !choices[conflict.id])} onClick={() => void merge()}>Apply merge</Button>
    </section>}
  </section>
}
