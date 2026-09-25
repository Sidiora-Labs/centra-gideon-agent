import ProviderTerminals from './ProviderTerminals'
import Storage from './Storage'
import ExternalTerminals from './ExternalTerminals'
import Desktops from './Desktops'
import Git from './Git'
import { useEffect, useState } from 'react'
import Processes from './Processes'
import Ports from './Ports'
import Projects from './Projects'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
import { AreaNavigation } from '../AreaNavigation'
import { Cable, FolderGit2, FolderKanban, GitBranch, HardDrive, Monitor, Save, ServerCog, TerminalSquare } from 'lucide-react'

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
  const [view, setView] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('view') || 'contexts')
  useEffect(() => {
    let active = true
    requestJson<Snapshot[]>(base).then(r => { if (active) { setRows(r); setLoaded(true) } }).catch(e => { if (active) setError(String(e)) })
    const changed = () => { const query = new URLSearchParams(location.hash.split('?')[1] || ''); setSelected(query.get('snapshot') || ''); setView(query.get('view') || 'contexts'); setComparison(null) }
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
  const contexts = <section className="mx-auto w-full space-y-l px-l py-l text-on-surface" style={{ maxWidth: 'var(--content-width)' }}>
    <header><h2 data-type="title-m">Workspace contexts</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-low">Save your branch, terminals and tasks. Comparing a context never changes files or restarts sessions.</p></header>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <Surface className="p-l"><form className="grid gap-m sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(save) }}>
      {([['Project ID', project, setProject], ['Workspace path', workspace, setWorkspace], ['Terminal IDs (comma separated)', terminals, setTerminals], ['Task IDs (comma separated)', tasks, setTasks]] as const).map(([label, value, update], index) => <Field key={label} label={label}><TextInput id={`context-${index}`} value={value} onChange={update} required={index < 2}/></Field>)}
      <Button type="submit" loading={busy}>Save context</Button>
    </form></Surface>
    {!loaded && !error && <p role="status">Loading contexts…</p>}
    {loaded && rows.length === 0 && <p>No saved contexts.</p>}
    <ul className="space-y-s">{rows.map(item => <li key={item.id}><Button className="w-full justify-start" variant={selected === item.id ? 'tonal' : 'secondary'} onClick={() => choose(item.id)}>{item.project_id} · {item.branch || 'Detached'} · {item.captured_at}</Button></li>)}</ul>
    {rows.length >= 100 && <Button loading={busy} onClick={() => void act(async () => { const more = await requestJson<Snapshot[]>(`${base}?offset=${rows.length}`); setRows(old => [...old, ...more]); if (!more.length) setError('No more saved contexts.') })}>Load older contexts</Button>}
    {selected && !row && loaded && <p>Context not found in this page.</p>}
    {row && <Surface className="space-y-m break-words p-l">
      <h3 data-type="title-m">{row.project_id}</h3><p className="text-on-surface-low">{row.workspace}</p><p>Saved branch: {row.branch || 'Detached'} · {row.dirty ? 'Uncommitted changes' : 'Clean'}</p>
      <div className="flex flex-wrap gap-2"><Button loading={busy} onClick={() => void act(async () => { setComparison(await requestJson<Comparison>(`${base}/${row.id}/reconcile`, 'POST', {})) })}>Compare with live workspace</Button>
      <Button variant="danger" loading={busy} onClick={() => void act(async () => { await requestJson(`${base}/${row.id}?revision=${row.revision}`, 'DELETE'); setRows(old => old.filter(x => x.id !== row.id)); choose('') })}>Delete saved context</Button></div>
      {comparison && <div aria-live="polite"><p>Live branch: {comparison.live.branch || 'Detached'} · {comparison.branch_matches === null ? 'No saved branch' : comparison.branch_matches ? 'Branch matches' : 'Branch changed'} · {comparison.live.dirty ? 'Uncommitted changes' : 'Clean'}</p>
        {(['surviving_terminal_ids', 'missing_terminal_ids', 'surviving_task_ids', 'missing_task_ids'] as const).map(key => <p key={key}>{key.replaceAll('_', ' ')}: {comparison[key].join(', ') || 'None'}</p>)}</div>}
    </Surface>}
  </section>
  const content = view === 'storage' ? <Storage/> : view === 'processes' ? <Processes/> : view === 'ports' ? <Ports/> : view === 'projects' ? <Projects/> : view === 'git' ? <Git/> : view === 'desktops' ? <Desktops/> : view === 'external' ? <ExternalTerminals/> : view === 'provider' ? <ProviderTerminals/> : contexts
  const items = [
    { id: 'contexts', label: 'Saved contexts', icon: Save }, { id: 'processes', label: 'Processes', icon: ServerCog },
    { id: 'ports', label: 'Ports', icon: Cable }, { id: 'projects', label: 'Projects', icon: FolderKanban },
    { id: 'git', label: 'Git', icon: GitBranch }, { id: 'desktops', label: 'Desktops', icon: Monitor },
    { id: 'external', label: 'Native terminals', icon: TerminalSquare }, { id: 'provider', label: 'Provider terminals', icon: FolderGit2 },
    { id: 'storage', label: 'Storage', icon: HardDrive },
  ]
  return <AreaNavigation label="Workspace tools" items={items} active={view} onChange={id => { location.hash = id === 'contexts' ? '/capabilities/workspace' : `/capabilities/workspace?view=${id}` }}>{content}</AreaNavigation>
}
