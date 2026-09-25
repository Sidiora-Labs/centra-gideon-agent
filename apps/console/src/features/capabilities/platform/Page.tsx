import { useEffect, useState } from 'react'
import { ArchiveRestore, Blocks, Bot, Boxes, Braces, Gauge, Network, Share2, Wrench } from 'lucide-react'
import InferenceHost from './InferenceHost'
import Insights from './Insights'
import IntegrationApps from './IntegrationApps'
import MediaSharing from './MediaSharing'
import Migration from './Migration'
import Peers from './Peers'
import Quotas from './Quotas'
import RemoteMedia from './RemoteMedia'
import { RemoteSessions } from './RemoteSessions'
import Replication from './Replication'
import Accounting from './Accounting'
import Composition from './Composition'
import Forecast from './Forecast'
import Cadence from './Cadence'
import PrScreening from './PrScreening'
import Maintenance from './Maintenance'
import Harnesses from './Harnesses'
import Comparisons from './Comparisons'
import References from './References'
import Ownership from './Ownership'
import Gsd from './Gsd'
import { DomainReadiness } from './DomainReadiness'
import { ProviderConnections } from './ProviderConnections'
import { AreaNavigation } from '../AreaNavigation'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'
import { EmptyState, ListSkeleton, LoadError } from '../../../shared/ui/ListScaffold'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { Field, Select, TextInput } from '../../../shared/ui/forms'
import { Surface } from '../../../shared/ui/Surface'
import { TopBar } from '../../../shared/ui/TopBar'

export interface ApiRoute { method: string; path: string; name: string | null; handler: string | null; schema: { status: string }; executable: boolean }
export interface Catalog { version: number; routes: ApiRoute[]; events: { name: string; transport: string; payload_keys: string[]; schema: { status: string } }[]; total: number; offset: number; limit: number; next_offset: number | null }
type View = 'providers' | 'integrations' | 'operations' | 'evidence' | 'network' | 'sharing' | 'migration' | 'api'
const endpoint = '/api/capabilities/platform/catalog'
const safeReads = new Set([endpoint, '/api/prompts/syntax'])
const views = [
  { id: 'providers', label: 'Providers', icon: Bot, group: 'Connect' }, { id: 'integrations', label: 'Integrations', icon: Blocks, group: 'Connect' },
  { id: 'operations', label: 'Operations', icon: Wrench, group: 'Operate' }, { id: 'evidence', label: 'Evidence', icon: Gauge, group: 'Operate' },
  { id: 'network', label: 'Network', icon: Network, group: 'Exchange' }, { id: 'sharing', label: 'Sharing', icon: Share2, group: 'Exchange' },
  { id: 'migration', label: 'Archive migration', icon: ArchiveRestore, group: 'Data' }, { id: 'api', label: 'API catalog', icon: Braces, group: 'Data' },
] as const
const message = (error: unknown) => error instanceof Error ? error.message : String(error)
const isView = (value: string): value is View => views.some(view => view.id === value)
function Workspace({ children }: { children: React.ReactNode }) { return <div className="mx-auto grid w-full min-w-0 gap-l px-l py-l" style={{ maxWidth: 'var(--content-width)' }}>{children}</div> }

function PlatformExplorer({ baseUrl }: { baseUrl: string }) {
  const { query: params, setQuery } = useHashRoute('capabilities')
  const [catalog, setCatalog] = useState<Catalog>(), [error, setError] = useState(''), [response, setResponse] = useState('')
  const [busy, setBusy] = useState(false), [reload, setReload] = useState(0)
  const offset = Number(params.offset || 0), query = params.q || '', method = params.method || '', selection = params.route || ''
  const update = (key: string, value: string) => setQuery({ [key]: value })
  useEffect(() => { let current = true; setCatalog(undefined); setError(''); setResponse(''); gatewayRequest(`${baseUrl}${endpoint}?offset=${offset}`).then(readJson<Catalog>).then(value => { if (current) setCatalog(value) }).catch(reason => { if (current) setError(message(reason)) }); return () => { current = false } }, [baseUrl, offset, reload])
  const selected = catalog?.routes.find(route => `${route.method} ${route.path}` === selection)
  const executable = selected?.executable && selected.method === 'GET' && safeReads.has(selected.path)
  const execute = async () => {
    if (!selected || !executable || busy) return
    setBusy(true); setResponse('')
    try {
      const result = await gatewayRequest(`${baseUrl}${selected.path}`), reader = result.body?.getReader(), decoder = new TextDecoder()
      let output = '', bytes = 0
      if (reader) { try { while (bytes < 65536) { const chunk = await reader.read(); if (chunk.done) break; bytes += chunk.value.length; output += decoder.decode(chunk.value.subarray(0, Math.max(0, 65536 - (bytes - chunk.value.length))), { stream: true }) } } finally { await reader.cancel() } }
      setResponse(`HTTP ${result.status}\n${output}${bytes >= 65536 ? '\nResponse truncated at 64 KiB.' : ''}`)
    } catch (reason) { setResponse(message(reason)) } finally { setBusy(false) }
  }
  const rows = catalog?.routes.filter(route => (!method || route.method === method) && `${route.path} ${route.name || ''} ${route.handler || ''}`.toLowerCase().includes(query.toLowerCase())) || []
  return <Workspace><section aria-label="API catalog" className="grid gap-l">
    <div><h2 data-type="title-m" className="text-on-surface">API catalog</h2><p data-type="body-s" className="mt-1 max-w-[48rem] text-on-surface-low">Find registered routes, inspect their declared schema, and read explicitly safe diagnostics.</p></div>
    <Surface className="flex flex-wrap items-end gap-m p-l"><div className="min-w-[min(100%,18rem)] flex-1"><Field label="Search routes"><TextInput value={query} maxLength={120} onChange={value => update('q', value)} /></Field></div><div className="min-w-32"><Field label="Method"><Select value={method} onChange={value => update('method', value)} options={['', 'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'].map(value => ({ value, label: value || 'All' }))} /></Field></div><Button variant="secondary" loading={busy} onClick={() => setReload(value => value + 1)}>Refresh</Button></Surface>
    {catalog && <p data-type="body-s" className="text-on-surface-low">{catalog.total} registered routes; page starts at {catalog.offset + 1}.</p>}
    {error ? <LoadError what="API catalog" error={error} onRetry={() => setReload(value => value + 1)} /> : !catalog ? <ListSkeleton rows={5} what="routes" /> : rows.length === 0 ? <EmptyState icon={Braces} title="No matching routes" hint="Try a different path or method." /> : <div className="grid gap-s">{rows.map(route => { const key = `${route.method} ${route.path}`, active = selection === key; return <article key={key} className="rounded-lg border border-outline-variant/25 bg-surface-container"><button type="button" aria-label={key} onClick={() => { update('route', key); setResponse('') }} aria-expanded={active} className="flex min-h-12 w-full min-w-0 items-center gap-m px-l text-left"><span className="w-16 shrink-0 font-mono text-xs text-primary">{route.method}</span><span className="min-w-0 flex-1 break-all font-mono text-sm text-on-surface">{route.path}</span><span className="hidden text-xs text-on-surface-low md:block">{route.schema.status}</span></button>{active && <div role="region" aria-label="Route detail" className="grid gap-m border-t border-outline-variant/25 px-l py-m"><h3 className="sr-only">{key}</h3><p className="text-sm text-on-surface-low">{route.name || 'Unnamed'} · {route.handler || 'Unknown'} · Schema: {route.schema.status}</p><div><Button size="sm" disabled={!executable} disabledReason="Execution is limited to explicitly harmless read endpoints" loading={busy} onClick={() => void execute()}>Execute read</Button></div>{response && <pre aria-label="HTTP response" className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface px-m py-m text-xs">{response}</pre>}</div>}</article> })}</div>}
    {catalog && <div className="flex gap-s"><Button variant="secondary" disabled={offset === 0} disabledReason="First page" onClick={() => update('offset', String(Math.max(0, offset - catalog.limit)))}>Previous</Button><Button variant="secondary" disabled={catalog.next_offset === null} disabledReason="Last page" onClick={() => update('offset', String(catalog.next_offset))}>Next</Button></div>}
    {!!catalog?.events.length && <section className="grid gap-s" aria-label="Declared events"><h3 data-type="headline-s">App events</h3><div className="grid gap-xs sm:grid-cols-2">{catalog.events.map(event => <div key={event.name} className="rounded-lg border border-outline-variant/20 bg-surface-container px-m py-s"><span className="break-all font-mono text-sm">{event.name}</span><span className="ml-2 text-xs text-on-surface-low">{event.transport} · Declared keys: {event.payload_keys.join(', ')} · {event.schema.status} (value types unspecified)</span></div>)}</div></section>}
  </section></Workspace>
}

function ProvidersWorkspace({ baseUrl }: { baseUrl: string }) { const [selected, setSelected] = useState(''); return <Workspace><ProviderConnections selected={selected} onSelect={setSelected} baseUrl={baseUrl} /><InferenceHost baseUrl={baseUrl} /><Harnesses baseUrl={baseUrl} /><Quotas baseUrl={baseUrl} /></Workspace> }
function ActiveWorkspace({ view, baseUrl }: { view: View; baseUrl: string }) {
  if (view === 'providers') return <ProvidersWorkspace baseUrl={baseUrl} />
  if (view === 'integrations') return <Workspace><IntegrationApps baseUrl={baseUrl} /><PrScreening baseUrl={baseUrl} /></Workspace>
  if (view === 'operations') return <Workspace><DomainReadiness baseUrl={baseUrl} /><Maintenance baseUrl={baseUrl} /><Gsd baseUrl={baseUrl} /><Cadence baseUrl={baseUrl} /><Composition baseUrl={baseUrl} /></Workspace>
  if (view === 'evidence') return <Workspace><Comparisons baseUrl={baseUrl} /><Forecast baseUrl={baseUrl} /><Accounting baseUrl={baseUrl} /><Insights baseUrl={baseUrl} /><References baseUrl={baseUrl} /><Ownership baseUrl={baseUrl} /></Workspace>
  if (view === 'network') return <Workspace><Peers baseUrl={baseUrl} /><RemoteSessions baseUrl={baseUrl} /><Replication baseUrl={baseUrl} /></Workspace>
  if (view === 'sharing') return <Workspace><RemoteMedia baseUrl={baseUrl} /><MediaSharing baseUrl={baseUrl} /></Workspace>
  if (view === 'migration') return <Workspace><Migration baseUrl={baseUrl} /></Workspace>
  return <PlatformExplorer baseUrl={baseUrl} />
}

export default function Page({ baseUrl = '' }: { baseUrl?: string }) {
  const { query, setQuery } = useHashRoute('capabilities')
  const view: View = isView(query.view || '') ? query.view as View : (query.route || query.q || query.method || query.offset ? 'api' : 'providers')
  const active = views.find(item => item.id === view) || views[0]
  return <AreaNavigation label="Platform workspaces" items={[...views]} active={view} onChange={id => setQuery({ view: id })}><div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden"><TopBar keepCornerPadding left={<div className="flex min-w-0 items-center gap-s"><Boxes size={18} className="shrink-0 text-on-surface-low" /><PageTitle className="truncate">{active.label}</PageTitle></div>} /><main className="min-h-0 min-w-0 flex-1 overflow-y-auto"><ActiveWorkspace view={view} baseUrl={baseUrl} /></main></div></AreaNavigation>
}
