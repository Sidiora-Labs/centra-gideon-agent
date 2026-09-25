import { useEffect, useState } from 'react'

export type ReadinessRow = { capability: string; selection: string; status: string; available: boolean; inference_verified: boolean; management_available: boolean; reason: string; model: { name: string; downloaded: boolean; sizes?: string[]; supports_edit?: boolean; aspect_ratios?: string[]; max_duration_s?: number } | null }
export function ReadinessCards({ items }: { items: ReadinessRow[] }) {
  return <div className="space-y-3">{items.map(item => <article key={item.capability} className="border rounded p-3"><h2>{item.capability === 'image_gen' ? 'Image generation' : 'Video generation'}</h2>
    <p>{item.selection || 'No model selected'}</p><p role="status">{item.status}</p><p>{item.reason}</p><p>Inference has not been verified.</p>
    {item.model && <dl><dt>Model</dt><dd>{item.model.name}</dd><dt>Catalog reports present</dt><dd>{item.model.downloaded ? 'Yes' : 'No'}</dd>
      {item.model.sizes && <><dt>Sizes</dt><dd>{item.model.sizes.join(', ') || 'Provider default'}</dd><dt>Image editing</dt><dd>{item.model.supports_edit ? 'Supported' : 'Not advertised'}</dd></>}
      {item.model.aspect_ratios && <><dt>Aspect ratios</dt><dd>{item.model.aspect_ratios.join(', ') || 'Provider default'}</dd><dt>Maximum duration</dt><dd>{item.model.max_duration_s} seconds</dd></>}
    </dl>}<p>{item.management_available ? 'Model management is available in provider settings.' : 'Local model management is not registered for this provider.'}</p>
  </article>)}</div>
}
export default function Readiness() {
  const [data, setData] = useState<{ observed_at: string | null; items: ReadinessRow[] } | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('')
  const load = async (refresh = false) => { setBusy(true); try { const response = await fetch('/api/capabilities/media/readiness', refresh ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' } : undefined); const value = await response.json(); if (!response.ok) throw new Error(value.error || 'Readiness request failed'); setData(value); setError('') } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  useEffect(() => { void load() }, [])
  return <section className="p-6 space-y-4"><h1>Media readiness</h1><a href="#/capabilities/media">Sketches</a> · <a href="#/settings?tab=models">Model settings</a> · <a href="#/settings?tab=providers">Provider settings</a>
    <p>Availability and model catalog observations do not prove credentials, remote reachability, GPU capacity, or successful inference. Refresh does not generate media or install models.</p>
    <button disabled={busy} onClick={() => void load(true)}>{busy ? 'Checking…' : 'Refresh readiness'}</button>{error && <p role="alert">{error}</p>}
    {!data && <p>Loading last observation…</p>}{data && <><p>Last observed: {data.observed_at || 'Never'}. Saved observations may be stale.</p><ReadinessCards items={data.items} /></>}
  </section>
}
