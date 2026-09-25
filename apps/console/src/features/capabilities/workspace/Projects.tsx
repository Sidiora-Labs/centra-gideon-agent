import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Detection = { workspace: string; types: string[]; commands: Record<string, string>; ports: number[]; has_git: boolean; source_files: string[] }
type Project = { project: { id: string; name: string; workspace_dir: string }; detection: Detection }
type Template = { id: string; name: string; available: boolean; command: string }
const base = '/api/capabilities/workspace/projects'
export default function Projects() {
  const [rows, setRows] = useState<Project[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [name, setName] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [parent, setParent] = useState('')
  const [directory, setDirectory] = useState('')
  const [template, setTemplate] = useState('python-http')
  const [detection, setDetection] = useState<Detection | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [retry, setRetry] = useState<{ body: string; id: string } | null>(null)
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('project') || '')
  useEffect(() => {
    let active = true
    Promise.all([requestJson<Project[]>(base), requestJson<Template[]>(`${base}/templates`)]).then(([projects, available]) => { if (active) { setRows(projects); setTemplates(available); setLoaded(true) } }).catch(e => { if (active) setError(String(e)) })
    const changed = () => setSelected(new URLSearchParams(location.hash.split('?')[1] || '').get('project') || '')
    addEventListener('hashchange', changed)
    return () => { active = false; removeEventListener('hashchange', changed) }
  }, [])
  async function act(work: () => Promise<void>) {
    if (busy) return
    setBusy(true); setError('')
    try { await work() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  function choose(id: string) {
    const query = new URLSearchParams(location.hash.split('?')[1] || '')
    query.set('project', id)
    location.hash = `/capabilities/workspace?${query}`
    setSelected(id)
  }
  async function write(kind: 'register' | 'scaffold') {
    const input = kind === 'register' ? { name, workspace } : { name, parent, directory, template }
    const body = JSON.stringify({ kind, ...input }), request_id = retry?.body === body ? retry.id : crypto.randomUUID()
    setRetry({ body, id: request_id })
    const result = await requestJson<Project>(kind === 'register' ? base : `${base}/scaffold`, 'POST', { ...input, request_id })
    setRows(old => [result, ...old.filter(x => x.project.id !== result.project.id)]); choose(result.project.id); setRetry(null)
  }
  const row = rows.find(item => item.project.id === selected)
  return <section className="space-y-3 border-t border-outline pt-4"><h2 className="text-lg font-semibold">Project discovery and templates</h2>
    <p>Inspect existing project files without running scripts, or create a new local HTTP service. Existing files are never overwritten.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <label htmlFor="project-name" className="grid gap-1">Project display name<input id="project-name" value={name} onChange={e => setName(e.target.value)} className="rounded border border-outline bg-surface p-2" /></label>
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(() => write('register')) }}>
      <label htmlFor="project-existing" className="grid gap-1">Existing project path<input id="project-existing" value={workspace} onChange={e => { setWorkspace(e.target.value); setDetection(null) }} required className="min-w-0 rounded border border-outline bg-surface p-2" /></label>
      <div className="flex flex-wrap gap-2"><Button loading={busy} onClick={() => void act(async () => setDetection(await requestJson<Detection>(`${base}/detect`, 'POST', { workspace })))}>Detect project</Button><Button type="submit" loading={busy}>Register project</Button></div>
    </form>
    {detection && <div aria-label="Project detection"><p>Types: {detection.types.join(', ') || 'Unrecognized'}</p><p>Git: {detection.has_git ? 'Present' : 'Absent'}</p><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(detection.commands, null, 2)}</pre></div>}
    <form className="grid gap-3 sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(() => write('scaffold')) }}>
      {([['Scaffold parent', parent, setParent], ['New directory', directory, setDirectory]] as const).map(([label, value, setter], index) => <label htmlFor={`scaffold-${index}`} key={label} className="grid gap-1">{label}<input id={`scaffold-${index}`} value={value} onChange={e => setter(e.target.value)} required className="min-w-0 rounded border border-outline bg-surface p-2" /></label>)}
      <label htmlFor="scaffold-template" className="grid gap-1">Service template<select id="scaffold-template" value={template} onChange={e => setTemplate(e.target.value)}>{templates.map(item => <option key={item.id} value={item.id}>{item.name}{item.available ? '' : ' (interpreter unavailable)'}</option>)}</select></label>
      <Button type="submit" loading={busy}>Create project</Button>
    </form>
    {!loaded && !error && <p role="status">Loading projects…</p>}
    {loaded && rows.length === 0 && <p>No registered local projects.</p>}
    <ul>{rows.map(item => <li key={item.project.id}><Button variant="ghost" disabled={busy} onClick={() => choose(item.project.id)}>{item.project.name}</Button></li>)}</ul>
    {selected && !row && loaded && <p>Project not found in this view.</p>}
    {row && <article className="space-y-2 break-words"><h3>{row.project.name}</h3><p>{row.project.workspace_dir}</p><p>{row.detection.types.join(', ') || 'Unrecognized project type'}</p><pre className="overflow-auto whitespace-pre-wrap">{JSON.stringify(row.detection.commands, null, 2)}</pre></article>}
  </section>
}
