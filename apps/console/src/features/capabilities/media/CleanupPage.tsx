import { useState } from 'react'
export type Transform = { op: string; [key: string]: string | number }
export const defaultTransform = (op: string): Transform => op === 'crop' ? { op, x: 0, y: 0, width: 256, height: 256 } : op === 'resize' ? { op, width: 512, height: 512 } : op === 'rotate' ? { op, degrees: 90 } : op === 'flip' ? { op, axis: 'horizontal' } : op === 'sharpen' ? { op, radius: 2, percent: 150, threshold: 3 } : op === 'solid_background' ? { op, color: '#ffffff', tolerance: 10 } : { op, factor: 1 }
export function TransformList({ operations, change }: { operations: Transform[]; change: (operations: Transform[]) => void }) {
  return <ol>{operations.map((operation, index) => <li className="border rounded p-2" key={index}><h2>{index+1}. {operation.op}</h2>
    {Object.entries(operation).filter(([key]) => key !== 'op').map(([key, value]) => <label key={key}>{key}<input type={typeof value === 'number' ? 'number' : key === 'color' ? 'color' : 'text'} step="any" value={value} onChange={event => change(operations.map((item, at) => at === index ? { ...item, [key]: typeof value === 'number' ? Number(event.target.value) : event.target.value } : item))} /></label>)}
    <button disabled={index === 0} onClick={() => { const reordered = [...operations]; [reordered[index-1], reordered[index]] = [reordered[index], reordered[index-1]]; change(reordered) }}>Move up</button><button onClick={() => change(operations.filter((_, at) => at !== index))}>Remove transform</button>
  </li>)}</ol>
}
export default function CleanupPage() {
  const [artifactId, setArtifactId] = useState(''), [version, setVersion] = useState(1), [operations, setOperations] = useState<Transform[]>([]), [kind, setKind] = useState('resize'), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const submit = async () => { setBusy(true); try { const response = await fetch('/api/capabilities/media/jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ operation: 'image_cleanup', request_id: crypto.randomUUID(), input: { source_artifact_id: artifactId, source_version: version, operations } }) }); const result = await response.json(); if (!response.ok) throw new Error(result.error || 'Cleanup request failed'); location.hash = '#/capabilities/media?view=jobs' } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  return <section className="p-6 space-y-4"><h1>Image cleanup</h1><a href="#/capabilities/media?view=library">Media library</a> · <a href="#/capabilities/media?view=jobs">Media jobs</a>
    <p>Transforms run in the listed order on an EXIF-oriented copy. The pinned original stays unchanged. Resize uses Lanczos interpolation; it does not invent detail. Solid background cleanup removes only matching color connected to image edges, preserving enclosed regions; it is not semantic segmentation.</p>
    {error && <p role="alert">{error}</p>}<label>Source image artifact<input value={artifactId} onChange={event => setArtifactId(event.target.value)} /></label><label>Source version<input type="number" min="1" value={version} onChange={event => setVersion(Number(event.target.value))} /></label>
    <label>Transform<select value={kind} onChange={event => setKind(event.target.value)}>{['crop', 'resize', 'rotate', 'flip', 'brightness', 'contrast', 'sharpen', 'solid_background'].map(name => <option key={name}>{name}</option>)}</select></label><button disabled={busy || operations.length >= 10} onClick={() => setOperations(old => [...old, defaultTransform(kind)])}>Add transform</button>
    <p>Up to 10 transforms. Dimensions stay within 4096 × 4096; solid background cleanup is limited to 4 megapixels. Rotations are counterclockwise right angles.</p>
    <TransformList operations={operations} change={setOperations} /><button disabled={busy || !artifactId || operations.length === 0} onClick={() => void submit()}>{busy ? 'Queuing…' : 'Queue cleanup'}</button>
  </section>
}
