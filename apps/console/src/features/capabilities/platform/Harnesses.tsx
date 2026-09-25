import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Row { id: string; name: string; package: string | null; installed: { version: string; integrity: string } | null; dependents: string[]; lifecycle: boolean }
interface Inventory { harnesses: Row[] }
export default function Harnesses({ baseUrl = '' }: { baseUrl?: string }) {
  const [data, setData] = useState<Inventory>(); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const [versions, setVersions] = useState<Record<string, string>>({})
  const url = `${baseUrl}/api/capabilities/platform/harnesses`
  const refresh = () => gatewayRequest(url).then(readJson<Inventory>).then(setData).catch(reason => setError(String(reason)))
  useEffect(() => { void refresh() }, [baseUrl])
  const change = async (row: Row, action: string) => {
    setBusy(true); setError('')
    try { setData(await readJson<Inventory>(await gatewayRequest(`${url}/${encodeURIComponent(row.id)}`, 'POST', { action, version: versions[row.id], expected_version: row.installed?.version }))) }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="CLI harnesses" className="space-y-m">
    <h2>CLI harness adapters</h2><p>Exact versions install into Gideon’s managed adapter directory. Authentication and latest versions are unchecked. Disable dependent runtimes before changing their adapter.</p>
    <Button onClick={() => void refresh()} disabled={busy}>Refresh harnesses</Button>
    {error && <p role="alert">{error}</p>}
    {data?.harnesses.map(row => <article key={row.id} aria-label={row.name} className="space-y-s rounded bg-surface-high p-m">
      <h3>{row.name}</h3><p>{row.installed ? `Installed ${row.installed.version}` : 'No managed adapter installed'}</p>
      {!!row.dependents.length && <p>Dependencies: {row.dependents.join(', ')}</p>}
      {row.lifecycle ? <><label>Exact version <input aria-label={`${row.name} exact version`} className="bg-surface p-s" value={versions[row.id] || ''} onChange={event => setVersions({ ...versions, [row.id]: event.target.value })} /></label>
        <Button disabled={busy || !!row.dependents.length || !versions[row.id]} onClick={() => void change(row, row.installed ? 'update' : 'install')}>{row.installed ? 'Update' : 'Install'} {row.name}</Button>
        {row.installed && <Button disabled={busy || !!row.dependents.length} onClick={() => void change(row, 'remove')}>Remove {row.name}</Button>}
      </> : <p>This runner has no managed adapter package.</p>}
    </article>)}
  </section>
}
