import { useEffect, useState } from 'react'
import { requestJson } from '../../../shared/data/gatewayRequest'
import { Button } from '../../../shared/ui/Button'

type Sync = { state: string; coverage: string; error?: string; messages_seen?: number; conversations_seen?: number }
type Source = { id: string; name: string; owner_email: string; credential_ref: string; revision: number; sync: Sync }
type Message = { provenance_key: string; source_kind: string; conversation_id: string; sender: { name: string }; person_id: string | null; direction: string; created_at: string; deleted_at: string | null; body: string; attachments: { id: string; name: string }[] }
const base = '/api/capabilities/communications/teams/sources'
const selected = () => new URLSearchParams(location.hash.split('?')[1] || '').get('teams_source') || ''
const style = 'block w-full rounded border border-outline bg-surface p-2 text-on-surface'

export function TeamsPanel() {
  const [sources, setSources] = useState<Source[]>([])
  const [id, setId] = useState(selected)
  const [messages, setMessages] = useState<Message[]>([])
  const [form, setForm] = useState({ name: '', owner_email: '', credential_ref: '' })
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)

  useEffect(() => { const update = () => setId(selected()); addEventListener('hashchange', update); return () => removeEventListener('hashchange', update) }, [])
  useEffect(() => {
    let active = true
    setLoading(true)
    Promise.all([requestJson<{ sources: Source[] }>(base), id ? requestJson<{ source: Source; messages: Message[] }>(`${base}/${encodeURIComponent(id)}/messages`) : Promise.resolve(null)])
      .then(([list, detail]) => { if (!active) return; setSources(list.sources); setMessages(detail?.messages || []); if (detail) setForm({ name: detail.source.name, owner_email: detail.source.owner_email, credential_ref: detail.source.credential_ref }) })
      .catch(e => { if (active) setError(String(e.message || e)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [id, version])

  function open(sourceId: string) {
    setError('')
    location.hash = `#/capabilities/communications${sourceId ? `?teams_source=${encodeURIComponent(sourceId)}` : ''}`
    setId(sourceId)
    if (!sourceId) { setForm({ name: '', owner_email: '', credential_ref: '' }); setMessages([]) }
  }
  async function save() {
    setBusy(true); setError('')
    try {
      const current = sources.find(row => row.id === id)
      const response = await requestJson<{ source: Source }>(id ? `${base}/${encodeURIComponent(id)}` : base, id ? 'PUT' : 'POST', { ...form, ...(current ? { revision: current.revision } : {}) })
      open(response.source.id); setVersion(value => value + 1)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  async function sync() {
    setBusy(true); setError('')
    try { await requestJson(`${base}/${encodeURIComponent(id)}/sync`, 'POST', {}); setVersion(value => value + 1) }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); setVersion(value => value + 1) }
    finally { setBusy(false) }
  }
  const current = sources.find(row => row.id === id)
  return <section className="my-6 space-y-3" aria-label="Microsoft Teams history">
    <h2>Microsoft Teams history</h2>
    <p>Read channel and chat history from the Microsoft account named in Gideon's credential store. Sync never sends messages.</p>
    <div className="flex flex-wrap gap-2"><Button onClick={() => open('')}>New Teams source</Button>{sources.map(source => <a key={source.id} className="text-primary underline" href={`#/capabilities/communications?teams_source=${encodeURIComponent(source.id)}`}>{source.name}</a>)}</div>
    {loading && <p role="status">Loading Teams sources…</p>}
    {error && <p role="alert" className="text-danger">{error}</p>}
    <label className="block">Teams source name<input className={style} value={form.name} maxLength={200} onChange={event => setForm({ ...form, name: event.target.value })} /></label>
    <label className="block">Verified Microsoft owner email<input className={style} type="email" value={form.owner_email} onChange={event => setForm({ ...form, owner_email: event.target.value })} /></label>
    <label className="block">Graph credential reference<input className={style} value={form.credential_ref} maxLength={120} onChange={event => setForm({ ...form, credential_ref: event.target.value })} /></label>
    <Button disabled={busy || !form.name || !form.owner_email || !form.credential_ref} onClick={save}>{id ? 'Save Teams source' : 'Create Teams source'}</Button>
    {current && <div className="space-y-2"><p>Sync: {current.sync.state} · coverage: {current.sync.coverage}</p>{current.sync.error && <p>{current.sync.error}</p>}<Button disabled={busy} onClick={sync}>Sync Teams history</Button>
      {messages.length === 0 ? <p>No Teams messages have been acquired.</p> : <ul>{messages.map(message => <li key={message.provenance_key} className="my-2"><time>{message.created_at}</time> · {message.source_kind} · {message.direction}{message.person_id ? ' · linked person' : ' · unlinked sender'}<p>{message.deleted_at ? '[Deleted]' : message.body}</p>{message.attachments.length > 0 && <p>{message.attachments.length} attachment reference(s)</p>}</li>)}</ul>}
    </div>}
  </section>
}
