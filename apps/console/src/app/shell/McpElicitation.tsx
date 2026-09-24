import { useEffect, useState } from 'react'
import { gatewayEvents, type GatewaySocket } from '../../shared/data/socketTransport'

type Field = { type?: string; title?: string; description?: string; enum?: string[]; minimum?: number; maximum?: number }
type Request = { id: string; server: string; message: string; requestedSchema?: { properties?: Record<string, Field>; required?: string[] } }

export function McpElicitationCards({ transport = gatewayEvents() }: { transport?: GatewaySocket }) {
  const [requests, setRequests] = useState<Request[]>([])
  useEffect(() => transport.attach({
    message: ({ type, data }) => {
      if (type === 'mcp_elicitation' && typeof data.id === 'string') {
        const request = data as Request
        setRequests(previous => [...previous.filter(item => item.id !== request.id), request])
      } else if (type === 'mcp_elicitation_withdrawn') {
        setRequests(previous => previous.filter(item => item.id !== data.id))
      }
    },
    status: connected => { if (!connected) setRequests([]) },
  }), [transport])
  if (!requests.length) return null
  return <aside aria-label="MCP requests" className="fixed bottom-4 right-4 z-50 max-h-[80vh] w-96 max-w-[calc(100vw-2rem)] space-y-3 overflow-auto">
    {requests.map(request => <ElicitationCard key={request.id} request={request} onRespond={(action, content) => {
      const sent = transport.send({ type: 'mcp_elicitation_response', id: request.id, action, content })
      if (sent) setRequests(previous => previous.filter(item => item.id !== request.id))
      return sent
    }} />)}
  </aside>
}

function ElicitationCard({ request, onRespond }: {
  request: Request
  onRespond: (action: string, content?: Record<string, unknown>) => boolean
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() => Object.fromEntries(Object.entries(request.requestedSchema?.properties ?? {}).filter(([, field]) => field.type === 'boolean').map(([name]) => [name, false])))
  const [error, setError] = useState('')
  const fields = Object.entries(request.requestedSchema?.properties ?? {})
  const supported = Boolean(request.requestedSchema) && fields.every(([, field]) => ['string', 'number', 'integer', 'boolean'].includes(field.type ?? ''))
  const respond = (action: string) => {
    if (!onRespond(action, action === 'accept' ? values : undefined)) setError('Connection unavailable. Please retry.')
  }
  return <form aria-label={`Request from ${request.server}`} className="rounded-lg border border-outline bg-surface p-4 text-on-surface shadow-lg" onSubmit={event => { event.preventDefault(); respond('accept') }}>
    <h2 className="font-semibold">{request.server} needs your input</h2>
    <p className="my-2">{request.message}</p>
    {supported ? fields.map(([name, field]) => <label className="mb-3 block" key={name}>
      <span>{field.title || name}</span>
      {field.description && <span className="block text-sm text-on-surface-low">{field.description}</span>}
      {field.type === 'boolean' ? <input type="checkbox" checked={values[name] === true} onChange={event => setValues(previous => ({ ...previous, [name]: event.target.checked }))} />
        : field.enum ? <select className="block w-full border bg-surface p-2" required={request.requestedSchema?.required?.includes(name)} value={String(values[name] ?? '')} onChange={event => setValues(previous => ({ ...previous, [name]: event.target.value }))}>
          <option value="">Choose…</option>{field.enum.map(option => <option key={option}>{option}</option>)}
        </select> : <input className="block w-full border bg-surface p-2" type={field.type === 'string' ? 'text' : 'number'} step={field.type === 'integer' ? 1 : 'any'} min={field.minimum} max={field.maximum} required={request.requestedSchema?.required?.includes(name)} value={String(values[name] ?? '')} onChange={event => setValues(previous => ({ ...previous, [name]: field.type === 'string' ? event.target.value : event.target.value === '' ? undefined : Number(event.target.value) }))} />}
    </label>) : <p>This request cannot be completed in this form.</p>}
    {error && <p role="alert">{error}</p>}
    <div className="mt-3 flex gap-3">
      {supported && <button type="submit" className="rounded border px-3 py-2">Submit</button>}
      <button type="button" className="rounded border px-3 py-2" onClick={() => respond('decline')}>Decline</button>
      <button type="button" className="rounded border px-3 py-2" onClick={() => respond('cancel')}>Cancel</button>
    </div>
  </form>
}
