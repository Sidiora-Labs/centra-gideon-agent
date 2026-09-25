import { useEffect, useRef, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import WorldTravel from './WorldTravel'
type Source = { kind: string; id: string; title: string; status: string; url: string }
type Snapshot = { world: string; seq: number; state: { entities: Record<string, { pos?: number[]; comp?: { gideon_source?: Source } }> }; present: { id: string; agent: boolean }[] }
const kinds = ['apps', 'agents', 'work', 'goals', 'schedule', 'health', 'memory', 'operations', 'peers']
export default function Worlds({ baseUrl = '/api/capabilities/experience' }: { baseUrl?: string }) {
  const [name, setName] = useState(() => new URLSearchParams(location.hash.split('?')[1]).get('world') || 'home')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [unavailable, setUnavailable] = useState<string[]>([])
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState(''), [receipt, setReceipt] = useState('')
  const [busy, setBusy] = useState(false), [object, setObject] = useState('')
  const [position, setPosition] = useState(['0', '0', '0'])
  const priorName = useRef(name)
  const path = baseUrl + '/worlds/' + encodeURIComponent(name)
  const refresh = async () => { const value = await requestJson<Snapshot>(path); setSnapshot(value); const preview = await requestJson<{ sources: Source[]; unavailable: string[] }>(path + '/sources'); setSources(preview.sources); setUnavailable(preview.unavailable) }
  useEffect(() => { if (priorName.current === name) return; priorName.current = name; setSnapshot(null); setSources([]); setUnavailable([]) }, [name])
  useEffect(() => { const change = () => { const value = new URLSearchParams(location.hash.split('?')[1]).get('world'); if (value) setName(value) }; window.addEventListener('hashchange', change); return () => window.removeEventListener('hashchange', change) }, [])
  async function run(action: () => Promise<void>) { setBusy(true); setError(''); try { await action() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  async function mutate(action: string, body: Record<string, unknown>) {
    if (!snapshot) return
    const result = await requestJson<{ complete: boolean; operations: unknown[] }>(path + '/' + action, 'POST', { ...body, expected_seq: snapshot.seq, request_id: crypto.randomUUID().replaceAll('-', '') })
    setReceipt(result.complete ? `${result.operations.length} world changes recorded` : 'World operation partially applied or refused; inspect current objects before retrying')
    await refresh()
  }
  return <><WorldTravel baseUrl={baseUrl} world={name} open={snapshot !== null} /><section aria-label="World workspace" className="space-y-3 rounded border p-4">
    <h2>World workspace</h2><p>Choose source metadata explicitly. Projected objects reference the original records; moving an object does not edit its source.</p>
    {error && <p role="alert">{error}</p>}{receipt && <p role="status">{receipt}</p>}
    <label>World name<input value={name} pattern="[A-Za-z0-9_-]{1,64}" onChange={e => setName(e.target.value)} /></label>
    <Button disabled={busy || !/^[A-Za-z0-9_-]{1,64}$/.test(name)} onClick={() => void run(async () => { setSnapshot(await requestJson<Snapshot>(path + '/open', 'POST', {})); const query = new URLSearchParams(location.hash.split('?')[1]); query.set('world', name); location.hash = '/capabilities/experience?' + query; await refresh() })}>Join or create world</Button>
    <Button disabled={busy} onClick={() => void run(refresh)}>Refresh world</Button>
    {snapshot && <><p>World {snapshot.world} · revision {snapshot.seq}</p><p>Present: {snapshot.present.map(person => person.id).join(', ') || 'Nobody'}</p>
      <fieldset disabled={busy}><legend>Project selected source categories</legend>{kinds.map(kind => <label key={kind}><input type="checkbox" disabled={unavailable.includes(kind)} checked={selected.includes(kind)} onChange={e => setSelected(previous => e.target.checked ? [...previous, kind] : previous.filter(value => value !== kind))} />{kind}</label>)}</fieldset>
      {unavailable.length > 0 && <p>Unavailable sources: {unavailable.join(', ')}</p>}
      <Button disabled={busy || selected.length === 0} onClick={() => void run(() => mutate('project', { kinds: selected }))}>Project selected sources</Button>
      <ul>{sources.filter(source => selected.includes(source.kind)).map(source => <li key={source.kind + source.id}><a href={source.url}>{source.title}</a> · {source.status}</li>)}</ul>
      <label>Object identifier<input value={object} onChange={e => setObject(e.target.value)} /></label>
      {position.map((value, index) => <label key={index}>{['X', 'Y', 'Z'][index]}<input type="number" value={value} onChange={e => setPosition(old => old.map((item, at) => at === index ? e.target.value : item))} /></label>)}
      {['spawn', 'place', 'remove'].map(operation => <Button key={operation} disabled={busy || !object} onClick={() => void run(() => mutate('objects', { operation, id: object, ...(operation === 'remove' ? {} : { position: position.map(Number) }) }))}>{operation} object</Button>)}
      <ul aria-label="World objects">{Object.entries(snapshot.state.entities).map(([id, entity]) => <li key={id}><Button onClick={() => { setObject(id); setPosition((entity.pos || [0, 0, 0]).map(String)) }}>{entity.comp?.gideon_source?.title || id}</Button> · {(entity.pos || []).join(', ')} {entity.comp?.gideon_source && <span>{entity.comp.gideon_source.status} · <a href={entity.comp.gideon_source.url}>Open source</a></span>}</li>)}</ul>
      <iframe title="Selected persistent world" src={baseUrl + '/world-engine/host/?world=' + encodeURIComponent(snapshot.world)} className="h-[60vh] w-full" allow="fullscreen" />
    </>}
  </section></>
}
