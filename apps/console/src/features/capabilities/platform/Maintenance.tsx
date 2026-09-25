import { useEffect, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
interface Run { id: string; status: string; stage: number; child_id: string | null; error: string | null; history: { stage: string; status: string }[] }
interface View { audits: string[]; projects: { id: string; name: string }[]; runs: Run[] }
export default function Maintenance({ baseUrl = '' }: { baseUrl?: string }) {
  const [view, setView] = useState<View>(); const [project, setProject] = useState(''); const [verify, setVerify] = useState(''); const [guard, setGuard] = useState(''); const [error, setError] = useState(''); const [busy, setBusy] = useState(false)
  const url = `${baseUrl}/api/capabilities/platform/maintenance`
  const load = async () => { try { setView(await readJson<View>(await gatewayRequest(url))) } catch (reason) { setError(String(reason)) } }
  useEffect(() => { void load(); const timer = setInterval(() => void load(), 2000); return () => clearInterval(timer) }, [baseUrl])
  const mutate = async (id?: string, action?: string) => {
    setBusy(true); setError('')
    try { await readJson(await gatewayRequest(id ? `${url}/${id}` : url, 'POST', id ? { action } : { project_id: project, verify_command: verify, guard_command: guard })); await load() } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  return <section aria-label="Project maintenance" className="space-y-m"><h2>Project maintenance</h2><p>Run seven audits through the installed code-project workflow. Actual maintenance issues are drained between audits; model and command prerequisites must pass.</p>
    {error && <p role="alert">{error}</p>}
    <p>{view?.audits.join(' → ')}</p>
    <label>Project<select aria-label="Maintenance project" value={project} onChange={event => setProject(event.target.value)}><option value="">Select project</option>{view?.projects.map(row => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>
    <label>Verification command<input aria-label="Maintenance verification command" value={verify} onChange={event => setVerify(event.target.value)} /></label>
    <label>Guard command<input aria-label="Maintenance guard command" value={guard} onChange={event => setGuard(event.target.value)} /></label>
    <Button disabled={busy || !project || !verify.trim() || !guard.trim()} onClick={() => void mutate()}>Start maintenance</Button>
    {view?.runs.map(row => <article key={row.id} aria-label={`Maintenance ${row.id}`}><p role="status">{row.status} · stage {row.stage + 1} · {row.history.length} recorded child runs</p>{row.child_id && <p>Workflow run: {row.child_id}</p>}{row.error && <p>{row.error}</p>}{row.history.map((item, index) => <p key={index}>{item.stage}: {item.status}</p>)}<Button disabled={busy || row.status !== 'running'} onClick={() => void mutate(row.id, 'cancel')}>Cancel maintenance</Button><Button disabled={busy || !['failed', 'cancelled'].includes(row.status)} onClick={() => void mutate(row.id, 'resume')}>Resume maintenance</Button></article>)}
  </section>
}
