import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Document = { id: string; title: string; text: string; enabled: boolean; private: boolean; weight: number; priority: number }
type Persona = { id: string; name: string; instructions: string; trait_adjustments: Record<string, string | number> }
type Snapshot = { revision: number; documents: Document[]; enabled: boolean; traits: Record<string, string | number>; personas: Persona[]; active_persona_id: string | null }
const blank = { title: '', text: '', enabled: true, private: false, weight: 5, priority: 50 }
export default function TwinPage({ endpoint = '/api/capabilities/identity/twin' }: { endpoint?: string }) {
  const [state, setState] = useState<Snapshot | null>(null)
  const [draft, setDraft] = useState<Omit<Document, 'id'> & { id?: string }>(blank)
  const [traits, setTraits] = useState('{}')
  const [personas, setPersonas] = useState('[]')
  const [active, setActive] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState('')
  const [budget, setBudget] = useState(1000)
  const call = async <T,>(path: string, method = 'GET', body?: unknown): Promise<T> => readJson<T>(await fetch(endpoint + path,
    { method, headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }))
  const adopt = (next: Snapshot) => { setState(next); setTraits(JSON.stringify(next.traits, null, 2)); setPersonas(JSON.stringify(next.personas, null, 2)); setActive(next.active_persona_id || ''); setEnabled(next.enabled) }
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  const load = () => perform(async () => { adopt(await call<Snapshot>('')) })
  useEffect(() => { void load() }, [endpoint])
  const select = (doc: Document) => { setDraft(doc); window.location.hash = '#/capabilities/identity/twin?document=' + doc.id }
  useEffect(() => {
    const id = new URLSearchParams(window.location.hash.split('?')[1] || '').get('document')
    const doc = state?.documents.find(value => value.id === id)
    if (doc) setDraft(doc)
  }, [state])
  return <section className="p-4 space-y-4 max-w-5xl mx-auto text-on-surface">
    <h1 className="text-2xl">Your identity context</h1><p>Describe yourself in your own words. Private sources stay out of automatic model context.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!state && <p role="status">Loading identity…</p>}
    <Button onClick={() => void load()} disabled={busy}>Reload identity</Button>
    <div className="grid md:grid-cols-2 gap-4">
      <section className="space-y-3 min-w-0"><h2 className="text-xl">Source documents</h2>
        {state?.documents.length === 0 && <p>No sources yet.</p>}
        {state?.documents.map(doc => <div key={doc.id}><button className="underline" onClick={() => select(doc)}>{doc.title}</button> {doc.private ? '(private)' : ''}</div>)}
        <Button variant="secondary" onClick={() => { setDraft(blank); window.location.hash = '#/capabilities/identity/twin' }}>New source</Button>
        <form className="space-y-2" onSubmit={e => { e.preventDefault(); void perform(async () => { const next = await call<Snapshot>('/documents', 'POST', { ...draft, expected_revision: state!.revision }); adopt(next); select(next.documents.find(d => d.id === draft.id) || next.documents[next.documents.length - 1]) }) }}>
          <label className="block" htmlFor="twin-title">Title</label><input className="w-full p-2 bg-surface-high" id="twin-title" required maxLength={200} value={draft.title} onChange={e => setDraft({ ...draft, title: e.target.value })} />
          <label className="block" htmlFor="twin-text">Source text</label><textarea className="w-full p-2 bg-surface-high" id="twin-text" required rows={6} maxLength={100000} value={draft.text} onChange={e => setDraft({ ...draft, text: e.target.value })} />
          <label className="block"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /> Include source</label>
          <label className="block"><input type="checkbox" checked={draft.private} onChange={e => setDraft({ ...draft, private: e.target.checked })} /> Private source</label>
          <label htmlFor="twin-weight">Weight</label><input id="twin-weight" type="number" min={1} max={10} value={draft.weight} onChange={e => setDraft({ ...draft, weight: Number(e.target.value) })} />
          <label htmlFor="twin-priority">Priority</label><input id="twin-priority" type="number" min={0} max={1000} value={draft.priority} onChange={e => setDraft({ ...draft, priority: Number(e.target.value) })} />
          <div className="flex flex-wrap gap-2"><Button type="submit" disabled={busy || !state}>Save source</Button>
            {draft.id && <><Button variant="danger" disabled={busy} onClick={() => void perform(async () => { adopt(await call<Snapshot>('/documents/' + draft.id + '?expected_revision=' + state!.revision, 'DELETE')); setDraft(blank); window.location.hash = '#/capabilities/identity/twin' })}>Delete source</Button>
              <Button variant="secondary" disabled={busy || draft.private || !draft.enabled} onClick={() => void perform(async () => { const result = await call<{ text: string }>('/enrich', 'POST', { document_id: draft.id }); setPreview(result.text) })}>Suggest questions</Button></>}</div>
        </form>
      </section>
      <section className="space-y-3 min-w-0"><h2 className="text-xl">Traits and persona overlays</h2>
        <label className="block"><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} /> Use identity in private conversations</label>
        <label className="block" htmlFor="twin-traits">Traits (JSON object)</label><textarea className="w-full bg-surface-high p-2" id="twin-traits" rows={4} value={traits} onChange={e => setTraits(e.target.value)} />
        <label className="block" htmlFor="twin-personas">Persona overlays (JSON list)</label><textarea className="w-full bg-surface-high p-2" id="twin-personas" rows={5} value={personas} onChange={e => setPersonas(e.target.value)} />
        <p>Each overlay has id, name, instructions and trait_adjustments. These describe your communication preferences.</p>
        <label className="block" htmlFor="twin-active">Active overlay ID</label><input className="w-full bg-surface-high p-2" id="twin-active" value={active} onChange={e => setActive(e.target.value)} />
        <Button disabled={busy || !state} onClick={() => void perform(async () => { adopt(await call<Snapshot>('', 'PUT', { expected_revision: state!.revision, enabled, traits: JSON.parse(traits), personas: JSON.parse(personas), active_persona_id: active || null })) })}>Save identity settings</Button>
        <label className="block" htmlFor="twin-budget">Context token budget (conservative)</label><input id="twin-budget" type="number" min={1} max={10000} value={budget} onChange={e => setBudget(Number(e.target.value))} />
        <Button variant="secondary" disabled={busy} onClick={() => void perform(async () => { const result = await call<{ text: string; omitted_ids: string[] }>('/context?budget=' + budget); setPreview(result.text + (result.omitted_ids.length ? '\nSources omitted for budget: ' + result.omitted_ids.join(', ') : '')) })}>Preview shared context</Button>
      </section>
    </div>
    {preview && <section aria-label="Identity preview"><h2>Preview</h2><pre className="whitespace-pre-wrap break-words">{preview}</pre></section>}
  </section>
}
