import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, Select, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
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
  return <section aria-label="Feature ownership" className="grid gap-l">
    <div><h2 data-type="title-m">Feature ownership</h2><p data-type="body-s" className="mt-1 max-w-[48rem] text-on-surface-low">Keep one accountable owner attached to a project feature until that owner releases it.</p></div>
    {error && <p role="alert" className="rounded-lg bg-danger/10 px-m py-s text-sm text-danger">{error}</p>}
    <Surface className="grid gap-l p-l"><div className="grid gap-m sm:grid-cols-2"><Field label="Project"><Select ariaLabel="Ownership project" value={project} onChange={value => { setProject(value); void load(value, feature) }} options={[{ value: '', label: 'Select project' }, ...(data?.projects || []).map(item => ({ value: item.id, label: item.name }))]} /></Field><Field label="Feature"><TextInput ariaLabel="Ownership feature" value={feature} onChange={setFeature} /></Field></div><div className="flex flex-wrap gap-s"><Button disabled={busy} variant="secondary" onClick={() => void load()}>Refresh ownership</Button><Button disabled={busy || !project || !feature || !!claim?.owner} onClick={() => void change('claim')}>Claim feature</Button><Button disabled={busy || !claim?.owner || claim.owner !== data?.actor} variant="secondary" onClick={() => void change('release')}>Release feature</Button></div><p data-type="body-s">Owner: <strong>{claim?.owner || 'Unclaimed'}</strong> · Revision: {claim?.revision || 0}</p></Surface>
    {!!data?.history.length && <Surface className="p-l"><h3 data-type="headline-s">Ownership history</h3><ol aria-label="Ownership history" className="mt-m grid gap-s">{data.history.map(event => <li key={event.revision} className="rounded-lg bg-surface px-m py-s text-sm">{event.action} · {event.actor} · revision {event.revision} · {event.at}</li>)}</ol></Surface>}
  </section>
}
