import Accounting from './Accounting'
import Composition from './Composition'
import Forecast from './Forecast'
import Cadence from './Cadence'
import PrScreening from './PrScreening'
import Maintenance from './Maintenance'
import { useEffect, useState } from 'react'
import Harnesses from './Harnesses'
import Comparisons from './Comparisons'
import References from './References'
import Ownership from './Ownership'
import Gsd from './Gsd'
import { useHashRoute } from '../../../app/shell/useHashRoute'
import { gatewayRequest, readJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

export interface ApiRoute { method: string; path: string; name: string | null; handler: string | null; schema: { status: string }; executable: boolean }
export interface Catalog { version: number; routes: ApiRoute[]; events: { name: string; transport: string; payload_keys: string[]; schema: { status: string } }[]; total: number; offset: number; limit: number; next_offset: number | null }
const endpoint = '/api/capabilities/platform/catalog'
const safeReads = new Set([endpoint, '/api/prompts/syntax'])
const message = (error: unknown) => error instanceof Error ? error.message : String(error)

export default function Page({ baseUrl = '' }: { baseUrl?: string }) {
  const { query: params, setQuery } = useHashRoute('capabilities')
  const [catalog, setCatalog] = useState<Catalog>()
  const [error, setError] = useState('')
  const [response, setResponse] = useState('')
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  const offset = Number(params.offset || 0)
  const query = params.q || ''
  const method = params.method || ''
  const selection = params.route || ''
  const update = (key: string, value: string) => setQuery({ [key]: value })
  useEffect(() => {
    let current = true
    setCatalog(undefined); setError(''); setResponse('')
    gatewayRequest(`${baseUrl}${endpoint}?offset=${offset}`).then(readJson<Catalog>)
      .then(value => { if (current) setCatalog(value) })
      .catch(reason => { if (current) setError(message(reason)) })
    return () => { current = false }
  }, [baseUrl, offset, reload])
  const selected = catalog?.routes.find(route => `${route.method} ${route.path}` === selection)
  const executable = selected?.executable && selected.method === 'GET' && safeReads.has(selected.path)
  const execute = async () => {
    if (!selected || !executable || busy) return
    setBusy(true); setResponse('')
    try {
      const result = await gatewayRequest(`${baseUrl}${selected.path}`)
      const reader = result.body?.getReader()
      let text = ''; let bytes = 0
      const decoder = new TextDecoder()
      if (reader) {
        try {
          while (bytes < 65536) {
            const chunk = await reader.read()
            if (chunk.done) break
            bytes += chunk.value.length
            text += decoder.decode(chunk.value.subarray(0, Math.max(0, 65536 - (bytes - chunk.value.length))), { stream: true })
          }
        } finally { await reader.cancel() }
      }
      setResponse(`HTTP ${result.status}\n${text}${bytes >= 65536 ? '\nResponse truncated at 64 KiB.' : ''}`)
    } catch (reason) { setResponse(message(reason)) }
    finally { setBusy(false) }
  }
  const rows = catalog?.routes.filter(route => (!method || route.method === method) && `${route.path} ${route.name || ''} ${route.handler || ''}`.toLowerCase().includes(query.toLowerCase())) || []
  return <main className="min-w-0 space-y-l p-l text-on-surface">
    <h1 className="text-xl">API explorer</h1>
    <Harnesses baseUrl={baseUrl} />
    <Comparisons baseUrl={baseUrl} />
    <References baseUrl={baseUrl} />
    <Ownership baseUrl={baseUrl} />
    <Accounting baseUrl={baseUrl} /><Composition baseUrl={baseUrl} /><Forecast baseUrl={baseUrl} /><Cadence baseUrl={baseUrl} /><PrScreening baseUrl={baseUrl} /><Maintenance baseUrl={baseUrl} /><Gsd baseUrl={baseUrl} />
    <p>Live registered HTTP routes and declared app events. Unspecified schemas remain unknown.</p>
    <div className="flex flex-wrap gap-m">
      <label>Filter this page <input aria-label="Filter this page" className="bg-surface-high rounded p-s" value={query} onChange={event => update('q', event.target.value)} /></label>
      <label>Method <select aria-label="Method" className="bg-surface-high rounded p-s" value={method} onChange={event => update('method', event.target.value)}>
        <option value="">All</option>{['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'].map(value => <option key={value}>{value}</option>)}
      </select></label>
      <Button onClick={() => setReload(value => value + 1)}>Refresh</Button>
    </div>
    {error && <p role="alert">{error}</p>}
    {!catalog && !error && <p role="status">Loading API catalog…</p>}
    {catalog && <>
      <p>{catalog.total} registered routes; page starts at {catalog.offset + 1}.</p>
      {!rows.length && <p>No routes match this page.</p>}
      <ul className="space-y-s">{rows.map(route => <li key={`${route.method} ${route.path}`}>
        <button className="max-w-full break-all text-left underline" onClick={() => { update('route', `${route.method} ${route.path}`); setResponse('') }}>{route.method} {route.path}</button>
      </li>)}</ul>
      <div className="flex gap-m">
        <Button disabled={offset === 0} disabledReason="First page" onClick={() => update('offset', String(Math.max(0, offset - catalog.limit)))}>Previous</Button>
        <Button disabled={catalog.next_offset === null} disabledReason="Last page" onClick={() => update('offset', String(catalog.next_offset))}>Next</Button>
      </div>
      {selected && <section className="min-w-0 space-y-m" aria-label="Route detail">
        <h2 className="break-all">{selection}</h2>
        <p>Name: {selected.name || 'Unnamed'} · Handler: {selected.handler || 'Unknown'} · Schema: {selected.schema.status}</p>
        <Button disabled={!executable} disabledReason="Execution is limited to explicitly harmless read endpoints" loading={busy} onClick={() => void execute()}>Execute read</Button>
        {response && <pre aria-label="HTTP response" className="max-h-96 overflow-auto whitespace-pre-wrap break-all bg-surface-high p-m">{response}</pre>}
      </section>}
      <section aria-label="Declared events"><h2>App events</h2>
        {catalog.events.map(event => <p key={event.name}>{event.name} · {event.transport} · Declared keys: {event.payload_keys.join(', ')} · Schema: {event.schema.status} (value types unspecified)</p>)}
      </section>
    </>}
  </main>
}
