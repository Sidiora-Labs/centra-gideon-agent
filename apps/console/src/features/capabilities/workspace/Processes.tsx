import ProcessLogs from './ProcessLogs'
import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { Field, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'

type Process = { id: string; project_id: string; workspace: string; command: string; status: string; revision: number; exit_code: number | null }
const base = '/api/capabilities/workspace/processes'
export default function Processes() {
  const [rows, setRows] = useState<Process[]>([])
  const [project, setProject] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [command, setCommand] = useState('')
  const [selected, setSelected] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('process') || '')
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [retry, setRetry] = useState<{ body: string; id: string } | null>(null)
  useEffect(() => {
    let active = true
    requestJson<Process[]>(base).then(result => { if (active) { setRows(result); setLoaded(true) } }).catch(e => { if (active) setError(String(e)) })
    const changed = () => { setSelected(new URLSearchParams(location.hash.split('?')[1] || '').get('process') || ''); setText(null) }
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
    query.set('process', id)
    location.hash = `/capabilities/workspace?${query}`
    setSelected(id); setText(null)
  }
  function update(row: Process) { setRows(old => [row, ...old.filter(x => x.id !== row.id)]) }
  async function start() {
    const input = { project_id: project, workspace, command }
    const body = JSON.stringify(input), request_id = retry?.body === body ? retry.id : crypto.randomUUID()
    setRetry({ body, id: request_id })
    const row = await requestJson<Process>(base, 'POST', { ...input, request_id })
    update(row); choose(row.id); setRetry(null)
  }
  const row = rows.find(item => item.id === selected)
  return <section className="mx-auto w-full space-y-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>
    <header><h2 data-type="title-m">Managed processes</h2>
    <p data-type="body-s" className="mt-1 text-on-surface-low">Start a command in an allowed project directory. Stop affects only processes started here.</p></header>
    {error && <p role="alert" className="text-danger">{error}</p>}
    <Surface className="p-l"><form className="grid gap-m sm:grid-cols-2" onSubmit={e => { e.preventDefault(); void act(start) }}>
      {([['Process project ID', project, setProject], ['Process workspace', workspace, setWorkspace], ['Command', command, setCommand]] as const).map(([label, value, setter], index) => <Field key={label} label={label}><TextInput id={`process-${index}`} value={value} onChange={setter} required /></Field>)}
      <Button type="submit" loading={busy}>Start process</Button>
    </form></Surface>
    {!loaded && !error && <p role="status">Loading processes…</p>}
    {loaded && rows.length === 0 && <p>No managed processes.</p>}
    <Button variant="secondary" loading={busy} onClick={() => void act(async () => { setRows(await requestJson<Process[]>(base)); setLoaded(true) })}>Refresh processes</Button>
    <ul className="space-y-s">{rows.map(item => <li key={item.id}><Button className="w-full justify-start" variant={selected === item.id ? 'tonal' : 'secondary'} disabled={busy} onClick={() => choose(item.id)}>{item.project_id} · {item.status}</Button></li>)}</ul>
    {selected && !row && loaded && <p>Process not found in this page.</p>}
    {row && <Surface className="space-y-m break-words p-l"><h3 data-type="title-m">{row.project_id}</h3><p className="text-on-surface-low">{row.workspace}</p><code className="block rounded-md bg-surface p-m">{row.command}</code><p aria-live="polite">Status: {row.status} · Exit: {row.exit_code ?? 'Not exited'}</p>
      <div className="flex flex-wrap gap-2"><Button loading={busy} onClick={() => void act(async () => { setText((await requestJson<{ text: string }>(`${base}/${row.id}/logs`)).text); update(await requestJson<Process>(`${base}/${row.id}`)) })}>Read process logs</Button>
      <Button variant="danger" loading={busy} disabled={!['running', 'starting'].includes(row.status)} onClick={() => void act(async () => { update(await requestJson<Process>(`${base}/${row.id}/stop`, 'POST', { revision: row.revision })) })}>Stop process</Button></div>
      {text !== null && <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-m" aria-label="Process logs">{text || 'No output yet.'}</pre>}
      <ProcessLogs key={row.id} id={row.id} />
    </Surface>}
  </section>
}
