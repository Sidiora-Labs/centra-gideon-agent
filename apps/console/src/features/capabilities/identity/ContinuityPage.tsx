import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type Line = { text: string; tombstoned: boolean; added_at: string }
type Status = { revision: number; heartbeat_paused: boolean; journal: { sequence: number; action: string; created_at: string }[]; slots: Record<string, Line[]>; context: string; scope: string; provider_readiness: string }
export default function ContinuityPage({ endpoint = '/api/capabilities/identity/continuity' }: { endpoint?: string }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [slot, setSlot] = useState('persona')
  const [text, setText] = useState('')
  const [request, setRequest] = useState(() => crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const call = async <T,>(path = '', body?: unknown, method = 'POST'): Promise<T> => readJson<T>(await fetch(endpoint + path, { method: body ? method : 'GET', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }))
  const load = async () => setStatus(await call<Status>())
  const perform = async (action: () => Promise<void>) => { setBusy(true); setError(''); try { await action() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform(load) }, [endpoint])
  return <section className="p-4 max-w-4xl mx-auto space-y-4 text-on-surface"><h1 className="text-2xl">Agent continuity</h1>
    <p>Bounded persona and self-note memory slots are reused by existing conversation context. Human-removed lines remain tombstoned.</p>
    {error && <p role="alert" className="text-danger">{error}</p>}{!status && <p role="status">Loading continuity…</p>}
    <Button disabled={busy} onClick={() => void perform(load)}>Reload continuity</Button>
    {status && <><section aria-label="Heartbeat control"><h2>Scheduled heartbeat turns</h2><p>{status.heartbeat_paused ? 'Paused' : 'Allowed by policy'}</p><p>{status.scope}</p><p>Provider readiness: {status.provider_readiness}</p>
      <Button disabled={busy} onClick={() => void perform(async () => { await call('', { heartbeat_paused: !status.heartbeat_paused, expected_revision: status.revision, request_id: request }, 'PUT'); setRequest(crypto.randomUUID()); await load() })}>{status.heartbeat_paused ? 'Resume scheduled turns' : 'Pause scheduled turns'}</Button></section>
      <form className="space-y-2" onSubmit={e => { e.preventDefault(); void perform(async () => { await call('/anchors', { slot, text }); setText(''); await load() }) }}>
        <label htmlFor="continuity-slot">Memory slot</label><select id="continuity-slot" value={slot} onChange={e => setSlot(e.target.value)}><option value="persona">Persona</option><option value="self_notes">Self notes</option></select>
        <label className="block" htmlFor="continuity-text">Continuity note</label><textarea className="w-full bg-surface-high p-2" id="continuity-text" required value={text} onChange={e => setText(e.target.value)} />
        <Button type="submit" disabled={busy}>Add continuity note</Button>
      </form>
      {Object.entries(status.slots).map(([name, lines]) => <section key={name} aria-label={name}><h2>{name === 'persona' ? 'Persona' : 'Self notes'}</h2>{!lines.length && <p>No continuity notes.</p>}{lines.map((line, index) => <article key={index} className="py-2"><p>{line.text}{line.tombstoned ? ' · Removed by human' : ''}</p>{!line.tombstoned && <Button variant="secondary" disabled={busy} onClick={() => void perform(async () => { await call('/anchors/remove', { slot: name, text: line.text }); await load() })}>Remove note</Button>}</article>)}</section>)}
      <section aria-label="Continuity context"><h2>Current context</h2><pre className="whitespace-pre-wrap">{status.context || 'No continuity context yet.'}</pre></section>
      <section aria-label="Control journal"><h2>Control journal</h2>{!status.journal.length && <p>No control changes.</p>}{status.journal.map(row => <p key={row.sequence}>{row.sequence} · {row.action} · {row.created_at}</p>)}</section></>}
  </section>
}
