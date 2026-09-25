import { useEffect, useMemo, useState } from 'react'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

interface Connection { id: string; kind: 'jira' | 'datadog' | 'github'; label: string; endpoint: string; credential_name: string; aux_credential_name: string; username: string; revision: number }
interface Run { id: string; connection_id: string; status: string; revision: number; request: { operation: string; method: string; path: string; params: Record<string, unknown>; body: unknown; mutates: boolean }; error: string | null }
interface View { connections: Connection[]; runs: Run[] }
const defaults = { jira: 'https://example.atlassian.net', datadog: 'https://api.datadoghq.com', github: 'https://api.github.com' }

export default function IntegrationApps({ baseUrl = '' }: { baseUrl?: string }) {
  const root = `${baseUrl}/api/capabilities/platform/integration-apps`
  const [view, setView] = useState<View>({ connections: [], runs: [] })
  const [kind, setKind] = useState<keyof typeof defaults>('jira')
  const [label, setLabel] = useState('')
  const [endpoint, setEndpoint] = useState(defaults.jira)
  const [credential, setCredential] = useState('')
  const [secondary, setSecondary] = useState('')
  const [username, setUsername] = useState('')
  const [selected, setSelected] = useState('')
  const [operation, setOperation] = useState('jira_auth')
  const [input, setInput] = useState('{}')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const selectedConnection = useMemo(() => view.connections.find(row => row.id === selected), [selected, view.connections])
  const load = async () => { setView(await readJson<View>(await gatewayRequest(root))) }
  useEffect(() => { void load().catch(reason => setError(String(reason))) }, [root])
  const save = async () => {
    setBusy(true); setError('')
    try {
      await readJson(await gatewayRequest(`${root}/connections`, 'POST', { kind, label, endpoint, credential_name: credential, aux_credential_name: secondary, username }))
      setLabel(''); await load()
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const prepare = async () => {
    setBusy(true); setError('')
    try {
      const body = JSON.parse(input) as Record<string, unknown>
      await readJson(await gatewayRequest(`${root}/connections/${selected}/prepare`, 'POST', { request_id: crypto.randomUUID(), operation, input: body }))
      await load()
    } catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const execute = async (run: Run) => {
    setBusy(true); setError('')
    try { await readJson(await gatewayRequest(`${root}/runs/${run.id}/execute`, 'POST', { revision: run.revision, confirm: true })); await load() }
    catch (reason) { setError(String(reason)) } finally { setBusy(false) }
  }
  const changeKind = (value: keyof typeof defaults) => { setKind(value); setEndpoint(defaults[value]); setOperation(`${value}_auth`) }
  return <section aria-label="Integration applications" className="grid gap-l">
    <h2 data-type="title-m">Jira, Datadog and GitHub</h2>
    <p>Credentials are named references. Every remote action is prepared for owner review before an approved execution.</p>
    {error && <p role="alert">{error}</p>}
    <fieldset className="grid gap-m rounded-lg border border-outline-variant/25 bg-surface-container p-l" disabled={busy}><legend data-type="headline-s" className="px-s">Add connection</legend>
      <label className="grid gap-xs text-sm">Provider<select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration provider" value={kind} onChange={event => changeKind(event.target.value as keyof typeof defaults)}><option value="jira">Jira</option><option value="datadog">Datadog</option><option value="github">GitHub</option></select></label>
      <label className="grid gap-xs text-sm">Label<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration label" value={label} onChange={event => setLabel(event.target.value)} /></label>
      <label className="grid gap-xs text-sm">API origin<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration API origin" value={endpoint} onChange={event => setEndpoint(event.target.value)} /></label>
      <label className="grid gap-xs text-sm">Credential name<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration credential name" value={credential} onChange={event => setCredential(event.target.value)} /></label>
      {kind === 'jira' && <label className="grid gap-xs text-sm">Jira email<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Jira email" value={username} onChange={event => setUsername(event.target.value)} /></label>}
      {kind === 'datadog' && <label className="grid gap-xs text-sm">Application credential name<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Datadog application credential name" value={secondary} onChange={event => setSecondary(event.target.value)} /></label>}
      <Button disabled={!label.trim() || !credential.trim() || (kind === 'jira' && !username.trim()) || (kind === 'datadog' && !secondary.trim())} onClick={() => void save()}>Save connection</Button>
    </fieldset>
    <fieldset className="grid gap-m rounded-lg border border-outline-variant/25 bg-surface-container p-l" disabled={busy || !view.connections.length}><legend data-type="headline-s" className="px-s">Prepare request</legend>
      <label className="grid gap-xs text-sm">Connection<select className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration connection" value={selected} onChange={event => { setSelected(event.target.value); const row = view.connections.find(item => item.id === event.target.value); if (row) setOperation(`${row.kind}_auth`) }}><option value="">Select connection</option>{view.connections.map(row => <option key={row.id} value={row.id}>{row.label} · {row.kind}</option>)}</select></label>
      <label className="grid gap-xs text-sm">Operation<input className="min-h-10 w-full rounded-md border border-outline-variant/30 bg-surface-container px-m text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration operation" value={operation} onChange={event => setOperation(event.target.value)} /></label>
      <label className="grid gap-xs text-sm">Operation input<textarea className="min-h-24 w-full resize-y rounded-md border border-outline-variant/30 bg-surface-container px-m py-s text-sm text-on-surface outline-none focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" aria-label="Integration operation input" value={input} onChange={event => setInput(event.target.value)} /></label>
      <Button disabled={!selectedConnection} onClick={() => void prepare()}>Prepare for review</Button>
    </fieldset>
    <div>{view.runs.map(run => <article className="grid gap-s rounded-lg border border-outline-variant/20 bg-surface-container p-l" key={run.id} aria-label={`Integration run ${run.id}`}>
      <h3 data-type="headline-s">{run.request.operation}</h3><p role="status">{run.status} · {run.request.method} {run.request.path}</p>
      <pre>{JSON.stringify({ params: run.request.params, body: run.request.body }, null, 2)}</pre>
      {run.request.mutates && <p>Remote mutation: owner approval is required.</p>}{run.error && <p>{run.error}</p>}
      <Button disabled={busy || run.status !== 'prepared'} disabledReason="Only a prepared request can be executed" onClick={() => void execute(run)}>Approve and execute</Button>
    </article>)}</div>
  </section>
}
