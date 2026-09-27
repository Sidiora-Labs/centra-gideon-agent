import NativeMediaPage from './NativeMediaPage'
import { useEffect, useState } from 'react'
type SourceImage = { id: string; name: string; version: number }
export type Transform = { op: string; [key: string]: string | number }
export const defaultTransform = (op: string): Transform => op === 'crop' ? { op, x: 0, y: 0, width: 256, height: 256 } : op === 'resize' ? { op, width: 512, height: 512 } : op === 'rotate' ? { op, degrees: 90 } : op === 'flip' ? { op, axis: 'horizontal' } : op === 'sharpen' ? { op, radius: 2, percent: 150, threshold: 3 } : op === 'solid_background' ? { op, color: '#ffffff', tolerance: 10 } : { op, factor: 1 }
export function TransformList({ operations, change }: { operations: Transform[]; change: (operations: Transform[]) => void }) {
  return <ol>{operations.map((operation, index) => <li className="rounded-lg bg-surface-container p-m" key={index}><h2>{index+1}. {operation.op}</h2>
    {Object.entries(operation).filter(([key]) => key !== 'op').map(([key, value]) => <label key={key}>{key}<input type={typeof value === 'number' ? 'number' : key === 'color' ? 'color' : 'text'} step="any" value={value} onChange={event => change(operations.map((item, at) => at === index ? { ...item, [key]: typeof value === 'number' ? Number(event.target.value) : event.target.value } : item))} /></label>)}
    <button disabled={index === 0} onClick={() => { const reordered = [...operations]; [reordered[index-1], reordered[index]] = [reordered[index], reordered[index-1]]; change(reordered) }}>Move up</button><button onClick={() => change(operations.filter((_, at) => at !== index))}>Remove transform</button>
  </li>)}</ol>
}
export default function CleanupPage({ onJob }: { onJob?: (id: string) => void } = {}) {
  const [images, setImages] = useState<SourceImage[]>([]), [source, setSource] = useState<SourceImage | null>(null)
  const [query, setQuery] = useState(''), [total, setTotal] = useState(0), [loading, setLoading] = useState(true), [refresh, setRefresh] = useState(0)
  const [operations, setOperations] = useState<Transform[]>([]), [kind, setKind] = useState('resize'), [busy, setBusy] = useState(false), [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    const timer = setTimeout(() => {
      setLoading(true)
      void fetch('/api/capabilities/media/library?' + new URLSearchParams({ kind: 'image', q: query.trim(), limit: '100' }), { signal: controller.signal })
        .then(async response => { const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Image library could not be loaded'); return value as { items: SourceImage[]; total: number } })
        .then(value => { setImages(value.items); setTotal(value.total); setError('') })
        .catch(reason => { if (!controller.signal.aborted) setError(String((reason as Error).message)) })
        .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    }, query ? 200 : 0)
    return () => { clearTimeout(timer); controller.abort() }
  }, [query, refresh])
  const submit = async () => { if (!source) return; setBusy(true); setError(''); try { const response = await fetch('/api/capabilities/media/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation: 'image_cleanup', request_id: crypto.randomUUID(), input: { source_artifact_id: source.id, source_version: source.version, operations } }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Cleanup request failed'); if (onJob) onJob(result.id); else location.hash = '#/capabilities/media?view=jobs' } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  return <NativeMediaPage title="Image cleanup" actions={<><a href="#/capabilities/media?view=library">Library</a><a href="#/capabilities/media?view=jobs">Jobs</a></>}>
    <p>Transforms run in the listed order on an EXIF-oriented copy. The pinned original stays unchanged. Resize uses Lanczos interpolation; it does not invent detail. Solid background cleanup removes only matching color connected to image edges, preserving enclosed regions; it is not semantic segmentation.</p>
    {error && <p role="alert">{error}</p>}
    <label>Find source image<input type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
    <button type="button" disabled={loading} onClick={() => setRefresh(value => value + 1)}>Refresh images</button>
    <label>Source image<select aria-label="Source image" value={source ? `${source.id}:${source.version}` : ''} onChange={event => setSource(images.find(image => `${image.id}:${image.version}` === event.target.value) || null)}>
      <option value="">Choose an image</option>{source && !images.some(image => image.id === source.id && image.version === source.version) && <option value={`${source.id}:${source.version}`}>{source.name} · version {source.version}</option>}{images.map(image => <option key={image.id} value={`${image.id}:${image.version}`}>{image.name} · version {image.version}</option>)}
    </select></label>
    {loading ? <p role="status">Loading images…</p> : <p role="status">{total} matching images{total > images.length ? ' · Search to find more' : ''}</p>}
    {source && <p>Pinned source: {source.name} · version {source.version}</p>}
    <label>Transform<select value={kind} onChange={event => setKind(event.target.value)}>{['crop', 'resize', 'rotate', 'flip', 'brightness', 'contrast', 'sharpen', 'solid_background'].map(name => <option key={name}>{name}</option>)}</select></label><button disabled={busy || operations.length >= 10} onClick={() => setOperations(old => [...old, defaultTransform(kind)])}>Add transform</button>
    <p>Up to 10 transforms. Dimensions stay within 4096 × 4096; solid background cleanup is limited to 4 megapixels. Rotations are counterclockwise right angles.</p>
    <TransformList operations={operations} change={setOperations} /><button disabled={busy || !source || operations.length === 0} onClick={() => void submit()}>{busy ? 'Queuing…' : 'Queue cleanup'}</button>
  </NativeMediaPage>
}
