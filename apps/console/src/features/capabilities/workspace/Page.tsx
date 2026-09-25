import Git from './Git'
import { useEffect, useState } from 'react'
import Processes from './Processes'
import Ports from './Ports'
import Projects from './Projects'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Snapshot = { id: string; project_id: string; workspace: string; branch: string | null; dirty: boolean; terminal_ids: string[]; task_ids: string[]; captured_at: string; revision: number }
type Comparison = { live: { branch: string | null; dirty: boolean }; branch_matches: boolean | null; surviving_terminal_ids: string[]; missing_terminal_ids: string[]; surviving_task_ids: string[]; missing_task_ids: string[] }
const base = '/api/capabilities/workspace'

export default function Page() {
  const [rows, setRows] = useState<Snapshot[]>([])
  const [project, setProject] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [terminals, setTerminals] = useState('')
  const [tasks, setTasks] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('snapshot') || '')
  const [retry, setRetry] = useState<{ body: string; id: string } | null>(null)
  useEffect(() => {
    let active = true
    requestJson<Snapshot[]>(base).then(r => { if (active) { setRows(r); setLoaded(true) } }).catch(e => { if (active) setError(String(e)) })
    const changed = () => { setSelected(new URLSearchParams(location.hash.split('?')[1] || '').get('snapshot') || ''); setComparison(null) }
    addEventListener('hashchange', changed)
    return () => { active = false; removeEventListener('hashchange', changed) }
  }, [])
  async function act(work: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  function choose(id: string) {
    location.hash = `/capabilities/workspace${id ? `?snapshot=${encodeURIComponent(id)}` : ''}`
    setSelected(id); setComparison(null)
  }
  async function save() {
    const ids = (text: string) => text.split(',').map(x => x.trim()).filter(Boolean)
    const payload = { project_id: project, workspace, terminal_ids: ids(terminals), task_ids: ids(tasks) }
    const body = JSON.stringify(payload)
    const id = retry?.body === body ? retry.id : crypto.randomUUID()
    setRetry({ body, id })
    const row = await requestJson<Snapshot>(base, 'POST', { ...payload, request_id: id })
    setRows(old => [row, ...old.filter(x => x.id !== row.id)]); setRetry(null); choose(row.id)
  }
  const row = rows.find(x => x.id === selected)
  return <section className="space-y-4 p-4 text-on-surface">
    <h1 className="text-xl font-semibold">Workspace contexts</h1>
    <p>Save your branch, terminals and tasks. Comparing a context never changes files or restarts sessions.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(save) }}>
      {([['Project ID', project, setProject], ['Workspace path', workspace, setWorkspace], ['Terminal IDs (comma separated)', terminals, setTerminals], ['Task IDs (comma separated)', tasks, setTasks]] as const).map(([label, value, update], index) => <label key={label} htmlFor={`context-${index}`} className="grid gap-1">{label}<input id={`context-${index}`} className="min-w-0 rounded border border-outline bg-surface p-2" value={value} onChange={e => update(e.target.value)} required={index < 2} /></label>)}
      <Button type="submit" loading={busy}>Save context</Button>
    </form>
    {!loaded && !error && <p role="status">Loading contexts…</p>}
    {loaded && rows.length === 0 && <p>No saved contexts.</p>}
    <ul className="space-y-2">{rows.map(item => <li key={item.id}><Button variant="secondary" onClick={() => choose(item.id)}>{item.project_id} · {item.branch || 'Detached'} · {item.captured_at}</Button></li>)}</ul>
    {rows.length >= 100 && <Button loading={busy} onClick={() => void act(async () => { const more = await requestJson<Snapshot[]>(`${base}?offset=${rows.length}`); setRows(old => [...old, ...more]); if (!more.length) setError('No more saved contexts.') })}>Load older contexts</Button>}
    {selected && !row && loaded && <p>Context not found in this page.</p>}
    {row && <article className="space-y-3 break-words">
      <h2>{row.project_id}</h2><p>{row.workspace}</p><p>Saved branch: {row.branch || 'Detached'} · {row.dirty ? 'Uncommitted changes' : 'Clean'}</p>
      <div className="flex flex-wrap gap-2"><Button loading={busy} onClick={() => void act(async () => { setComparison(await requestJson<Comparison>(`${base}/${row.id}/reconcile`, 'POST', {})) })}>Compare with live workspace</Button>
      <Button variant="danger" loading={busy} onClick={() => void act(async () => { await requestJson(`${base}/${row.id}?revision=${row.revision}`, 'DELETE'); setRows(old => old.filter(x => x.id !== row.id)); choose('') })}>Delete saved context</Button></div>
      {comparison && <div aria-live="polite"><p>Live branch: {comparison.live.branch || 'Detached'} · {comparison.branch_matches === null ? 'No saved branch' : comparison.branch_matches ? 'Branch matches' : 'Branch changed'} · {comparison.live.dirty ? 'Uncommitted changes' : 'Clean'}</p>
        {(['surviving_terminal_ids', 'missing_terminal_ids', 'surviving_task_ids', 'missing_task_ids'] as const).map(key => <p key={key}>{key.replaceAll('_', ' ')}: {comparison[key].join(', ') || 'None'}</p>)}</div>}
    </article>}
    <Processes />
    <Ports />
    <Projects /><Git />
  </section>
}
