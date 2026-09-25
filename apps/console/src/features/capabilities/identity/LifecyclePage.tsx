import { useEffect, useState } from 'react'
import { Button } from '../../../shared/ui/Button'
import { PageTitle } from '../../../shared/ui/PageTitle'
import { TopBar } from '../../../shared/ui/TopBar'
import { WorkbenchLayout } from '../../../shared/ui/WorkbenchLayout'
import { gatewayHeaders, readJson } from '../../../shared/data/gatewayRequest'

type View = { policy: { revision: number; enabled: boolean; loop_id: string | null }; loop: { id: string; name: string; status: string } | null; requests: { id: string; preset: string; status: string; reason?: string }[]; provider_readiness: string; scope: string }
export default function LifecyclePage({ endpoint = '/api/capabilities/identity/lifecycle' }: { endpoint?: string }) {
  const [view, setView] = useState<View | null>(null)
  const [loopId, setLoopId] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [preset, setPreset] = useState('reflect')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const call = async <T,>(path = '', body?: unknown): Promise<T> => readJson<T>(await fetch(endpoint + path, { method: body ? 'POST' : 'GET', headers: { ...gatewayHeaders, 'Content-Type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) }))
  const load = async () => { const next = await call<View>(); setView(next); setLoopId(next.policy.loop_id || ''); setEnabled(next.policy.enabled) }
  const perform = async (path?: string, body?: unknown) => { setBusy(true); setError(''); try { if (path) { await call(path, body); setRequestId(crypto.randomUUID()) } await load() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) } }
  useEffect(() => { void perform() }, [endpoint])
  return <WorkbenchLayout topBar={<TopBar keepCornerPadding left={<PageTitle>Agent lifecycle</PageTitle>} />}>
  <section className="mx-auto flex w-full max-w-[56rem] flex-col gap-l px-l py-2xl text-on-surface"><p>Bind an existing autonomous loop to this identity. Enable approves its current route; provider readiness remains unknown until actual runtime evidence exists.</p>
    {error && <p role="alert">{error}</p>}{view && <><p>{view.scope}</p><p>Provider readiness: {view.provider_readiness}</p><p role="status">Loop state: {view.loop?.status || 'unbound'}</p>
      <form className="space-y-m rounded-lg bg-surface-container p-l" onSubmit={e => { e.preventDefault(); void perform('/configure', { loop_id: loopId, enabled, expected_revision: view.policy.revision, request_id: requestId }) }}><label className="block" htmlFor="lifecycle-loop">Existing loop ID</label><input id="lifecycle-loop" required value={loopId} disabled={Boolean(view.policy.loop_id)} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary" onChange={e => setLoopId(e.target.value)} /><label className="block"><input type="checkbox" checked={enabled} className="size-4 shrink-0 accent-primary" onChange={e => setEnabled(e.target.checked)} /> Enable bound identity and approve current route</label><Button type="submit" disabled={busy}>Save lifecycle policy</Button></form>
      <div className="flex gap-2 flex-wrap">{['start', 'resume', 'pause', 'stop', 'cancel_turn'].map(action => <Button key={action} disabled={busy || !view.loop} onClick={() => void perform('/action', { action, expected_revision: view.policy.revision, request_id: requestId })}>{action === 'cancel_turn' ? 'Cancel current turn' : action[0].toUpperCase() + action.slice(1)}</Button>)}</div>
      <p>Pause and stop affect future cycles. Cancel current turn asks the existing engine to cancel the active turn while preserving queued work.</p>
      <form className="space-y-m rounded-lg bg-surface-container p-l" onSubmit={e => { e.preventDefault(); void perform('/requests', { preset, request_id: requestId }) }}><label htmlFor="thinking-preset">Bounded thinking preset</label><select id="thinking-preset" value={preset} className="h-10 w-full min-w-0 rounded-md border border-outline-variant/30 bg-surface-container px-m text-on-surface outline-none transition-colors focus:border-primary/40 focus:ring-2 focus:ring-inset focus:ring-primary appearance-none" onChange={e => setPreset(e.target.value)}><option value="reflect">Reflect on identity anchors</option><option value="review">Review objective progress</option></select><Button type="submit" disabled={busy || !view.policy.enabled}>Request thinking</Button><p>One pending request, at most three per UTC day, at least thirty minutes apart. Delivery does not mean model completion.</p></form>
      <section aria-label="Thinking receipts">{view.requests.map(row => <article key={row.id}><p>{row.preset}: {row.status}{row.reason ? ' · ' + row.reason : ''}</p>{row.status === 'pending' && <Button disabled={busy} onClick={() => void perform('/dispatch', { id: row.id })}>Dispatch thinking request</Button>}</article>)}</section>
    </>}
  </section></WorkbenchLayout>
}
