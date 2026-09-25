import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
type State = { project_id: string; head: string; branch: string; dirty: boolean; status: string; submodules: { path: string; expected_head: string; head: string | null; initialized: boolean; dirty: boolean }[] }
type Receipt = { id: string; operation: string; target: string; status: string }
const base = '/api/capabilities/workspace/git'
export default function Git() {
  const [projects, setProjects] = useState<{ project: { id: string; name: string } }[]>([])
  const [project, setProject] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('git_project') || '')
  const [state, setState] = useState<State | null>(null)
  const [history, setHistory] = useState<Receipt[]>([])
  const [branch, setBranch] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [retry, setRetry] = useState<{ body: string; id: string } | null>(null)
  function choose(id: string) {
    setProject(id); setState(null); setHistory([]); setRetry(null)
    const query = new URLSearchParams(location.hash.split('?')[1] || '')
    query.set('git_project', id); location.hash = `/capabilities/workspace?${query}`
  }
  async function refresh(id: string) {
    const [next, receipts] = await Promise.all([requestJson<State>(`${base}/${encodeURIComponent(id)}`), requestJson<Receipt[]>(`${base}/${encodeURIComponent(id)}/operations`)])
    setState(next); setHistory(receipts)
  }
  async function act(work: () => Promise<void>) {
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  useEffect(() => { void requestJson<typeof projects>('/api/capabilities/workspace/projects').then(setProjects).catch(e => setError(String(e))) }, [])
  async function mutate(operation: string, target: string) {
    if (!state) return
    const input = { project_id: project, operation, target, expected_head: state.head }
    const body = JSON.stringify(input), request_id = retry?.body === body ? retry.id : crypto.randomUUID()
    setRetry({ body, id: request_id })
    await requestJson<Receipt>(`${base}/operations`, 'POST', { ...input, request_id })
    setRetry(null); await refresh(project)
  }
  return <section className="space-y-3 border-t border-outline pt-4"><h2 className="text-lg font-semibold">Project Git operations</h2>
    <p>Local clean-tree branch operations and initialized submodules only. Submodule updates use cached pinned revisions. No fetch, clone, push or commit is performed.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <label htmlFor="git-project" className="grid gap-1">Git project<select aria-label="Git project" id="git-project" disabled={busy} value={project} onChange={e => choose(e.target.value)}><option value="">Select a registered project</option>{projects.map(p => <option key={p.project.id} value={p.project.id}>{p.project.name}</option>)}</select></label>
    <Button disabled={busy || !project} onClick={() => void act(() => refresh(project))}>Inspect Git project</Button>
    {state && <><p>Current branch: {state.branch || 'Detached HEAD'} · {state.dirty ? 'Dirty' : 'Clean'}</p><p className="break-all">Revision: {state.head}</p><pre className="overflow-auto whitespace-pre-wrap">{state.status}</pre>
      <label htmlFor="git-branch" className="grid gap-1">Local branch name<input id="git-branch" value={branch} onChange={e => setBranch(e.target.value)} /></label>
      <div className="flex flex-wrap gap-2"><Button disabled={busy || state.dirty || !branch} onClick={() => void act(() => mutate('create_branch', branch))}>Create and switch branch</Button><Button disabled={busy || state.dirty || !branch} onClick={() => void act(() => mutate('switch_branch', branch))}>Switch existing branch</Button></div>
      <ul>{state.submodules.map(sub => <li key={sub.path} className="space-y-1"><p>{sub.path} · {sub.initialized ? sub.dirty ? 'Dirty' : 'Initialized' : 'Uninitialized'}</p><p className="break-all">Pinned: {sub.expected_head}</p><Button disabled={busy || state.dirty || !sub.initialized || sub.dirty} onClick={() => void act(() => mutate('submodule_update', sub.path))}>Update {sub.path} to pinned revision</Button></li>)}</ul>
      {state.submodules.length === 0 && <p>No registered submodules.</p>}
      <ul aria-label="Git operation history">{history.map(row => <li key={row.id}>{row.operation}: {row.target} · {row.status}</li>)}</ul>
    </>}
  </section>
}
