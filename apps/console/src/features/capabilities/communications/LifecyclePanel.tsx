import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
const base = '/api/capabilities/communications/platform-assignments'
type Account = { id: string; platform: string; handle: string; revision: number }
type Assignment = { id: string; account_id: string; account_revision: number; agent_id: string; state: string; revision: number; usable?: boolean; agent_available?: boolean; account_changed?: boolean }
type History = Assignment & { reason: string; at: string }
export function LifecyclePanel() {
  const [agents, setAgents] = useState<{ id: string }[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [rows, setRows] = useState<Assignment[]>([])
  const [agentId, setAgentId] = useState('')
  const [accountId, setAccountId] = useState('')
  const [reason, setReason] = useState('')
  const [id, setId] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('platform_assignment') || '')
  const [history, setHistory] = useState<History[]>([])
  const [key, setKey] = useState(() => crypto.randomUUID())
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const selected = rows.find(row => row.id === id)
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  const reload = async () => { const [configured, registered, assigned] = await Promise.all([requestJson<{ agents: { id: string }[] }>(base + '/agents'), requestJson<{ accounts: Account[] }>('/api/capabilities/communications/social/accounts'), requestJson<{ assignments: Assignment[] }>(base)]); setAgents(configured.agents); setAccounts(registered.accounts); setRows(assigned.assignments) }
  const select = (value: string) => { setId(value); setHistory([]); const params = new URLSearchParams(location.hash.split('?')[1] || ''); params.set('platform_assignment', value); location.hash = '#/capabilities/communications?' + params.toString() }
  useEffect(() => { void run(reload) }, [])
  const change = async (state: string) => { if (!selected) return; const account = accounts.find(row => row.id === selected.account_id); await requestJson(base + '/' + selected.id, 'PUT', { revision: selected.revision, account_revision: account?.revision || selected.account_revision, state, reason }); setReason(''); await reload(); setHistory([]) }
  return <section aria-label="Agent platform assignments" className="space-y-3 rounded border p-4"><h2>Agent platform assignments</h2><p>Local assignments track ownership and readiness. They do not create external accounts, grant tool permissions, or revoke external credentials. Existing tool approval remains authoritative.</p>{error && <p role="alert">{error}</p>}<button disabled={busy} onClick={() => void run(reload)}>Refresh platform assignments</button>
    <form onSubmit={e => { e.preventDefault(); void run(async () => { const account = accounts.find(row => row.id === accountId); const result = await requestJson<{ assignment: Assignment }>(base, 'POST', { account_id: accountId, account_revision: account?.revision, agent_id: agentId, reason, request_key: key }); await reload(); select(result.assignment.id); setKey(crypto.randomUUID()); setReason('') }) }}><label>Configured agent<select required value={agentId} onChange={e => setAgentId(e.target.value)}><option value="">Select configured agent</option>{agents.map(agent => <option key={agent.id}>{agent.id}</option>)}</select></label><label>Registered platform account<select required value={accountId} onChange={e => setAccountId(e.target.value)}><option value="">Select registered account</option>{accounts.map(account => <option value={account.id} key={account.id}>{account.platform}: {account.handle}</option>)}</select></label><label>Assignment reason<input required value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={busy || !agentId || !accountId || !reason}>Request platform assignment</button></form>
    {agents.length === 0 && <p>No configured agents. Create an agent in the existing Agents settings.</p>}<label>Platform assignment<select value={id} onChange={e => select(e.target.value)}><option value="">Select assignment</option>{rows.map(row => <option value={row.id} key={row.id}>{row.agent_id}: {row.state} ({row.id.slice(0, 8)})</option>)}</select></label>
    {selected && <article><p>Assignment: {selected.state}; revision {selected.revision}</p><p>Ready locally: {selected.usable ? 'yes' : 'no'}</p>{!selected.agent_available && <p>Configured agent is unavailable.</p>}{selected.account_changed && <p>Account registration changed. Refresh and explicitly activate against its current revision.</p>}{selected.state !== 'revoked' && <>{['active', 'paused', 'revoked'].map(state => <button key={state} disabled={busy || !reason || selected.state === 'requested' && state === 'paused'} onClick={() => void run(() => change(state))}>{state === 'active' ? 'Activate assignment' : state === 'paused' ? 'Pause assignment' : 'Revoke local assignment'}</button>)}</>}<button disabled={busy} onClick={() => void run(async () => setHistory((await requestJson<{ history: History[] }>(`${base}/${selected.id}/history`)).history))}>Review assignment history</button></article>}
    {history.map(item => <p key={item.revision}>{item.state}: {item.reason} — revision {item.revision}</p>)}
  </section>
}
