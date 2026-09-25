import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
export interface CoreTile { ref: string; size: string; order: number }
interface View { id: string; name: string; preset: boolean; tiles: CoreTile[] }
interface State { revision: number; selected_view: string; views: View[]; core_refs: string[] }
export function useComposition(baseUrl = '') {
  const [state, setState] = useState<State>(); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/compositions`
  const reload = async () => { try { setState(await readJson<State>(await gatewayRequest(url))); setError('') } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void reload() }, [baseUrl])
  const write = async (id: string | null, body: object) => { setBusy(true); setError(''); try { setState(await readJson<State>(await gatewayRequest(id ? `${url}/${id}` : url, id ? 'PUT' : 'POST', id ? { revision: state?.revision, ...body } : body))) } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  const selected = state?.views.find(view => view.id === state.selected_view)
  return { state, selected, error, busy, write, reload }
}
export function CompositionEditor({ model }: { model: ReturnType<typeof useComposition> }) {
  const [name, setName] = useState(''); const { state, selected, error, busy, write } = model
  const tiles = selected?.tiles.filter(tile => tile.ref.startsWith('core:')).sort((a, b) => a.order - b.order) || []
  const save = (next: CoreTile[]) => void write(selected!.id, { tiles: next.map(({ ref, size }) => ({ ref, size })) })
  return <section aria-label="Dashboard composition" className="space-y-s"><h2>Dashboard composition</h2>{error && <p role="alert">{error}</p>}<label>View<select aria-label="Dashboard view" value={selected?.id || 'overview'} disabled={busy} onChange={event => void write(event.target.value, { select: true })}>{state?.views.map(view => <option key={view.id} value={view.id}>{view.name}{view.preset ? ' (preset)' : ''}</option>)}</select></label><label>New view<input aria-label="New dashboard view" value={name} onChange={event => setName(event.target.value)} /></label><Button disabled={busy || !name.trim()} onClick={() => void write(null, { name })}>Create dashboard view</Button><Button disabled={busy} onClick={() => void model.reload()}>Reload dashboard views</Button>
    {selected && !selected.preset && <><label>Add widget<select aria-label="Add core widget" value="" disabled={busy} onChange={event => { if (event.target.value) save([...tiles, { ref: event.target.value, size: 'm', order: tiles.length }]) }}><option value="">Choose widget</option>{state?.core_refs.filter(ref => !tiles.some(tile => tile.ref === ref)).map(ref => <option key={ref}>{ref}</option>)}</select></label>{tiles.map((tile, index) => <div key={tile.ref}><span>{tile.ref}</span><select aria-label={`Size ${tile.ref}`} value={tile.size} disabled={busy} onChange={event => save(tiles.map(row => row.ref === tile.ref ? { ...row, size: event.target.value } : row))}>{['s', 'm', 'l', 'full'].map(size => <option key={size}>{size}</option>)}</select><Button disabled={busy || index === 0} onClick={() => { const next = [...tiles]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; save(next) }}>Move up {tile.ref}</Button><Button disabled={busy} onClick={() => save(tiles.filter(row => row.ref !== tile.ref))}>Remove {tile.ref}</Button></div>)}</>}
  </section>
}
export default function Composition({ baseUrl = '' }: { baseUrl?: string }) { return <CompositionEditor model={useComposition(baseUrl)} /> }
