import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Claim { project_id: string; feature: string; owner: string | null; revision: number }
interface Snapshot { actor: string; projects: { id: string; name: string }[]; ownership: Claim[]; history: { actor: string; action: string; revision: number; at: string }[] }
export default function Ownership({ baseUrl = '' }: { baseUrl?: string }) {
  const [data, setData] = useState<Snapshot>(); const [project, setProject] = useState(''); const [feature, setFeature] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/ownership`
  const load = (projectId = project, featureKey = feature) => gatewayRequest(`${url}?project_id=${encodeURIComponent(projectId)}&feature=${encodeURIComponent(featureKey)}`).then(readJson<Snapshot>).then(setData).catch(reason => setError(String(reason)))
  useEffect(() => { void load() }, [baseUrl])
  const claim = data?.ownership.find(row => row.project_id === project && row.feature === feature)
  const change = async (action: string) => {
    setBusy(true); setError('')
    try { await readJson<Snapshot>(await gatewayRequest(url, 'POST', { project_id: project, feature, action, revision: claim?.revision || 0, request_id: crypto.randomUUID() })); await load() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Feature ownership" className="space-y-m">
    <h2>Persistent feature ownership</h2><p>Ownership remains until its current owner releases it. Claims use your authenticated identity.</p>
    {error && <p role="alert">{error}</p>}
    <label>Project<select aria-label="Ownership project" value={project} onChange={event => { setProject(event.target.value); void load(event.target.value, feature) }}><option value="">Select project</option>{data?.projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <label>Feature<input aria-label="Ownership feature" className="bg-surface-high p-s" value={feature} onChange={event => setFeature(event.target.value)} /></label>
    <Button disabled={busy} onClick={() => void load()}>Refresh ownership</Button>
    <p>Owner: {claim?.owner || 'Unclaimed'} · Revision: {claim?.revision || 0}</p>
    <Button disabled={busy || !project || !feature || !!claim?.owner} onClick={() => void change('claim')}>Claim feature</Button>
    <Button disabled={busy || !claim?.owner || claim.owner !== data?.actor} onClick={() => void change('release')}>Release feature</Button>
    {!!data?.history.length && <ol aria-label="Ownership history">{data.history.map(event => <li key={event.revision}>{event.action} · {event.actor} · revision {event.revision} · {event.at}</li>)}</ol>}
  </section>
}
