import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Reference { id: string; name: string; path: string; branch: string; reviewed: string | null; checked_at: string | null; stale: boolean; error: string | null; snapshot: { head: string; commits: { sha: string; subject: string }[]; limit: number } | null }
interface Snapshot { references: Reference[] }
export default function References({ baseUrl = '' }: { baseUrl?: string }) {
  const [data, setData] = useState<Snapshot>(); const [draft, setDraft] = useState({ name: '', path: '', branch: 'main' }); const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const url = `${baseUrl}/api/capabilities/platform/references`
  const load = () => gatewayRequest(url).then(readJson<Snapshot>).then(setData).catch(reason => setError(String(reason)))
  useEffect(() => { void load() }, [baseUrl])
  const write = async (reference?: Reference, action?: string) => {
    setBusy(true); setError('')
    try { setData(await readJson<Snapshot>(await gatewayRequest(reference ? `${url}/${reference.id}${action === 'remove' ? '' : `/${action}`}` : url, action === 'remove' ? 'DELETE' : 'POST', reference ? { head: reference.snapshot?.head } : draft))); if (!reference) setDraft({ name: '', path: '', branch: 'main' }) }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Reference repositories" className="space-y-m">
    <h2>Reference repositories</h2><p>Check fetches the tracked origin branch. Only Mark reviewed advances your cursor. Failed checks retain the last successful snapshot.</p>
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-s">{(['name', 'path', 'branch'] as const).map(field => <label key={field}>{field}<input aria-label={`Reference ${field}`} className="block bg-surface-high p-s" value={draft[field]} onChange={event => setDraft({ ...draft, [field]: event.target.value })} /></label>)}</div>
    <Button disabled={busy || !draft.name || !draft.path || !draft.branch} onClick={() => void write()}>Track reference</Button>
    <Button disabled={busy} onClick={() => void load()}>Refresh references</Button>
    {data && !data.references.length && <p>No reference repositories.</p>}
    {data?.references.map(reference => <article key={reference.id} aria-label={reference.name} className="space-y-s rounded bg-surface-high p-m">
      <h3>{reference.name}</h3><p>{reference.path} · {reference.branch}</p><p>Reviewed: {reference.reviewed || 'Never'}</p>
      {reference.checked_at && <p>Checked {reference.checked_at}{reference.stale ? ' · Stale snapshot' : ''}</p>}
      {reference.error && <p role="status">{reference.error}</p>}
      <Button disabled={busy} onClick={() => void write(reference, 'check')}>Check {reference.name}</Button>
      <Button disabled={busy || reference.stale || !reference.snapshot} onClick={() => void write(reference, 'review')}>Mark {reference.name} reviewed</Button>
      <Button disabled={busy} onClick={() => void write(reference, 'remove')}>Remove {reference.name}</Button>
      {reference.snapshot && <><p>Observed {reference.snapshot.head}. Showing at most {reference.snapshot.limit} unreviewed commits.</p><ul>{reference.snapshot.commits.map(commit => <li key={commit.sha}>{commit.sha.slice(0, 12)} {commit.subject}</li>)}</ul>{!reference.snapshot.commits.length && <p>No unreviewed commits.</p>}</>}
    </article>)}
  </section>
}
