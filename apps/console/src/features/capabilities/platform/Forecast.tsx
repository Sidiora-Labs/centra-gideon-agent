import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface View { qualification: string; truncated: boolean; events: { trigger_id: string; name: string; at: number; edit_url: string; admission_now: { allowed: boolean; gate: string; reason: string } }[]; excluded: { trigger_id: string; reason: string }[]; dependencies: { maintenance_id: string; status: string; child_id: string | null; pending_issues: number }[] }
export default function Forecast({ baseUrl = '' }: { baseUrl?: string }) {
  const [view, setView] = useState<View>(); const [horizon, setHorizon] = useState('3600'); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const load = async () => { setBusy(true); setError(''); try { setView(await readJson<View>(await gatewayRequest(`${baseUrl}/api/capabilities/platform/forecast?horizon=${horizon}`))) } catch (reason) { setError(String(reason)) } finally { setBusy(false) } }
  useEffect(() => { void load() }, [baseUrl, horizon])
  return <section aria-label="Schedule forecast" className="space-y-m"><h2>Cross-schedule forecast</h2><label>Horizon<select aria-label="Forecast horizon" value={horizon} onChange={event => setHorizon(event.target.value)}><option value="3600">One hour</option><option value="21600">Six hours</option><option value="86400">One day</option></select></label><Button disabled={busy} onClick={() => void load()}>Refresh forecast</Button>{error && <p role="alert">{error}</p>}<p>{view?.qualification}</p>{view?.truncated && <p role="status">Forecast limit reached; some future firings are omitted.</p>}
    <table><thead><tr><th>Time</th><th>Trigger</th><th>Current admission</th></tr></thead><tbody>{view?.events.map((row, index) => <tr key={row.trigger_id + index}><td>{new Date(row.at * 1000).toLocaleString()}</td><td><a href={row.edit_url}>{row.name}</a></td><td>{row.admission_now.allowed ? 'Currently eligible; future execution conditional' : `${row.admission_now.gate}: ${row.admission_now.reason}`}</td></tr>)}</tbody></table>
    {view?.excluded.map(row => <p key={row.trigger_id}>{row.trigger_id}: {row.reason}</p>)}{view?.dependencies.map(row => <p key={row.maintenance_id}>Maintenance {row.maintenance_id}: {row.status}; {row.pending_issues} pending issues; {row.child_id ? `child ${row.child_id}` : 'no current child'}; next time unknown.</p>)}
  </section>
}
