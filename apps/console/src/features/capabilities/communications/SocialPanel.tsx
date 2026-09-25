import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
const base = '/api/capabilities/communications/social/accounts'
const empty = { platform: 'x', handle: '', label: '', profile_url: '', credential_ref: '', person_id: '', status: 'active', notes: '' }
type Account = Omit<typeof empty, 'person_id'> & { person_id: string | null; id: string; revision: number }
type History = { event: string; at: string; account: Account }
export function SocialPanel() {
  const [rows, setRows] = useState<Account[]>([])
  const [people, setPeople] = useState<{ id: string; name: string }[]>([])
  const [id, setId] = useState(() => new URLSearchParams(location.hash.split('?')[1] || '').get('social_account') || '')
  const [form, setForm] = useState(empty)
  const [editing, setEditing] = useState<Account | null>(null)
  const [history, setHistory] = useState<History[]>([])
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState(() => crypto.randomUUID())
  const selected = rows.find(row => row.id === id)
  const reload = async () => setRows((await requestJson<{ accounts: Account[] }>(base)).accounts)
  const select = (value: string) => { setId(value); setHistory([]); const params = new URLSearchParams(location.hash.split('?')[1] || ''); params.set('social_account', value); location.hash = '#/capabilities/communications?' + params.toString() }
  const run = async (operation: () => Promise<void>) => { setBusy(true); setError(''); try { await operation() } catch (e) { setError(String(e)) } finally { setBusy(false) } }
  useEffect(() => { let active = true; Promise.all([requestJson<{ accounts: Account[] }>(base), requestJson<{ people: { id: string; name: string }[] }>('/api/capabilities/communications/people')]).then(([accounts, persons]) => { if (active) { setRows(accounts.accounts); setPeople(persons.people) } }).catch(e => { if (active) setError(String(e)) }); return () => { active = false } }, [])
  return <section aria-label="Social account registry" className="space-y-3 rounded border p-4"><h2>Social account registry</h2><p>Local registry only. Profile links and credential references do not verify external account ownership or connectivity.</p>{error && <p role="alert">{error}</p>}
    <form onSubmit={e => { e.preventDefault(); void run(async () => { const values = { ...form, label: form.label || form.handle, person_id: form.person_id || null }; const result = await requestJson<{ account: Account }>(base + (editing ? '/' + editing.id : ''), editing ? 'PUT' : 'POST', { ...values, ...(editing ? { revision: editing.revision } : { request_key: key }) }); await reload(); select(result.account.id); setEditing(null); setForm(empty); setKey(crypto.randomUUID()) }) }}>
      <label>Social platform<select value={form.platform} onChange={e => setForm({ ...form, platform: e.target.value })}>{['x', 'stackernews', 'github', 'mastodon', 'bluesky', 'linkedin', 'instagram', 'other'].map(platform => <option key={platform}>{platform}</option>)}</select></label>
      {(['handle', 'label', 'profile_url', 'credential_ref', 'notes'] as const).map(field => <label key={field}>Social {field}<input required={field === 'handle'} value={form[field]} onChange={e => setForm({ ...form, [field]: e.target.value })} /></label>)}
      <label>Linked person<select value={form.person_id} onChange={e => setForm({ ...form, person_id: e.target.value })}><option value="">No linked person</option>{people.map(person => <option value={person.id} key={person.id}>{person.name}</option>)}</select></label>
      <label>Registration status<select value={form.status} onChange={e => setForm({ ...form, status: e.target.value })}>{['active', 'paused', 'archived'].map(status => <option key={status}>{status}</option>)}</select></label><button disabled={busy}>{editing ? 'Save registration' : 'Register social account'}</button>{editing && <button type="button" onClick={() => { setEditing(null); setForm(empty) }}>Cancel registration edit</button>}
    </form>
    <button disabled={busy} onClick={() => void run(reload)}>Refresh registrations</button><label>Social account<select value={id} onChange={e => select(e.target.value)}><option value="">Select registration</option>{rows.map(row => <option value={row.id} key={row.id}>{row.platform}: {row.handle}</option>)}</select></label>
    {selected && <article><h3>{selected.label}</h3><p>Registration: {selected.status}; revision {selected.revision}</p>{selected.profile_url && <a href={selected.profile_url} target="_blank" rel="noopener noreferrer">Open profile</a>}<p>{selected.notes}</p><button disabled={busy} onClick={() => { setEditing(selected); setForm({ platform: selected.platform, handle: selected.handle, label: selected.label, profile_url: selected.profile_url, credential_ref: selected.credential_ref, person_id: selected.person_id || '', status: selected.status, notes: selected.notes }) }}>Edit registration</button><button disabled={busy} onClick={() => void run(async () => setHistory((await requestJson<{ history: History[] }>(`${base}/${id}/history`)).history))}>Review registration history</button><button disabled={busy} onClick={() => void run(async () => { await requestJson(`${base}/${id}/remove`, 'POST', { revision: selected.revision }); await reload(); select(''); setEditing(null); setForm(empty) })}>Remove local registration</button></article>}
    {history.map((item, index) => <p key={index}>{item.event}: revision {item.account.revision} — {item.at}</p>)}
  </section>
}
