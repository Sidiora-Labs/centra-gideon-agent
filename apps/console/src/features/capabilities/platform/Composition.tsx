import { Select, TextInput } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
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
  return <section aria-label="Dashboard composition" className="grid gap-m"><h2 data-type="title-m">Dashboard composition</h2>{error && <p role="alert">{error}</p>}<label data-type="label-s" className="grid gap-xs">View<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Dashboard view" value={selected?.id || 'overview'} readOnly={busy} readOnlyReason={BUSY_REASON} onChange={value => void write(value, { select: true })} options={[...(state?.views.map(view => ({ value: view.id, label: [view.name, view.preset ? ' (preset)' : ''].join('') })) ?? [])]} /></label><label data-type="label-s" className="grid gap-xs">New view<TextInput className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="New dashboard view" value={name} onChange={value => setName(value)} /></label><Button disabled={busy || !name.trim()} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void write(null, { name })}>Create dashboard view</Button><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void model.reload()}>Reload dashboard views</Button>
    {selected && !selected.preset && <><label data-type="label-s" className="grid gap-xs">Add widget<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Add core widget" value="" readOnly={busy} readOnlyReason={BUSY_REASON} onChange={value => { if (value) save([...tiles, { ref: value, size: 'm', order: tiles.length }]) }} options={[{ value: "", label: ["Choose widget"].join('') }, ...(state?.core_refs.filter(ref => !tiles.some(tile => tile.ref === ref)).map(ref => ({ value: [ref].join(''), label: [ref].join('') })) ?? [])]} /></label>{tiles.map((tile, index) => <div key={tile.ref}><span>{tile.ref}</span><Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel={`Size ${tile.ref}`} value={tile.size} readOnly={busy} readOnlyReason={BUSY_REASON} onChange={value => save(tiles.map(row => row.ref === tile.ref ? { ...row, size: value } : row))} options={[...(['s', 'm', 'l', 'full'].map(size => ({ value: [size].join(''), label: [size].join('') })) ?? [])]} /><Button disabled={busy || index === 0} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => { const next = [...tiles]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; save(next) }}>Move up {tile.ref}</Button><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => save(tiles.filter(row => row.ref !== tile.ref))}>Remove {tile.ref}</Button></div>)}</>}
  </section>
}
export default function Composition({ baseUrl = '' }: { baseUrl?: string }) { return <CompositionEditor model={useComposition(baseUrl)} /> }
