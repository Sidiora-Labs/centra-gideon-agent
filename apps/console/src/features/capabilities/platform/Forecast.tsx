import { Select } from '../../../shared/ui/forms'
import { BUSY_REASON } from '../../../shared/ui/unavailable'
import { Table, THead, Th, Td } from '../../../shared/ui/Table'
import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface View { qualification: string; truncated: boolean; events: { trigger_id: string; name: string; at: number; edit_url: string; admission_now: { allowed: boolean; gate: string; reason: string } }[]; excluded: { trigger_id: string; reason: string }[]; dependencies: { maintenance_id: string; status: string; child_id: string | null; pending_issues: number }[] }
export default function Forecast({ baseUrl = '' }: { baseUrl?: string }) {
  const [view, setView] = useState<View>(); const [horizon, setHorizon] = useState('3600'); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const load = async () => { setBusy(true); setError(''); try { setView(await readJson<View>(await gatewayRequest(`${baseUrl}/api/capabilities/platform/forecast?horizon=${horizon}`))) } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  useEffect(() => { void load() }, [baseUrl, horizon])
  return <section aria-label="Schedule forecast" className="grid gap-l"><h2 data-type="title-m">Cross-schedule forecast</h2><label className="grid gap-xs text-sm">Horizon<Select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" ariaLabel="Forecast horizon" value={horizon} onChange={value => setHorizon(value)} options={[{ value: "3600", label: ["One hour"].join('') }, { value: "21600", label: ["Six hours"].join('') }, { value: "86400", label: ["One day"].join('') }]} /></label><Button disabled={busy} disabledReason={busy ? BUSY_REASON : undefined} onClick={() => void load()}>Refresh forecast</Button>{error && <p role="alert">{error}</p>}<p>{view?.qualification}</p>{view?.truncated && <p role="status">Forecast limit reached; some future firings are omitted.</p>}
    <Table wrapClassName="min-w-0 overflow-x-auto rounded-lg border border-outline-variant/25" caption="Upcoming trigger forecast" className="w-full min-w-[640px] text-sm"><THead><tr><Th>Time</Th><Th>Trigger</Th><Th>Current admission</Th></tr></THead><tbody>{view?.events.map((row, index) => <tr key={row.trigger_id + index}><Td>{new Date(row.at * 1000).toLocaleString()}</Td><Td><a href={row.edit_url}>{row.name}</a></Td><Td>{row.admission_now.allowed ? 'Currently eligible; future execution conditional' : `${row.admission_now.gate}: ${row.admission_now.reason}`}</Td></tr>)}</tbody></Table>
    {view?.excluded.map(row => <p key={row.trigger_id}>{row.trigger_id}: {row.reason}</p>)}{view?.dependencies.map(row => <p key={row.maintenance_id}>Maintenance {row.maintenance_id}: {row.status}; {row.pending_issues} pending issues; {row.child_id ? `child ${row.child_id}` : 'no current child'}; next time unknown.</p>)}
  </section>
}
