import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListSkeleton } from '../../../shared/ui/ListScaffold'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
import { Blocks } from 'lucide-react'
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
  return <section aria-label="CLI harnesses" className="grid gap-l">
    <div className="flex flex-wrap items-start justify-between gap-m"><div><h2 data-type="title-m">CLI harnesses</h2><p data-type="body-s" className="mt-1 max-w-[48rem] text-on-surface-low">Install exact adapter versions on this machine. Disable dependent runtimes before replacing or removing an adapter.</p></div><Button variant="secondary" onClick={() => void refresh()} disabled={busy}>Refresh</Button></div>
    {error && <p role="alert" className="rounded-lg bg-danger/10 px-m py-s text-sm text-danger">{error}</p>}
    {!data ? <ListSkeleton rows={2} what="CLI harnesses" /> : data.harnesses.length === 0 ? <EmptyState icon={Blocks} title="No harnesses available" hint="Installed harness packages appear here." /> : <div className="grid gap-s lg:grid-cols-2">{data.harnesses.map(row => <article key={row.id} aria-label={row.name}><Surface className="grid h-full gap-m p-l">
      <div><h3 data-type="headline-s">{row.name}</h3><p data-type="caption" className="mt-1 text-on-surface-low">{row.installed ? `Installed ${row.installed.version}` : 'No managed adapter installed'}</p></div>
      {!!row.dependents.length && <p data-type="body-s">Dependencies: {row.dependents.join(', ')}</p>}
      {row.lifecycle ? <><Field label="Exact version"><TextInput ariaLabel={`${row.name} exact version`} mono value={versions[row.id] || ''} onChange={value => setVersions({ ...versions, [row.id]: value })} /></Field><div className="flex flex-wrap gap-s"><Button disabled={busy || !!row.dependents.length || !versions[row.id]} onClick={() => void change(row, row.installed ? 'update' : 'install')}>{row.installed ? 'Update' : 'Install'} {row.name}</Button>{row.installed && <Button variant="secondary" disabled={busy || !!row.dependents.length} onClick={() => void change(row, 'remove')}>Remove {row.name}</Button>}</div></> : <p data-type="body-s" className="text-on-surface-low">This runner has no managed adapter package.</p>}
    </Surface></article>)}</div>}
  </section>
}
